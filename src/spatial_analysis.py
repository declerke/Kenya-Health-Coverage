"""Spatial analysis: compute facility coverage buffers and county-level metrics.

This module:
1. Loads facilities and counties from DuckDB
2. Projects all geometries to UTM Zone 37N (EPSG:32637) for metric operations
3. Builds Shapely buffer circles at 5km, 10km, 15km per facility (in metres)
4. Computes county coverage ratios (union of buffers ∩ county polygon area / county area)
   — all area calculations are done in the projected CRS, never in geographic degrees
5. Computes facility density per 100k population
6. Computes distance from each county centroid to the nearest Level 4+ hospital
   — distance computed in projected CRS (metres), converted to km
7. Builds composite health access index (0-100)
8. Writes all spatial results back to DuckDB for dbt to pick up

CRS note: EPSG:32637 (WGS 84 / UTM Zone 37N) is used for all metric operations.
          Kenya spans 34°E–42°E and ~4°S–5°N; UTM 37N gives accurate distances and
          areas for the majority of the country.
"""

import sys
from pathlib import Path

import pandas as pd
import numpy as np
import geopandas as gpd
from shapely import wkt, ops
from shapely.geometry import Point, MultiPolygon, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import get_conn, get_logger

logger = get_logger(__name__)

# Buffer distances in METRES (projected, not degrees)
# 1° ≈ 111 km at the equator — do NOT use degree approximations for buffer radii
BUFFER_5KM_M  = 5_000   # 5 km
BUFFER_10KM_M = 10_000  # 10 km
BUFFER_15KM_M = 15_000  # 15 km

# UTM Zone 37N — the projected CRS for all spatial metric operations
UTM_CRS = "EPSG:32637"
GEO_CRS = "EPSG:4326"

# Composite index weights — must sum to 1.0
WEIGHT_COVERAGE       = 0.40
WEIGHT_DENSITY        = 0.30
WEIGHT_WB_INDICATORS  = 0.30

assert abs(WEIGHT_COVERAGE + WEIGHT_DENSITY + WEIGHT_WB_INDICATORS - 1.0) < 1e-9, (
    "Composite index weights must sum to 1.0"
)


# ---------------------------------------------------------------------------
# Load raw data from DuckDB
# ---------------------------------------------------------------------------

def load_facilities_gdf() -> gpd.GeoDataFrame:
    """Load raw facilities from DuckDB and return as GeoDataFrame (EPSG:4326)."""
    conn = get_conn()
    df = conn.execute(
        "SELECT facility_id, facility_name, facility_type, facility_level, "
        "county, latitude, longitude FROM raw_facilities"
    ).df()
    conn.close()

    geometry = gpd.points_from_xy(df["longitude"], df["latitude"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs=GEO_CRS)
    logger.info("Loaded %d facilities from DuckDB", len(gdf))
    return gdf


def load_counties_gdf() -> gpd.GeoDataFrame:
    """Load raw counties (geometry as WKT) from DuckDB (EPSG:4326)."""
    conn = get_conn()
    df = conn.execute(
        "SELECT county_name, area_km2, centroid_lat, centroid_lon, geometry_wkt "
        "FROM raw_counties"
    ).df()
    conn.close()

    df["geometry"] = gpd.GeoSeries.from_wkt(df["geometry_wkt"])
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs=GEO_CRS)
    gdf["centroid_geom"] = gpd.points_from_xy(gdf["centroid_lon"], gdf["centroid_lat"])
    logger.info("Loaded %d counties from DuckDB", len(gdf))
    return gdf


def load_population() -> pd.DataFrame:
    """Load county population from DuckDB."""
    conn = get_conn()
    df = conn.execute(
        "SELECT county_name, population_2023 FROM raw_county_population"
    ).df()
    conn.close()
    return df


def load_wb_latest() -> pd.DataFrame:
    """Load most-recent value of each WB indicator for Kenya."""
    conn = get_conn()
    df = conn.execute("""
        SELECT indicator_code, indicator_label, value, year
        FROM raw_wb_indicators
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY indicator_code ORDER BY year DESC
        ) = 1
    """).df()
    conn.close()
    return df


# ---------------------------------------------------------------------------
# Buffer analysis — all in UTM 37N (metres)
# ---------------------------------------------------------------------------

def compute_facility_buffers(gdf: gpd.GeoDataFrame) -> dict[str, gpd.GeoDataFrame]:
    """Create buffer GeoDataFrames at 5km, 10km, 15km for all facilities.

    Projects to UTM 37N before buffering, then reprojects back to WGS84
    so results can be stored as WKT for the dashboard.
    """
    buffers = {}
    gdf_utm = gdf.to_crs(UTM_CRS)
    for label, radius_m in [("5km", BUFFER_5KM_M), ("10km", BUFFER_10KM_M), ("15km", BUFFER_15KM_M)]:
        buf_utm = gdf_utm.set_geometry(gdf_utm.geometry.buffer(radius_m))
        buf_wgs = buf_utm.to_crs(GEO_CRS)
        buffers[label] = buf_wgs
    logger.info("Computed buffers at 5km, 10km, 15km for %d facilities", len(gdf))
    return buffers


def compute_county_coverage(
    counties_gdf: gpd.GeoDataFrame,
    facilities_gdf: gpd.GeoDataFrame,
    buffer_radius_m: float,
) -> pd.Series:
    """For each county, compute coverage ratio at *buffer_radius_m* metres.

    Coverage ratio = area of (union of facility buffers ∩ county polygon) / county area.
    ALL area calculations are done in UTM 37N (EPSG:32637) to avoid degree-based
    distortion.  Returns a Series indexed by county_name with values in [0, 1].

    Parameters
    ----------
    buffer_radius_m : float
        Buffer radius in metres (e.g. 10_000 for 10 km).
    """
    logger.info("Computing coverage ratios at radius=%.0f m...", buffer_radius_m)

    # Project both GDFs to UTM for metric operations
    counties_utm = counties_gdf.to_crs(UTM_CRS)
    facilities_utm = facilities_gdf.to_crs(UTM_CRS)

    # Buffer all facilities in UTM (metres)
    facilities_buffered_utm = facilities_utm.set_geometry(facilities_utm.geometry.buffer(buffer_radius_m))

    coverage = {}
    for idx_row, county_row in counties_utm.iterrows():
        county_poly = county_row["geometry"]
        county_name = counties_gdf.loc[idx_row, "county_name"]

        # Find facilities whose buffer intersects this county (spatial index)
        county_facilities = facilities_buffered_utm[
            facilities_buffered_utm.intersects(county_poly)
        ]

        if county_facilities.empty:
            coverage[county_name] = 0.0
            continue

        # Union all buffers, then intersect with county — area in m²
        union_buffers = ops.unary_union(county_facilities.geometry.values)
        covered_area = union_buffers.intersection(county_poly).area
        county_area = county_poly.area

        ratio = min(covered_area / county_area, 1.0) if county_area > 0 else 0.0
        coverage[county_name] = ratio

    return pd.Series(coverage, name=f"coverage_{buffer_radius_m:.0f}m")


# ---------------------------------------------------------------------------
# Facility density
# ---------------------------------------------------------------------------

def compute_facility_density(
    facilities_gdf: gpd.GeoDataFrame,
    counties_gdf: gpd.GeoDataFrame,
    population_df: pd.DataFrame,
) -> pd.Series:
    """Count facilities per 100,000 population per county.

    Returns a Series indexed by county_name.
    """
    facility_counts = (
        facilities_gdf.groupby("county")
        .size()
        .rename("facility_count")
        .reset_index()
    )

    merged = counties_gdf[["county_name"]].merge(
        facility_counts, left_on="county_name", right_on="county", how="left"
    )
    merged["facility_count"] = merged["facility_count"].fillna(0)

    merged = merged.merge(population_df, on="county_name", how="left")
    merged["population_2023"] = merged["population_2023"].fillna(100_000)

    merged["facilities_per_100k"] = (
        merged["facility_count"] / merged["population_2023"] * 100_000
    )

    return merged.set_index("county_name")["facilities_per_100k"]


# ---------------------------------------------------------------------------
# Nearest Level-4+ hospital distance — projected CRS
# ---------------------------------------------------------------------------

def compute_nearest_hospital_distance(
    counties_gdf: gpd.GeoDataFrame,
    facilities_gdf: gpd.GeoDataFrame,
) -> pd.Series:
    """For each county centroid, compute the distance (km) to the nearest
    Level 4, 5, or 6 facility.

    Distance is computed in UTM Zone 37N (EPSG:32637) — metres — then
    converted to km.  Using geographic degrees for distance is incorrect and
    produces meaningless results.

    Returns a Series indexed by county_name.
    """
    hospitals = facilities_gdf[facilities_gdf["facility_level"] >= 4]
    logger.info("Level 4+ hospitals: %d", len(hospitals))

    if hospitals.empty:
        logger.warning("No Level 4+ hospitals found; using all facilities as fallback")
        hospitals = facilities_gdf

    # Project both GDFs to UTM for metric distance
    hospitals_utm = hospitals.to_crs(UTM_CRS)
    counties_utm = counties_gdf.to_crs(UTM_CRS)

    hosp_coords_m = np.column_stack([
        hospitals_utm.geometry.x.values,
        hospitals_utm.geometry.y.values,
    ])

    distances = {}
    for _, county_row in counties_utm.iterrows():
        county_name = county_row["county_name"]
        # Use the centroid of the projected county polygon as the reference point
        cx, cy = county_row["geometry"].centroid.x, county_row["geometry"].centroid.y

        if not hosp_coords_m.size:
            distances[county_name] = float("nan")
            continue

        # Euclidean distance in metres (valid in a projected CRS)
        dists_m = np.sqrt(
            (hosp_coords_m[:, 0] - cx) ** 2 + (hosp_coords_m[:, 1] - cy) ** 2
        )
        nearest_km = float(dists_m.min()) / 1000.0
        distances[county_name] = round(nearest_km, 2)

    return pd.Series(distances, name="nearest_hospital_km")


# ---------------------------------------------------------------------------
# Composite health access index
# ---------------------------------------------------------------------------

def _minmax_scale(s: pd.Series) -> pd.Series:
    """Scale series to [0, 1]; handle zero-range gracefully."""
    lo, hi = s.min(), s.max()
    if hi == lo:
        return pd.Series(0.5, index=s.index)
    return (s - lo) / (hi - lo)


def compute_health_access_index(
    coverage_series: pd.Series,
    density_series: pd.Series,
    nearest_hospital_series: pd.Series,
    wb_latest: pd.DataFrame,
) -> pd.DataFrame:
    """Compute composite health access index (0–100) per county.

    Weights (must sum to 1.0):
      40% — 10km coverage ratio
      30% — facility density per 100k
      30% — normalised WB indicators (inverse mortality, availability of beds/physicians)

    Returns a DataFrame with columns: county_name, coverage_ratio_10km,
    facilities_per_100k, nearest_hospital_km, wb_score, health_access_index,
    is_underserved.
    """
    df = pd.DataFrame(
        {
            "coverage_ratio_10km": coverage_series,
            "facilities_per_100k": density_series,
            "nearest_hospital_km": nearest_hospital_series,
        }
    )
    df.index.name = "county_name"
    df = df.reset_index()

    # Coverage ratio already in [0, 1]; scale to [0, 100]
    df["coverage_score"] = df["coverage_ratio_10km"].clip(0.0, 1.0) * 100

    # Normalise density to [0, 100]
    df["density_score"] = _minmax_scale(df["facilities_per_100k"]) * 100

    # WB score: use under-5 mortality (inverted) and physicians (direct)
    under5 = wb_latest.loc[
        wb_latest["indicator_code"] == "SH.DYN.MORT", "value"
    ]
    under5_val = float(under5.iloc[0]) if not under5.empty else 50.0

    physicians = wb_latest.loc[
        wb_latest["indicator_code"] == "SH.MED.PHYS.ZS", "value"
    ]
    phys_val = float(physicians.iloc[0]) if not physicians.empty else 0.1

    # Inverse-normalise under-5 mortality (lower is better; 200 = worst historical)
    wb_mortality_score = max(0.0, 100.0 - (under5_val / 200.0) * 100.0)
    # Physician density score: 0.1 = practical min, 1.0 = WHO target
    wb_phys_score = min(100.0, (phys_val / 1.0) * 100.0)
    wb_composite = wb_mortality_score * 0.6 + wb_phys_score * 0.4

    # Apply hospital-distance penalty to WB composite (national data, adjusted locally)
    distance_penalty = _minmax_scale(df["nearest_hospital_km"])  # higher = worse
    df["wb_score"] = (wb_composite * (1 - distance_penalty * 0.3)).clip(0, 100)

    # Composite index — clipped to [0, 100]
    df["health_access_index"] = (
        WEIGHT_COVERAGE      * df["coverage_score"]
        + WEIGHT_DENSITY     * df["density_score"]
        + WEIGHT_WB_INDICATORS * df["wb_score"]
    ).clip(0, 100).round(2)

    # Flag underserved: coverage < 50% OR density < 2 per 100k
    df["is_underserved"] = (
        (df["coverage_ratio_10km"] < 0.50)
        | (df["facilities_per_100k"] < 2.0)
    )

    return df


# ---------------------------------------------------------------------------
# Write results to DuckDB
# ---------------------------------------------------------------------------

def write_spatial_results(df: pd.DataFrame, facilities_gdf: gpd.GeoDataFrame) -> None:
    """Write computed spatial metrics to DuckDB for dbt staging.

    Uses a single DuckDB connection throughout to avoid write-lock conflicts.
    """
    conn = get_conn()

    # --- Spatial metrics table ---
    conn.execute("DROP TABLE IF EXISTS raw_spatial_metrics")
    conn.execute("""
        CREATE TABLE raw_spatial_metrics (
            county_name          VARCHAR,
            coverage_ratio_10km  DOUBLE,
            facilities_per_100k  DOUBLE,
            nearest_hospital_km  DOUBLE,
            coverage_score       DOUBLE,
            density_score        DOUBLE,
            wb_score             DOUBLE,
            health_access_index  DOUBLE,
            is_underserved       BOOLEAN
        )
    """)
    conn.execute("INSERT INTO raw_spatial_metrics SELECT * FROM df")
    count = conn.execute("SELECT COUNT(*) FROM raw_spatial_metrics").fetchone()[0]
    logger.info("Wrote %d rows to raw_spatial_metrics", count)

    # --- Facility buffers table (10km, Level 4+ only) ---
    # Build the buffer DataFrame using the already-loaded GeoDataFrame passed in;
    # this avoids opening a second DuckDB connection while the first is still open.
    conn.execute("DROP TABLE IF EXISTS raw_facility_buffers_10km")
    conn.execute("""
        CREATE TABLE raw_facility_buffers_10km (
            facility_id    BIGINT,
            facility_name  VARCHAR,
            facility_level INTEGER,
            county         VARCHAR,
            latitude       DOUBLE,
            longitude      DOUBLE,
            buffer_wkt     VARCHAR
        )
    """)

    hospitals_gdf = facilities_gdf[facilities_gdf["facility_level"] >= 4].copy()

    if not hospitals_gdf.empty:
        # Buffer in UTM 37N (metres), then reproject to WGS84 for WKT storage
        hospitals_utm = hospitals_gdf.to_crs(UTM_CRS)
        hospitals_utm["geometry"] = hospitals_utm["geometry"].buffer(BUFFER_10KM_M)
        hospitals_wgs = hospitals_utm.to_crs(GEO_CRS)

        buf_df = hospitals_gdf[
            ["facility_id", "facility_name", "facility_level", "county", "latitude", "longitude"]
        ].copy().reset_index(drop=True)
        buf_df["buffer_wkt"] = hospitals_wgs.geometry.to_wkt().values

        conn.execute("INSERT INTO raw_facility_buffers_10km SELECT * FROM buf_df")

    count2 = conn.execute("SELECT COUNT(*) FROM raw_facility_buffers_10km").fetchone()[0]
    logger.info("Wrote %d rows to raw_facility_buffers_10km", count2)

    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("=== Spatial Analysis ===")

    facilities_gdf = load_facilities_gdf()
    counties_gdf = load_counties_gdf()
    population_df = load_population()
    wb_latest = load_wb_latest()

    # Coverage at 10km (primary metric for the index) — radius in metres
    coverage_10km = compute_county_coverage(counties_gdf, facilities_gdf, BUFFER_10KM_M)

    # Facility density per 100k population
    density = compute_facility_density(facilities_gdf, counties_gdf, population_df)

    # Nearest Level 4+ hospital distance (km, projected)
    nearest = compute_nearest_hospital_distance(counties_gdf, facilities_gdf)

    # Composite health access index
    index_df = compute_health_access_index(coverage_10km, density, nearest, wb_latest)

    logger.info(
        "Index stats — min=%.1f  max=%.1f  mean=%.1f",
        index_df["health_access_index"].min(),
        index_df["health_access_index"].max(),
        index_df["health_access_index"].mean(),
    )
    underserved_n = int(index_df["is_underserved"].sum())
    logger.info("Underserved counties: %d / %d", underserved_n, len(index_df))

    # Pass facilities_gdf so write_spatial_results can build buffers without a
    # second DuckDB connection (avoids write-lock conflicts)
    write_spatial_results(index_df, facilities_gdf)
    logger.info("Done.")


if __name__ == "__main__":
    main()
