"""
pipeline/ingest/fetch_osm_network.py

Busca a rede viaria do OSM via osmnx, delimitando a area de busca pelo
buffer da malha DNIT ja validada (evita baixar a malha do estado
inteiro) e filtra pelas tags 'ref'/'name' pra isolar so a Anhanguera.
"""

import geopandas as gpd
import osmnx as ox


def fetch_osm_network(
    gdf_reference: gpd.GeoDataFrame,
    buffer_m: float = 500,
) -> gpd.GeoDataFrame:
    """
    gdf_reference: a malha DNIT ja filtrada/reprojetada (CRS metrico),
    usada so pra delimitar a area de busca no OSM.

    Retorna as edges (vias) do OSM dentro dessa area, sem filtro de
    nome/ref ainda -- a filtragem fica em filter_anhanguera().
    """
    area_busca = gdf_reference.geometry.buffer(buffer_m).union_all()
    area_busca_gdf = gpd.GeoDataFrame(geometry=[area_busca], crs=gdf_reference.crs)
    polygon_wgs84 = area_busca_gdf.to_crs(epsg=4326).geometry.iloc[0]

    # simplify=False preserva os segmentos originais do OSM sem
    # mesclar vias -- evita que tags 'ref'/'name' virem listas
    G = ox.graph_from_polygon(polygon_wgs84, network_type="drive", simplify=False)
    gdf_edges = ox.graph_to_gdfs(G, nodes=False, edges=True)

    return gdf_edges


def filter_anhanguera(
    gdf_edges: gpd.GeoDataFrame,
    ref_contains: str = "330",
    name_contains: str = "Anhanguera",
) -> gpd.GeoDataFrame:
    """Isola as vias cuja tag 'ref' ou 'name' identifica a Anhanguera."""

    def _match(row):
        ref = str(row.get("ref", ""))
        name = str(row.get("name", ""))
        return ref_contains in ref or name_contains in name

    mask = gdf_edges.apply(_match, axis=1)
    return gdf_edges[mask]


if __name__ == "__main__":
    from pipeline.ingest.fetch_dnit_network import fetch_dnit_network

    gdf_anh_dnit = fetch_dnit_network(
        path="data/raw/SP330.json",
        codigo_rodovia="330",
        uf="SP",
    )

    gdf_osm_raw = fetch_osm_network(gdf_anh_dnit)
    gdf_osm_anh = filter_anhanguera(gdf_osm_raw)

    print(gdf_osm_raw.shape, "vias na área de busca")
    print(gdf_osm_anh.shape, "vias identificadas como Anhanguera")
    print(gdf_osm_anh[["ref", "name", "oneway"]].head(10))
