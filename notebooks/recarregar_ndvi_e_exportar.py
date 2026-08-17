import pandas as pd
from pipeline.ingest.fetch_dnit_network import fetch_dnit_network
from pipeline.process.segment_highway import segment_highway
from pipeline.process.viability_filter import classify_viability
from pipeline.load.write_to_postgis import get_engine, write_ndvi

engine = get_engine()

gdf_anh_dnit = fetch_dnit_network(path="data/raw/SP330.json", codigo_rodovia="330", uf="SP")
gdf_segments = segment_highway(gdf_anh_dnit)
gdf_viabilidade = classify_viability(gdf_segments)

df_ndvi = pd.read_csv("data/processed/ndvi_completo.csv", parse_dates=["data"])
write_ndvi(df_ndvi, engine)


df_contexto = gdf_viabilidade[["segment_id", "km_inicial", "km_final", "confianca_viabilidade"]]
df_para_ml = df_ndvi.merge(df_contexto, on="segment_id", how="left")
df_para_ml.to_csv("data/processed/ndvi_para_ml.csv", index=False)
