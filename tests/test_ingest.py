"""Tests for data ingestion modules.

Covers:
  - Facility CSV has expected lat/lon columns
  - GADM GeoDataFrame has exactly 47 counties
  - World Bank API returns non-empty DataFrame
  - Facility data loads into DuckDB with expected schema
  - County data loads into DuckDB with valid centroid coordinates
"""

import sys
from pathlib import Path
import pytest
import pandas as pd
import geopandas as gpd

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Facility ingest tests
# ---------------------------------------------------------------------------

class TestFacilityIngest:
    """Tests for src/ingest_facilities.py."""

    def test_load_facilities_returns_dataframe(self):
        """load_facilities() should return a non-empty pandas DataFrame."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        assert isinstance(df, pd.DataFrame), "Result must be a DataFrame"
        assert len(df) > 0, "Facilities DataFrame must be non-empty"

    def test_facilities_has_lat_lon_columns(self):
        """Facilities DataFrame must contain latitude and longitude columns."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        assert "latitude" in df.columns, "latitude column must exist"
        assert "longitude" in df.columns, "longitude column must exist"

    def test_facilities_lat_lon_are_numeric(self):
        """Latitude and longitude columns must be numeric (float64)."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        assert pd.api.types.is_float_dtype(df["latitude"]), "latitude must be float"
        assert pd.api.types.is_float_dtype(df["longitude"]), "longitude must be float"

    def test_facilities_lat_lon_within_kenya_bounds(self):
        """All facilities must fall within Kenya's bounding box."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        assert df["latitude"].between(-5.0, 5.0).all(), "latitude must be in Kenya bounds"
        assert df["longitude"].between(33.9, 41.9).all(), "longitude must be in Kenya bounds"

    def test_facilities_has_required_columns(self):
        """Facilities DataFrame must have all required columns."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        required = {"facility_id", "facility_name", "facility_type", "facility_level", "county"}
        missing = required - set(df.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_facility_types_are_valid(self):
        """facility_type must only contain known controlled vocabulary values."""
        from ingest_facilities import load_facilities, KNOWN_TYPES
        df = load_facilities()
        unknown = set(df["facility_type"].unique()) - KNOWN_TYPES
        assert not unknown, f"Unknown facility types found: {unknown}"

    def test_facility_levels_in_valid_range(self):
        """facility_level must be between 2 and 6 inclusive."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        invalid = df[~df["facility_level"].between(2, 6)]
        assert len(invalid) == 0, f"{len(invalid)} rows have invalid facility_level"

    def test_facility_ids_are_unique(self):
        """facility_id must be unique across all rows."""
        from ingest_facilities import load_facilities
        df = load_facilities()
        assert df["facility_id"].is_unique, "facility_id must be unique"


# ---------------------------------------------------------------------------
# Boundaries ingest tests
# ---------------------------------------------------------------------------

class TestBoundariesIngest:
    """Tests for src/ingest_boundaries.py."""

    def test_load_counties_returns_geodataframe(self):
        """load_counties() should return a GeoDataFrame."""
        from ingest_boundaries import load_counties
        gdf = load_counties()
        assert isinstance(gdf, gpd.GeoDataFrame), "Result must be a GeoDataFrame"

    def test_counties_has_47_rows(self):
        """Kenya has exactly 47 counties; GADM level-1 must return 47 rows."""
        from ingest_boundaries import load_counties
        gdf = load_counties()
        assert len(gdf) == 47, f"Expected 47 counties, got {len(gdf)}"

    def test_counties_has_valid_geometry(self):
        """All county geometries must be valid (non-null, non-empty polygons)."""
        from ingest_boundaries import load_counties
        gdf = load_counties()
        null_geom = gdf["geometry"].isna().sum()
        assert null_geom == 0, f"{null_geom} counties have null geometry"
        invalid = (~gdf["geometry"].is_valid).sum()
        assert invalid == 0, f"{invalid} counties have invalid geometry"

    def test_counties_centroid_within_kenya(self):
        """County centroids must be within Kenya's bounding box."""
        from ingest_boundaries import load_counties
        gdf = load_counties()
        assert gdf["centroid_lat"].between(-5.0, 5.0).all()
        assert gdf["centroid_lon"].between(33.9, 41.9).all()

    def test_counties_area_is_positive(self):
        """County areas must all be positive (non-zero km²)."""
        from ingest_boundaries import load_counties
        gdf = load_counties()
        assert (gdf["area_km2"] > 0).all(), "All county areas must be > 0"


# ---------------------------------------------------------------------------
# World Bank ingest tests
# ---------------------------------------------------------------------------

class TestWorldBankIngest:
    """Tests for src/ingest_worldbank.py."""

    def test_wb_api_returns_non_empty_dataframe(self):
        """World Bank API must return a non-empty DataFrame for Kenya."""
        from ingest_worldbank import load_wb_indicators
        df = load_wb_indicators()
        assert isinstance(df, pd.DataFrame), "Result must be a DataFrame"
        assert len(df) > 0, "WB indicators DataFrame must be non-empty"

    def test_wb_has_required_columns(self):
        """WB DataFrame must have indicator_code, year, value columns."""
        from ingest_worldbank import load_wb_indicators
        df = load_wb_indicators()
        for col in ["indicator_code", "year", "value"]:
            assert col in df.columns, f"Missing column: {col}"

    def test_wb_all_indicators_present(self):
        """All 5 required indicator codes must be present in the response."""
        from ingest_worldbank import load_wb_indicators, INDICATORS
        df = load_wb_indicators()
        returned_codes = set(df["indicator_code"].unique())
        for code in INDICATORS.keys():
            assert code in returned_codes, f"Missing indicator: {code}"

    def test_wb_values_are_numeric(self):
        """WB indicator values must be numeric."""
        from ingest_worldbank import load_wb_indicators
        df = load_wb_indicators()
        assert pd.api.types.is_float_dtype(df["value"]), "value must be float"

    def test_wb_years_are_valid(self):
        """WB years must be plausible (1960–2025)."""
        from ingest_worldbank import load_wb_indicators
        df = load_wb_indicators()
        assert df["year"].between(1960, 2025).all(), "All years must be in 1960–2025"
