"""
pipeline/process/viability_filter.py

Classifica a viabilidade de cada segmento com base em COBERTURA REAL
DO SOLO (ESA WorldCover, 10m), em vez de estimar largura por geometria
(abordagens anteriores por distancia entre pistas DNIT/OSM foram
abandonadas -- nao capturavam os acostamentos laterais, que sao tao
importantes quanto o canteiro central pra manutencao real).

Metodo: cria um buffer simetrico ao redor de toda a linha de cada
segmento (cobre canteiro central + os dois acostamentos ao mesmo
tempo), consulta o ESA WorldCover dentro desse buffer, e calcula a
fracao de pixels classificados como gramínea/pastagem (classe 30).
Fracao alta = ha vegetacao rasteira real e mapeavel ali = viavel.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer
import pystac_client
import rasterio
from rasterio.mask import mask

WORLDCOVER_GRASSLAND_CLASS = 30
BUFFER_MIN_M = 8.0   # raio interno -- exclui o pavimento/eixo da via
BUFFER_MAX_M = 20.0  # raio externo -- limite da "zona de manutenção" considerada

# dois limiares em vez de um -- gera 3 níveis em vez de 2 (alta/baixa)
LIMIAR_BAIXA_MEDIA = 0.10   # abaixo disso: praticamente sem gramínea (pavimento/construído domina)
LIMIAR_MEDIA_ALTA = 0.30    # acima disso: cobertura de gramínea consistente

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"


def _get_worldcover_items(bbox_wgs84):
    catalog = pystac_client.Client.open(
        STAC_URL, modifier=planetary_computer.sign_inplace
    )
    search = catalog.search(collections=["esa-worldcover"], bbox=bbox_wgs84)
    return list(search.items())


def classify_viability(
    gdf_segments: gpd.GeoDataFrame,
    buffer_min_m: float = BUFFER_MIN_M,
    buffer_max_m: float = BUFFER_MAX_M,
) -> gpd.GeoDataFrame:
    gdf = gdf_segments.copy()

    bbox_wgs84 = list(gdf.to_crs(4326).total_bounds)  # [minx, miny, maxx, maxy]
    items = _get_worldcover_items(bbox_wgs84)
    if not items:
        raise RuntimeError("Nenhum tile do ESA WorldCover encontrado pra essa área.")

    # abre cada tile relevante UMA vez, fora do loop de segmentos --
    # evita reabrir o mesmo arquivo remoto centenas de vezes
    datasets = [rasterio.open(item.assets["map"].href) for item in items]

    fracoes = []
    for _, seg in gdf.iterrows():
        # anel (buffer externo menos buffer interno) -- exclui o
        # pavimento da via, que nunca vai contar como gramínea e
        # diluía a métrica na versão anterior (buffer cheio)
        anel = seg.geometry.buffer(buffer_max_m).difference(
            seg.geometry.buffer(buffer_min_m)
        )
        anel_wgs84 = gpd.GeoSeries([anel], crs=gdf.crs).to_crs(4326).iloc[0]

        pixels_encontrados = []
        for src in datasets:
            try:
                out_image, _ = mask(src, [anel_wgs84], crop=True)
            except ValueError:
                continue  # anel não intersecta esse tile específico
            pixels_encontrados.append(out_image.flatten())

        if not pixels_encontrados:
            fracoes.append(None)
            continue

        todos_pixels = np.concatenate(pixels_encontrados)
        todos_pixels = todos_pixels[todos_pixels != 0]  # remove nodata
        if len(todos_pixels) == 0:
            fracoes.append(None)
            continue

        fracao_graminea = float(np.mean(todos_pixels == WORLDCOVER_GRASSLAND_CLASS))
        fracoes.append(fracao_graminea)

    for src in datasets:
        src.close()

    gdf["fracao_graminea"] = fracoes

    def _classificar(f):
        if f is None or pd.isna(f):
            return "low"  # sem observação -- trata como low por padrão, conservador
        if f < LIMIAR_BAIXA_MEDIA:
            return "low"
        if f < LIMIAR_MEDIA_ALTA:
            return "medium"
        return "high"

    gdf["confianca_viabilidade"] = gdf["fracao_graminea"].apply(_classificar)

    return gdf


if __name__ == "__main__":
    from pipeline.ingest.fetch_dnit_network import fetch_dnit_network
    from pipeline.process.segment_highway import segment_highway

    gdf_anh_dnit = fetch_dnit_network(
        path="data/raw/SP330.json",
        codigo_rodovia="330",
        uf="SP",
    )
    gdf_segments = segment_highway(gdf_anh_dnit)

    gdf_viabilidade = classify_viability(gdf_segments)

    print(gdf_viabilidade["confianca_viabilidade"].value_counts())
    print(gdf_viabilidade["fracao_graminea"].describe())
    print(gdf_viabilidade.dtypes)
