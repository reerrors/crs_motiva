"""
pipeline/process/segment_highway.py

Divide os trechos de uma rodovia em segmentos fixos (padrão: 1km),
usando a quilometragem OFICIAL do DNIT (colunas Quilometragem_Inicial /
Quilometragem_Final) como referência de distância — não a distância
bruta da geometria. Isso garante que o identificador de cada segmento
bate com o km real usado em campo, mesmo que a geometria (traçado GPS)
tenha um comprimento levemente diferente do km oficial.
"""

import geopandas as gpd
from shapely.ops import substring, linemerge
from shapely.geometry import MultiLineString

SEGMENT_LENGTH_KM = 1.0


def _normalize_to_linestring(geom):
    """
    Garante que a geometria seja um LineString único antes de segmentar.
    Se vier como MultiLineString, tenta "colar" as partes com linemerge
    (funciona quando as partes são contíguas, ponta a ponta). Se ainda
    assim sobrar um MultiLineString (partes realmente desconectadas),
    retorna a maior parte individual como aproximação, e sinaliza.
    """
    if geom.geom_type == "LineString":
        return geom, False

    merged = linemerge(geom)
    if merged.geom_type == "LineString":
        return merged, False

    # ainda é MultiLineString após o merge -- partes desconectadas de
    # verdade. Usa a maior parte como aproximação e sinaliza o corte.
    maior_parte = max(merged.geoms, key=lambda g: g.length)
    return maior_parte, True


def segment_highway(
    gdf: gpd.GeoDataFrame,
    segment_length_km: float = SEGMENT_LENGTH_KM,
) -> gpd.GeoDataFrame:
    """
    Recebe um GeoDataFrame já reprojetado para um CRS métrico
    (ex: EPSG:31983), contendo trechos de rodovia com colunas
    'Quilometragem_Inicial' e 'Quilometragem_Final', e retorna um
    novo GeoDataFrame com um registro por segmento, identificado
    pelo km oficial de início/fim.

    Cada trecho original do DNIT é processado de forma independente,
    então trechos duplicados (pista norte/sul cobrindo o mesmo km)
    geram segmentos separados, ligados ao trecho de origem via
    'trecho_origem_id' — a decisão de mesclar ou não fica pra depois,
    na Fase 1 (viability_filter.py).
    """
    segments = []
    trechos_invalidos = []
    trechos_aproximados = []

    for idx, trecho in gdf.iterrows():
        km_inicial = trecho["Quilometragem_Inicial"]
        km_final = trecho["Quilometragem_Final"]
        geom_bruta = trecho.geometry

        km_extensao_oficial = km_final - km_inicial

        # trecho com km oficial ausente, invertido ou zerado -- não dá
        # pra segmentar com confiança, registra e pula
        if km_extensao_oficial <= 0 or geom_bruta is None or geom_bruta.length == 0:
            trechos_invalidos.append(idx)
            continue

        geom, foi_aproximado = _normalize_to_linestring(geom_bruta)
        if foi_aproximado:
            trechos_aproximados.append(idx)

        geom_length_m = geom.length

        n_full_segments = int(km_extensao_oficial // segment_length_km)
        remainder_km = km_extensao_oficial - (n_full_segments * segment_length_km)

        cut_points_km = [i * segment_length_km for i in range(n_full_segments + 1)]
        if remainder_km > 1e-6:  # sobra um pedaço menor que 1km no fim do trecho
            cut_points_km.append(km_extensao_oficial)

        for i in range(len(cut_points_km) - 1):
            frac_start = cut_points_km[i] / km_extensao_oficial
            frac_end = cut_points_km[i + 1] / km_extensao_oficial

            start_dist_m = frac_start * geom_length_m
            end_dist_m = frac_end * geom_length_m

            sub_geom = substring(geom, start_dist_m, end_dist_m)

            km_seg_inicial = km_inicial + cut_points_km[i]
            km_seg_final = km_inicial + cut_points_km[i + 1]

            segment_id = (
                f"SP-{trecho['Codigo_Rodovia']}_t{trecho['id']}_"
                f"km{km_seg_inicial:07.3f}_{km_seg_final:07.3f}"
            )

            segments.append(
                {
                    "segment_id": segment_id,
                    "codigo_rodovia": trecho["Codigo_Rodovia"],
                    "km_inicial": round(km_seg_inicial, 3),
                    "km_final": round(km_seg_final, 3),
                    "trecho_origem_id": trecho["id"],
                    "trecho_local_inicio": trecho.get("Local_Inicio"),
                    "trecho_local_fim": trecho.get("Local_Fim"),
                    "geometry": sub_geom,
                }
            )

    if trechos_invalidos:
        print(
            f"[segment_highway] {len(trechos_invalidos)} trecho(s) pulado(s) "
            f"por km oficial inválido ou geometria vazia: {trechos_invalidos}"
        )

    if trechos_aproximados:
        print(
            f"[segment_highway] {len(trechos_aproximados)} trecho(s) tinham "
            f"MultiLineString desconectado -- usada a maior parte como "
            f"aproximação, pode haver leve perda de extensão: {trechos_aproximados}"
        )

    gdf_segments = gpd.GeoDataFrame(segments, geometry="geometry", crs=gdf.crs)
    return gdf_segments


if __name__ == "__main__":
    # Reaproveita a função de ingestão já validada, em vez de
    # reimplementar leitura + filtro + reprojeção aqui.
    from pipeline.ingest.fetch_dnit_network import fetch_dnit_network
    import pandas as pd

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", None)

    gdf_anh = fetch_dnit_network(
        path="data/raw/SP330.json",
        codigo_rodovia="330",
        uf="SP",
    )
    gdf_segments = segment_highway(gdf_anh)

    print(gdf_segments.shape)
    print(gdf_segments.dtypes)
    print(gdf_segments.head())
    print(f"Total de km cobertos: {gdf_segments['km_final'].max() - gdf_segments['km_inicial'].min():.2f}")
    print(gdf_segments["segment_id"].duplicated().sum())
    colunas_texto = ["segment_id", "codigo_rodovia", "trecho_origem_id","trecho_local_inicio", "trecho_local_fim"]
    for col in colunas_texto:
        print(col, gdf_segments[col].str.len().max())
