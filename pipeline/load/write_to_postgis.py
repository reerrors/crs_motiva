"""
pipeline/load/write_to_postgis.py

Carrega os resultados do pipeline (segmentos, viabilidade, serie de
NDVI) nas tabelas do PostgreSQL/PostGIS. Os nomes de coluna do Python
(portugues, vindo do processamento) nao batem 1:1 com os nomes do
schema SQL (ingles, decisao tomada ao desenhar o banco) -- por isso
cada tabela tem um mapeamento explicito, em vez de depender de nomes
coincidirem por acaso.

Ordem de carga importa: 'segments' precisa ser gravada ANTES de
'viability' e 'ndvi_observations', porque as duas ultimas tem
FOREIGN KEY apontando pra 'segments' -- gravar fora de ordem quebra
a constraint.
"""

import os

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

# lido de variavel de ambiente, nunca hardcoded -- configura em
# .env (ver .env.example na raiz do projeto) ou exporta no shell:
# export DATABASE_URL="postgresql+psycopg2://postgres:SENHA@localhost:5432/crs_motiva"
DATABASE_URL = os.environ.get("DATABASE_URL")

MAPA_SEGMENTS = {
    "segment_id": "segment_id",
    "codigo_rodovia": "road_code",
    "km_inicial": "km_start",
    "km_final": "km_end",
    "trecho_origem_id": "track_origin_id",
    "trecho_local_inicio": "track_start_name",
    "trecho_local_fim": "track_end_name",
    "geometry": "geometry",
}

MAPA_VIABILITY = {
    "segment_id": "segment_id",
    "fracao_graminea": "grass_ratio",
    "confianca_viabilidade": "confidence",
}

MAPA_NDVI = {
    "segment_id": "segment_id",
    "data": "date_capture",
    "ndvi_medio": "ndvi_avg",
    "n_pixels_validos": "valid_pixels",
}


def get_engine():
    if not DATABASE_URL:
        raise RuntimeError(
            "Variável de ambiente DATABASE_URL não definida. "
            "Configure no .env ou exporte no shell antes de rodar."
        )
    return create_engine(DATABASE_URL)


def write_segments(gdf_segments: gpd.GeoDataFrame, engine, if_exists: str = "append"):
    gdf = gdf_segments.rename(columns=MAPA_SEGMENTS)[list(MAPA_SEGMENTS.values())]
    gdf.to_postgis("segments", engine, if_exists=if_exists, index=False)
    print(f"[write_to_postgis] {len(gdf)} linha(s) gravadas em 'segments'.")


def write_viability(gdf_viabilidade, engine, if_exists: str = "append"):
    df = gdf_viabilidade.rename(columns=MAPA_VIABILITY)[list(MAPA_VIABILITY.values())]
    df = pd.DataFrame(df)  # garante que não sobra geometria (viability não tem coluna espacial)
    df.to_sql("viability", engine, if_exists=if_exists, index=False)
    print(f"[write_to_postgis] {len(df)} linha(s) gravadas em 'viability'.")


def write_ndvi(df_ndvi: pd.DataFrame, engine, if_exists: str = "append"):
    df = df_ndvi.rename(columns=MAPA_NDVI)[list(MAPA_NDVI.values())]
    # n_pixels_validos veio como float (efeito do np.average na agregação
    # do calc_ndvi.py) -- a coluna no banco é INTEGER, converte antes
    df["valid_pixels"] = df["valid_pixels"].astype(int)
    df.to_sql("ndvi_observations", engine, if_exists=if_exists, index=False)
    print(f"[write_to_postgis] {len(df)} linha(s) gravadas em 'ndvi_observations'.")


if __name__ == "__main__":
    from pipeline.ingest.fetch_dnit_network import fetch_dnit_network
    from pipeline.process.segment_highway import segment_highway
    from pipeline.process.viability_filter import classify_viability
    from pipeline.process.calc_ndvi import calc_ndvi_por_segmento

    engine = get_engine()

    gdf_anh_dnit = fetch_dnit_network(
        path="data/raw/SP330.json",
        codigo_rodovia="330",
        uf="SP",
    )
    gdf_segments = segment_highway(gdf_anh_dnit)
    gdf_viabilidade = classify_viability(gdf_segments)

    # teste pequeno primeiro (10 segmentos) -- troca pra
    # gdf_segments completo quando já tiver validado a carga
    df_ndvi = calc_ndvi_por_segmento(gdf_segments.head(10))

    write_segments(gdf_segments, engine)
    write_viability(gdf_viabilidade, engine)
    write_ndvi(df_ndvi, engine)
