"""
pipeline/ingest/fetch_dnit_network.py

Le a malha rodoviaria exportada do VGeo/SNV do DNIT, filtra pela
rodovia e UF de interesse, e reprojeta para um CRS metrico -- pronto
pra ser consumido por qualquer etapa seguinte do pipeline (segmentacao,
calculo de viabilidade, etc), sem que cada uma precise reimplementar
essa leitura.
"""

import geopandas as gpd

DEFAULT_METRIC_CRS = "EPSG:31983"  # UTM 23S -- adequado pra SP


def fetch_dnit_network(
    path: str,
    codigo_rodovia: str,
    uf: str,
    target_crs: str = DEFAULT_METRIC_CRS,
) -> gpd.GeoDataFrame:
    """
    Le um GeoJSON exportado do VGeo/DNIT, filtra pelo codigo da rodovia
    e UF exatos, e reprojeta pro CRS metrico informado.

    Parameters
    ----------
    path: caminho do GeoJSON bruto (ex: "data/raw/sp330_dnit.geojson")
    codigo_rodovia: codigo exato da rodovia, ex: "330"
    uf: sigla do estado, ex: "SP"
    target_crs: CRS metrico de destino (default: UTM 23S, EPSG:31983)

    Returns
    -------
    GeoDataFrame filtrado e reprojetado
    """
    gdf = gpd.read_file(path)

    gdf_filtered = gdf[
        (gdf["Codigo_Rodovia"] == codigo_rodovia) & (gdf["Unidade_Federacao"] == uf)
    ].copy()

    if gdf_filtered.empty:
        raise ValueError(
            f"Nenhum trecho encontrado para Codigo_Rodovia={codigo_rodovia!r} "
            f"e Unidade_Federacao={uf!r}. Confira os valores com "
            f"gdf['Codigo_Rodovia'].unique() antes de filtrar."
        )

    gdf_reprojected = gdf_filtered.to_crs(target_crs)
    return gdf_reprojected


if __name__ == "__main__":
    gdf_anh = fetch_dnit_network(
        path="data/raw/SP330",
        codigo_rodovia="330",
        uf="SP",
    )
    print(gdf_anh.shape)
    print(gdf_anh.crs)
