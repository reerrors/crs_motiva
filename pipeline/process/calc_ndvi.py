"""
pipeline/process/calc_ndvi.py

Calcula NDVI medio por segmento, por data, usando cenas Sentinel-2 L2A
via Microsoft Planetary Computer.

Reescrito pra ler cada banda (B04/B08/SCL) UMA VEZ por cena, cobrindo
de uma vez a area combinada de todos os segmentos que aquela cena
cobre -- em vez de uma leitura remota por segmento por cena (a versao
anterior fazia ~2 milhoes de requisicoes HTTP sequenciais pra 592
segmentos x ~1100 cenas x 3 bandas, o que levava horas). Depois da
leitura unica, o NDVI por segmento e calculado em memoria (sem nova
chamada de rede), usando rasterizacao da geometria de cada segmento
sobre o array ja carregado.

Decisoes de projeto (ja combinadas):
- Periodo: ultimo 1 ano (parametrizavel)
- Fonte: Sentinel-2 L2A (Planetary Computer)
- Cada cena e assinada individualmente, na hora de usar -- nao em
  lote no inicio da busca -- porque links assinados expiram (~1h) e
  processar um dataset grande demora mais que isso
- Filtro de nuvem: pre-filtro por %% de nuvem da cena inteira na busca,
  MAIS mascaramento pixel a pixel via banda SCL
- Agregacao: NDVI medio dentro do mesmo "anel" (buffer externo menos
  buffer interno) usado no viability_filter.py -- exclui o pavimento
- Correcao de offset da baseline 04.00+ (Sentinel-2)
- Media ponderada por segmento+data quando cenas adjacentes se
  sobrepoem, e descarte de observacoes com poucos pixels validos
"""

from datetime import datetime, timedelta

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer
import pystac_client
import rasterio
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.warp import transform_geom
from shapely.geometry import shape
from shapely.ops import unary_union

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"

MAX_CLOUD_COVER_CENA = 40  # %% -- pre-filtro na busca, so reduz candidatos
BOA_ADD_OFFSET = 1000  # offset da baseline 04.00+ do Sentinel-2

SCL_CLASSES_VALIDAS = {4, 5}  # 4=vegetacao, 5=solo exposto/nao vegetado

MIN_PIXELS_VALIDOS = 100  # abaixo disso, descarta a observação (pouca confiança)

# tamanho do lote de segmentos processado por leitura remota. Muito
# grande = retângulo de leitura gigante (rodovia é comprida, segmentos
# de uma mesma cena podem estar espalhados por dezenas de km). Muito
# pequeno = volta a ser muitas requisições pequenas (lento por outro
# motivo). 40 é um meio-termo de partida -- ajustável.
CHUNK_SIZE = 40

# mesmos valores do viability_filter.py -- TODO: mover pra um modulo
# compartilhado quando o pipeline crescer
BUFFER_MIN_M = 8.0
BUFFER_MAX_M = 20.0


def _search_sentinel2_items(bbox_wgs84, dias_atras=365, max_cloud=MAX_CLOUD_COVER_CENA):
    # sem assinatura em lote aqui de proposito -- ver docstring do modulo
    catalog = pystac_client.Client.open(STAC_URL)
    data_fim = datetime.utcnow()
    data_inicio = data_fim - timedelta(days=dias_atras)

    search = catalog.search(
        collections=[COLLECTION],
        bbox=bbox_wgs84,
        datetime=f"{data_inicio.isoformat()}Z/{data_fim.isoformat()}Z",
        query={"eo:cloud_cover": {"lt": max_cloud}},
    )
    return list(search.items())


def _ler_bloco(href, geom_wgs84, tentativas=3):
    """
    Le UMA janela do raster remoto, recortada em geom_wgs84 (que pode
    cobrir varios segmentos de uma vez). Retorna (array, transform,
    crs) ou (None, None, None) se falhar ou não intersectar.
    """
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            with rasterio.open(href) as src:
                geom_no_crs = transform_geom(
                    "EPSG:4326", src.crs, geom_wgs84.__geo_interface__
                )
                try:
                    out_image, out_transform = mask(src, [geom_no_crs], crop=True)
                except ValueError:
                    return None, None, None  # não intersecta esse raster
                return out_image[0], out_transform, src.crs
        except rasterio.errors.RasterioIOError as e:
            ultimo_erro = e
            continue  # falha transitória -- tenta de novo

    print(f"[calc_ndvi] Falha ao ler bloco após {tentativas} tentativas: {ultimo_erro}")
    return None, None, None


def _resize_nearest(array, target_shape):
    """Reamostra vizinho mais próximo -- SCL vem em 20m, B04/B08 em 10m."""
    src_rows, src_cols = array.shape
    tgt_rows, tgt_cols = target_shape
    row_idx = np.clip((np.arange(tgt_rows) * src_rows / tgt_rows).astype(int), 0, src_rows - 1)
    col_idx = np.clip((np.arange(tgt_cols) * src_cols / tgt_cols).astype(int), 0, src_cols - 1)
    return array[row_idx][:, col_idx]


def _processar_item(item, chunks_aneis_wgs84):
    """
    Processa UMA cena Sentinel-2, em LOTES de segmentos (não todos de
    uma vez, não um por um). Cada lote gera uma leitura remota que
    cobre só a área daquele lote -- evita tanto o extremo de "1
    retângulo gigante cobrindo segmentos espalhados por dezenas de km"
    quanto o extremo de "1 leitura por segmento" (muitas requisições).
    """
    item = planetary_computer.sign(item)  # assina agora, na hora de usar
    footprint_cena = shape(item.geometry)

    href_red = item.assets["B04"].href
    href_nir = item.assets["B08"].href
    href_scl = item.assets["SCL"].href

    data_cena = item.datetime.date()
    registros = []

    for chunk in chunks_aneis_wgs84:
        segmentos_cobertos = {
            seg_id: anel for seg_id, anel in chunk
            if footprint_cena.intersects(anel)
        }
        if not segmentos_cobertos:
            continue  # esse lote inteiro fica fora da área dessa cena

        geometria_combinada = unary_union(list(segmentos_cobertos.values()))

        red, transform_red, crs_red = _ler_bloco(href_red, geometria_combinada)
        if red is None:
            continue
        nir, _, _ = _ler_bloco(href_nir, geometria_combinada)
        if nir is None:
            continue
        scl, _, _ = _ler_bloco(href_scl, geometria_combinada)
        if scl is None:
            continue

        if scl.shape != red.shape:
            scl = _resize_nearest(scl, red.shape)

        red_f = red.astype("float32")
        nir_f = nir.astype("float32")

        baseline = item.properties.get("s2:processing_baseline", "00.00")
        if baseline >= "04.00":
            red_f = red_f - BOA_ADD_OFFSET
            nir_f = nir_f - BOA_ADD_OFFSET

        mascara_scl_valida = np.isin(scl, list(SCL_CLASSES_VALIDAS))
        denom = nir_f + red_f
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = np.where(denom != 0, (nir_f - red_f) / denom, np.nan)

        for seg_id, anel_wgs84 in segmentos_cobertos.items():
            anel_no_crs = transform_geom(
                "EPSG:4326", crs_red, anel_wgs84.__geo_interface__
            )
            mascara_segmento = geometry_mask(
                [anel_no_crs], out_shape=red.shape, transform=transform_red, invert=True
            )
            mascara_final = mascara_segmento & mascara_scl_valida

            ndvi_validos = ndvi[mascara_final]
            ndvi_validos = ndvi_validos[~np.isnan(ndvi_validos)]
            if len(ndvi_validos) == 0:
                continue

            registros.append(
                {
                    "segment_id": seg_id,
                    "data": data_cena,
                    "ndvi_medio": float(np.mean(ndvi_validos)),
                    "n_pixels_validos": int(len(ndvi_validos)),
                }
            )

    return registros


def calc_ndvi_por_segmento(
    gdf_segments: gpd.GeoDataFrame,
    dias_atras: int = 365,
    buffer_min_m: float = BUFFER_MIN_M,
    buffer_max_m: float = BUFFER_MAX_M,
) -> pd.DataFrame:
    bbox_wgs84 = list(gdf_segments.to_crs(4326).total_bounds)
    items = _search_sentinel2_items(bbox_wgs84, dias_atras=dias_atras)

    if not items:
        raise RuntimeError("Nenhuma cena Sentinel-2 encontrada pro período/área.")

    print(f"[calc_ndvi] {len(items)} cena(s) Sentinel-2 encontradas nos últimos {dias_atras} dias.")

    aneis_wgs84 = []  # lista de (segment_id, anel), preserva ordem ~geográfica
    for _, seg in gdf_segments.iterrows():
        anel = seg.geometry.buffer(buffer_max_m).difference(
            seg.geometry.buffer(buffer_min_m)
        )
        anel_wgs84 = gpd.GeoSeries([anel], crs=gdf_segments.crs).to_crs(4326).iloc[0]
        aneis_wgs84.append((seg["segment_id"], anel_wgs84))

    # divide em lotes de CHUNK_SIZE segmentos, na ordem em que já
    # aparecem (aproximadamente sequencial ao longo da rodovia, já
    # que segment_highway.py gera os segmentos em ordem de trecho+km)
    chunks = [
        aneis_wgs84[i : i + CHUNK_SIZE] for i in range(0, len(aneis_wgs84), CHUNK_SIZE)
    ]

    try:
        from tqdm import tqdm
        items_iter = tqdm(items, desc="Processando cenas Sentinel-2")
    except ImportError:
        items_iter = items  # tqdm opcional -- roda sem barra de progresso se não instalado

    registros = []
    for item in items_iter:
        registros.extend(_processar_item(item, chunks))

    df_bruto = pd.DataFrame(registros)
    if df_bruto.empty:
        return df_bruto

    def _media_ponderada(grupo):
        pesos = grupo["n_pixels_validos"]
        return pd.Series(
            {
                "ndvi_medio": np.average(grupo["ndvi_medio"], weights=pesos),
                "n_pixels_validos": int(pesos.sum()),
            }
        )

    df_agregado = (
        df_bruto.groupby(["segment_id", "data"])
        .apply(_media_ponderada, include_groups=False)
        .reset_index()
    )

    n_antes = len(df_agregado)
    df_agregado = df_agregado[df_agregado["n_pixels_validos"] >= MIN_PIXELS_VALIDOS].copy()
    n_descartadas = n_antes - len(df_agregado)
    if n_descartadas > 0:
        print(
            f"[calc_ndvi] {n_descartadas} observação(ões) descartada(s) por "
            f"terem menos de {MIN_PIXELS_VALIDOS} pixels válidos."
        )

    return df_agregado


if __name__ == "__main__":
    from pipeline.ingest.fetch_dnit_network import fetch_dnit_network
    from pipeline.process.segment_highway import segment_highway

    gdf_anh_dnit = fetch_dnit_network(
        path="data/raw/SP330.json",
        codigo_rodovia="330",
        uf="SP",
    )
    gdf_segments = segment_highway(gdf_anh_dnit)

    df_ndvi = calc_ndvi_por_segmento(gdf_segments.head(10))

    print(df_ndvi.shape)
    print(df_ndvi.head(20))
