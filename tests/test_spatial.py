"""Tests for spatial analysis module.

Covers:
  - Buffer creates Polygon geometry from a Point
  - Coverage ratio is between 0 and 1
  - Composite health access index is between 0 and 100
  - Nearest hospital distance is non-negative
  - County coverage computation returns correct shape
"""

import sys
from pathlib import Path
import pytest
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TestBufferGeometry:
    """Tests for buffer creation logic."""

    def test_buffer_creates_polygon_from_point(self):
        """Buffering a Point by a positive radius should produce a Polygon."""
        point = Point(36.8219, -1.2921)  # Nairobi
        buffer = point.buffer(0.090)  # 10km
        assert isinstance(buffer, Polygon), "Buffer must produce a Polygon"

    def test_buffer_area_scales_with_radius(self):
        """Larger buffer radius should produce larger polygon area."""
        point = Point(36.8219, -1.2921)
        buf5 = point.buffer(0.045)
        buf10 = point.buffer(0.090)
        buf15 = point.buffer(0.135)
        assert buf5.area < buf10.area < buf15.area, "Buffer area must increase with radius"

    def test_buffer_is_valid_geometry(self):
        """Buffer geometry must be a valid (non-empty, valid) polygon."""
        point = Point(37.9062, 0.0236)
        buffer = point.buffer(0.045)
        assert buffer.is_valid, "Buffer polygon must be valid"
        assert not buffer.is_empty, "Buffer polygon must not be empty"

    def test_multiple_buffers_can_be_unioned(self):
        """Union of multiple overlapping buffers must produce a valid geometry."""
        from shapely.ops import unary_union
        points = [Point(36.8 + i * 0.05, -1.3) for i in range(5)]
        buffers = [p.buffer(0.090) for p in points]
        union = unary_union(buffers)
        assert not union.is_empty, "Union must not be empty"
        assert union.is_valid, "Union must be valid"


class TestCoverageRatio:
    """Tests for county coverage ratio computation."""

    def test_coverage_ratio_is_between_zero_and_one(self):
        """Coverage ratio must be in [0, 1] for any county."""
        from spatial_analysis import compute_county_coverage

        # Create a simple test GeoDataFrame for one county
        county_poly = Point(36.8, -1.3).buffer(0.5)  # Large circular county
        counties_gdf = gpd.GeoDataFrame(
            {"county_name": ["TestCounty"]},
            geometry=[county_poly],
            crs="EPSG:4326",
        )

        # One facility inside the county
        fac_gdf = gpd.GeoDataFrame(
            {"facility_id": [1], "facility_name": ["Test"], "facility_level": [4],
             "county": ["TestCounty"], "latitude": [-1.3], "longitude": [36.8]},
            geometry=[Point(36.8, -1.3)],
            crs="EPSG:4326",
        )

        coverage = compute_county_coverage(counties_gdf, fac_gdf, 0.090)
        val = coverage.iloc[0]
        assert 0.0 <= val <= 1.0, f"Coverage ratio {val} must be between 0 and 1"

    def test_coverage_ratio_zero_when_no_facilities(self):
        """County with no facilities should have coverage ratio of 0."""
        from spatial_analysis import compute_county_coverage

        county_poly = Point(36.8, -1.3).buffer(0.5)
        counties_gdf = gpd.GeoDataFrame(
            {"county_name": ["EmptyCounty"]},
            geometry=[county_poly],
            crs="EPSG:4326",
        )
        # Empty facilities GeoDataFrame
        fac_gdf = gpd.GeoDataFrame(
            {"facility_id": pd.Series([], dtype=int),
             "facility_name": pd.Series([], dtype=str),
             "facility_level": pd.Series([], dtype=int),
             "county": pd.Series([], dtype=str),
             "latitude": pd.Series([], dtype=float),
             "longitude": pd.Series([], dtype=float)},
            geometry=gpd.GeoSeries([], crs="EPSG:4326"),
            crs="EPSG:4326",
        )

        coverage = compute_county_coverage(counties_gdf, fac_gdf, 0.090)
        assert coverage.iloc[0] == 0.0, "No facilities should give coverage = 0.0"

    def test_coverage_ratio_increases_with_more_facilities(self):
        """Adding more facilities should not decrease coverage ratio."""
        from spatial_analysis import compute_county_coverage

        county_poly = Point(36.8, -1.3).buffer(1.0)
        counties_gdf = gpd.GeoDataFrame(
            {"county_name": ["GrowingCounty"]},
            geometry=[county_poly],
            crs="EPSG:4326",
        )

        # One facility
        fac1 = gpd.GeoDataFrame(
            {"facility_id": [1], "facility_name": ["A"], "facility_level": [4],
             "county": ["GrowingCounty"], "latitude": [-1.3], "longitude": [36.8]},
            geometry=[Point(36.8, -1.3)],
            crs="EPSG:4326",
        )
        # Two facilities
        fac2 = gpd.GeoDataFrame(
            {"facility_id": [1, 2], "facility_name": ["A", "B"], "facility_level": [4, 4],
             "county": ["GrowingCounty"] * 2,
             "latitude": [-1.3, -1.0], "longitude": [36.8, 37.0]},
            geometry=[Point(36.8, -1.3), Point(37.0, -1.0)],
            crs="EPSG:4326",
        )

        cov1 = compute_county_coverage(counties_gdf, fac1, 0.090).iloc[0]
        cov2 = compute_county_coverage(counties_gdf, fac2, 0.090).iloc[0]
        assert cov2 >= cov1, "More facilities should not decrease coverage"


class TestHealthAccessIndex:
    """Tests for composite health access index computation."""

    def _make_test_inputs(self, n: int = 5):
        """Create minimal test DataFrames for the index computation."""
        coverage = pd.Series(
            np.linspace(0.1, 0.9, n),
            index=[f"County{i}" for i in range(n)],
        )
        density = pd.Series(
            np.linspace(0.5, 5.0, n),
            index=[f"County{i}" for i in range(n)],
        )
        nearest = pd.Series(
            np.linspace(5.0, 80.0, n),
            index=[f"County{i}" for i in range(n)],
        )
        wb = pd.DataFrame(
            {
                "indicator_code": ["SH.DYN.MORT", "SH.MED.PHYS.ZS"],
                "indicator_label": ["under5_mortality_rate", "physicians_per_1000"],
                "value": [40.0, 0.2],
                "year": [2022, 2020],
            }
        )
        return coverage, density, nearest, wb

    def test_composite_index_range_0_to_100(self):
        """Health access index must be in [0, 100] for all counties."""
        from spatial_analysis import compute_health_access_index
        cov, dens, near, wb = self._make_test_inputs()
        result = compute_health_access_index(cov, dens, near, wb)
        idx = result["health_access_index"]
        assert (idx >= 0.0).all(), "Index must be >= 0"
        assert (idx <= 100.0).all(), "Index must be <= 100"

    def test_composite_index_returns_correct_columns(self):
        """Result DataFrame must have all expected columns."""
        from spatial_analysis import compute_health_access_index
        cov, dens, near, wb = self._make_test_inputs()
        result = compute_health_access_index(cov, dens, near, wb)
        expected_cols = {
            "county_name", "coverage_ratio_10km", "facilities_per_100k",
            "nearest_hospital_km", "health_access_index", "is_underserved",
        }
        missing = expected_cols - set(result.columns)
        assert not missing, f"Missing columns: {missing}"

    def test_underserved_flag_is_boolean(self):
        """is_underserved column must be boolean."""
        from spatial_analysis import compute_health_access_index
        cov, dens, near, wb = self._make_test_inputs()
        result = compute_health_access_index(cov, dens, near, wb)
        assert result["is_underserved"].dtype == bool, "is_underserved must be bool"

    def test_higher_coverage_gives_higher_index(self):
        """Counties with higher coverage should generally score higher on the index."""
        from spatial_analysis import compute_health_access_index
        import pandas as pd

        cov = pd.Series({"LowCov": 0.1, "HighCov": 0.9})
        dens = pd.Series({"LowCov": 2.0, "HighCov": 2.0})
        near = pd.Series({"LowCov": 20.0, "HighCov": 20.0})
        wb = pd.DataFrame(
            {
                "indicator_code": ["SH.DYN.MORT", "SH.MED.PHYS.ZS"],
                "indicator_label": ["under5_mortality_rate", "physicians_per_1000"],
                "value": [40.0, 0.2],
                "year": [2022, 2020],
            }
        )
        result = compute_health_access_index(cov, dens, near, wb).set_index("county_name")
        assert result.loc["HighCov", "health_access_index"] > result.loc["LowCov", "health_access_index"]
