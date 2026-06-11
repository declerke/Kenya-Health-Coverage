"""Tests for dashboard and DuckDB mart table availability.

Covers:
  - DuckDB mart tables exist after dbt run
  - Folium map object initialises correctly
  - County health index table has the expected columns
  - Coverage gaps table is queryable
  - Facility table in DuckDB has valid GPS coordinates
"""

import sys
import os
from pathlib import Path
import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Load .env if available
_project_root = Path(__file__).resolve().parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)

_DB_PATH = os.getenv("DUCKDB_PATH", "data/kenya_health.duckdb")
if not Path(_DB_PATH).is_absolute():
    _DB_PATH = str(_project_root / _DB_PATH)


def _db_exists() -> bool:
    return Path(_DB_PATH).exists()


def _skip_if_no_db():
    """Pytest skip marker for tests that require a populated DuckDB."""
    return pytest.mark.skipif(
        not _db_exists(),
        reason=f"DuckDB not found at {_DB_PATH}; run the full pipeline first.",
    )


# ---------------------------------------------------------------------------
# Folium map tests (no DB required)
# ---------------------------------------------------------------------------

class TestFoliumMap:
    """Tests that verify Folium map objects initialise correctly."""

    def test_folium_map_initialises(self):
        """A Folium Map object must initialise without error."""
        import folium
        m = folium.Map(location=[0.0236, 37.9062], zoom_start=6)
        assert m is not None, "Folium map must initialise"

    def test_folium_map_has_correct_location(self):
        """Folium map must be centred on Kenya coordinates."""
        import folium
        m = folium.Map(location=[0.0236, 37.9062], zoom_start=6)
        assert m.location == [0.0236, 37.9062], "Map must be centred on Kenya"

    def test_folium_geojson_from_shapely(self):
        """Folium GeoJson layer must accept a Shapely geometry's __geo_interface__."""
        import folium
        from shapely.geometry import Point
        poly = Point(36.8, -1.3).buffer(0.5)
        layer = folium.GeoJson(data=poly.__geo_interface__)
        assert layer is not None, "GeoJson layer must initialise from Shapely geometry"

    def test_folium_circle_marker(self):
        """Folium CircleMarker must initialise with lat/lon coordinates."""
        import folium
        marker = folium.CircleMarker(location=[-1.2921, 36.8219], radius=5)
        assert marker is not None


# ---------------------------------------------------------------------------
# DuckDB mart table tests (require full pipeline)
# ---------------------------------------------------------------------------

@_skip_if_no_db()
class TestDuckDBMarts:
    """Tests that verify dbt-generated mart tables exist and have correct schema."""

    def _conn(self):
        import duckdb
        return duckdb.connect(_DB_PATH, read_only=True)

    def test_county_health_index_table_exists(self):
        """county_health_index mart table must exist in DuckDB after dbt run."""
        conn = self._conn()
        tables = conn.execute("SHOW TABLES").df()["name"].tolist()
        conn.close()
        assert "county_health_index" in tables, "county_health_index table must exist"

    def test_facility_coverage_table_exists(self):
        """facility_coverage mart table must exist."""
        conn = self._conn()
        tables = conn.execute("SHOW TABLES").df()["name"].tolist()
        conn.close()
        assert "facility_coverage" in tables, "facility_coverage table must exist"

    def test_coverage_gaps_table_exists(self):
        """coverage_gaps mart table must exist."""
        conn = self._conn()
        tables = conn.execute("SHOW TABLES").df()["name"].tolist()
        conn.close()
        assert "coverage_gaps" in tables, "coverage_gaps table must exist"

    def test_county_health_index_has_47_rows(self):
        """county_health_index must have 47 rows (one per Kenya county)."""
        conn = self._conn()
        count = conn.execute("SELECT COUNT(*) FROM county_health_index").fetchone()[0]
        conn.close()
        assert count == 47, f"Expected 47 rows in county_health_index, got {count}"

    def test_health_access_index_in_valid_range(self):
        """health_access_index values must all be between 0 and 100."""
        conn = self._conn()
        df = conn.execute("SELECT health_access_index FROM county_health_index").df()
        conn.close()
        assert (df["health_access_index"] >= 0).all()
        assert (df["health_access_index"] <= 100).all()

    def test_county_health_index_has_required_columns(self):
        """county_health_index must have all required dashboard columns."""
        conn = self._conn()
        df = conn.execute("SELECT * FROM county_health_index LIMIT 1").df()
        conn.close()
        required = {
            "county_name", "health_access_index", "access_tier",
            "coverage_ratio_10km", "total_facilities", "nearest_hospital_km",
        }
        missing = required - set(df.columns)
        assert not missing, f"Missing columns in county_health_index: {missing}"

    def test_stg_facilities_has_valid_gps(self):
        """stg_facilities must have valid GPS in Kenya bounds."""
        conn = self._conn()
        df = conn.execute("SELECT latitude, longitude FROM stg_facilities").df()
        conn.close()
        assert len(df) > 0, "stg_facilities must not be empty"
        assert df["latitude"].between(-5.0, 5.0).all(), "All latitudes must be in Kenya"
        assert df["longitude"].between(33.9, 41.9).all(), "All longitudes must be in Kenya"

    def test_coverage_gaps_has_required_columns(self):
        """coverage_gaps table must have key analytical columns."""
        conn = self._conn()
        df = conn.execute("SELECT * FROM coverage_gaps LIMIT 1").df()
        conn.close()
        required = {"county_name", "gap_severity_score", "facilities_needed", "coverage_pct"}
        missing = required - set(df.columns)
        assert not missing, f"Missing columns in coverage_gaps: {missing}"
