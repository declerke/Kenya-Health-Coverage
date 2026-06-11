"""Ingest Kenya county admin boundaries from GADM 4.1 GeoPackage.

Downloads the GADM GeoPackage (45 MB, cached locally), extracts the
level-1 layer (Kenya's 47 counties), computes centroids and area_km2,
then writes to DuckDB as raw_counties with geometry stored as WKT.
"""

import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.ops import transform
import pyproj

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import get_conn, get_data_dir, get_logger, download_file

logger = get_logger(__name__)

GADM_URL = os.getenv(
    "GADM_GPKG_URL",
    "https://geodata.ucdavis.edu/gadm/gadm4.1/gpkg/gadm41_KEN.gpkg",
)

# GADM layer name for level-1 admin (counties in Kenya)
GADM_LAYER = "ADM_ADM_1"


def _compute_area_km2(gdf: gpd.GeoDataFrame) -> pd.Series:
    """Reproject to a local equal-area CRS and compute polygon area in km²."""
    # Africa Albers Equal Area Conic (EPSG:102022 / custom)
    # Simpler: use UTM zone 37S which covers most of Kenya
    gdf_utm = gdf.to_crs("EPSG:32737")
    return gdf_utm.geometry.area / 1_000_000  # m² → km²


def load_counties(force_download: bool = False) -> gpd.GeoDataFrame:
    """Download GADM GeoPackage and return cleaned county GeoDataFrame."""
    data_dir = get_data_dir()
    dest = data_dir / "gadm41_KEN.gpkg"

    download_file(GADM_URL, dest, force=force_download)

    logger.info("Reading GADM layer '%s' ...", GADM_LAYER)
    gdf = gpd.read_file(str(dest), layer=GADM_LAYER, engine="pyogrio")
    logger.info("GADM raw rows: %d", len(gdf))

    # Keep relevant columns
    # GADM uses NAME_1 for county name at level 1
    if "NAME_1" not in gdf.columns:
        # Try alternate column names
        name_cols = [c for c in gdf.columns if c.startswith("NAME_")]
        logger.warning("NAME_1 not found; available name columns: %s", name_cols)
        if name_cols:
            gdf = gdf.rename(columns={name_cols[0]: "NAME_1"})

    gdf = gdf[["NAME_1", "geometry"]].copy()
    gdf = gdf.rename(columns={"NAME_1": "county_name"})

    # Ensure WGS84
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # Compute area
    gdf["area_km2"] = _compute_area_km2(gdf)

    # Compute centroids in WGS84
    gdf_utm = gdf.to_crs("EPSG:32737")
    centroids_utm = gdf_utm.geometry.centroid
    centroids_wgs = centroids_utm.to_crs("EPSG:4326")
    gdf["centroid_lat"] = centroids_wgs.y
    gdf["centroid_lon"] = centroids_wgs.x

    # Store geometry as WKT for DuckDB
    gdf["geometry_wkt"] = gdf["geometry"].apply(lambda g: g.wkt)

    # Validate we have 47 counties
    n = len(gdf)
    if n != 47:
        logger.warning("Expected 47 Kenya counties, got %d", n)
    else:
        logger.info("Found all 47 Kenya counties")

    return gdf


def write_to_duckdb(gdf: gpd.GeoDataFrame) -> None:
    """Write county GeoDataFrame to DuckDB raw_counties table."""
    df = gdf[["county_name", "area_km2", "centroid_lat", "centroid_lon", "geometry_wkt"]].copy()

    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS raw_counties")
    conn.execute("""
        CREATE TABLE raw_counties (
            county_name   VARCHAR,
            area_km2      DOUBLE,
            centroid_lat  DOUBLE,
            centroid_lon  DOUBLE,
            geometry_wkt  VARCHAR
        )
    """)
    conn.execute("INSERT INTO raw_counties SELECT * FROM df")
    count = conn.execute("SELECT COUNT(*) FROM raw_counties").fetchone()[0]
    logger.info("Wrote %d counties to raw_counties", count)
    conn.close()


def main() -> None:
    logger.info("=== Ingest: County Boundaries ===")
    gdf = load_counties()
    write_to_duckdb(gdf)
    logger.info("Done.")


if __name__ == "__main__":
    main()
