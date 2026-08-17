import pandas as pd
from pipeline.ingest.fetch_dnit_network import fetch_dnit_network
from pipeline.process.segment_highway import segment_highway
from pipeline.process.viability_filter import classify_viability

gdf_anh_dnit = fetch_dnit_network(path="data/raw/SP330.json", codigo_rodovia="330", uf="SP")
gdf_segments = segment_highway(gdf_anh_dnit)
gdf_viabilidade = classify_viability(gdf_segments)

gdf_export = gdf_viabilidade.rename(columns={
    "codigo_rodovia": "road_code",
    "km_inicial": "km_start",
    "km_final": "km_end",
    "trecho_local_inicio": "track_start_name",
    "trecho_local_fim": "track_end_name",
    "fracao_graminea": "grass_ratio",
    "confianca_viabilidade": "confidence",
})[["segment_id", "road_code", "km_start", "km_end", "track_start_name", "track_end_name", "grass_ratio", "confidence", "geometry"]]

gdf_export_wgs84 = gdf_export.to_crs(4326)  # MapLibre espera EPSG:4326, não 31983
gdf_export_wgs84.to_file("data/processed/segments_exemplo.geojson", driver="GeoJSON")
