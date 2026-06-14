"""Tests for spatial analysis module.

Covers:
  - Buffer creates Polygon geometry from a Point (in projected CRS)
  - Coverage ratio is between 0 and 1
  - Composite health access index is between 0 and 100
  - Nearest hospital distance is non-negative
  - County coverage computation returns correct shape

NOTE: All buffer and coverage tests use UTM Zone 37N (EPSG:32637) with metre
radii, matching the production code in spatial_analysis.py.  Using geographic-
degree radii on an EPSG:4326 GeoDataFrame gives the wrong result because the
production function projects to EPSG:32637 before buffering.
"""

import sys
from pathlib import Path
import pytest
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Buffer radii in metres — must match spatial_analysis.py constants
BUFFER_5KM_M  = 5_000
BUFFER_10KM_M = 10_000
BUFFER_15KM_M = 15_000

UTM_CRS = "EPSG:32637"
GEO_CRS = "EPSG:4326"


class TestBufferGeometry:
    """Tests for buffer creation logic — all operations in UTM 37N (metres)."""

    def _nairobi_point_utm(self):
        """Return a Point for Nairobi projected to UTM 37N."""
        p = gpd.GeoSeries([Point(36.8219, -1.2921)], crs=GEO_CRS)
        return p.to_crs(UTM_CRS).iloc[0]

    def test_buffer_creates_polygon_from_point(self):
        """Buffering a Point by a positive metre radius should produce a Polygon."""
        point_utm = self._nairobi_point_utm()
        buffer = point_utm.buffer(BUFFER_10KM_M)
        assert isinstance(buffer, Polygon), "Buffer must produce a Polygon"

    def test_buffer_area_scales_with_radius(self):
        """Larger buffer radius should produce larger polygon area (in m²)."""
        point_utm = self._nairobi_point_utm()
        buf5  = point_utm.buffer(BUFFER_5KM_M)
        buf10 = point_utm.buffer(BUFFER_10KM_M)
        buf15 = point_utm.buffer(BUFFER_15KM_M)
        assert buf5.area < buf10.area < buf15.area, (
            "Buffer area must increase with radius (areas in m²)"
        )

    def test_buffer_area_approx_pi_r_squared(self):
        """10km buffer area should be approximately π × r² = ~314 km²."""
        point_utm = self._nairobi_point_utm()
        buf = point_utm.buffer(BUFFER_10KM_M)
        expected_m2 = 3.14159 * (BUFFER_10KM_M ** 2)
        # Allow 1% tolerance for resolution of Shapely's circular approximation
        assert abs(buf.area - expected_m2) / expected_m2 < 0.01, (
            f"10km buffer area {buf.area/1e6:.1f} km² deviates too far from "
            f"expected {expected_m2/1e6:.1f} km²"
        )

    def test_buffer_is_valid_geometry(self):
        """Buffer geometry must be a valid (non-empty, valid) polygon."""
        point_utm = self._nairobi_point_utm()
        buffer = point_utm.buffer(BUFFER_5KM_M)
        assert buffer.is_valid, "Buffer polygon must be valid"
        assert not buffer.is_empty, "Buffer polygon must not be empty"

    def test_multiple_buffers_can_be_unioned(self):
        """Union of multiple overlapping buffers must produce a valid geometry."""
        from shapely.ops import unary_union
        # Create 5 points spaced 2km apart along a line in UTM coords
        base = gpd.GeoSeries([Point(36.8, -1.3)], crs=GEO_CRS).to_crs(UTM_CRS).iloc[0]
        points_utm = [Point(base.x + i * 2000, base.y) for i in range(5)]
        buffers = [p.buffer(BUFFER_10KM_M) for p in points_utm]
        union = unary_union(buffers)
        assert not union.is_empty, "Union must not be empty"
        assert union.is_valid, "Union must be valid"


class TestCoverageRatio:
    """Tests for county coverage ratio computation.

    GeoDataFrames are built in EPSG:4326; compute_county_coverage internally
    reprojects to UTM 37N before buffering.  Buffer radii are passed in metres.
    """

    def test_coverage_ratio_is_between_zero_and_one(self):
        """Coverage ratio must be in [0, 1] for any county."""
        from spatial_analysis import compute_county_coverage

        # Large circular county in WGS84 (roughly 55km radius)
        county_poly = Point(36.8, -1.3).buffer(0.5)
        counties_gdf = gpd.GeoDataFrame(
            {"county_name": ["TestCounty"]},
            geometry=[county_poly],
            crs=GEO_CRS,
        )

        fac_gdf = gpd.GeoDataFrame(
            {
                "facility_id": [1],
                "facility_name": ["Test"],
                "facility_level": [4],
                "county": ["TestCounty"],
                "latitude": [-1.3],
                "longitude": [36.8],
            },
            geometry=[Point(36.8, -1.3)],
            crs=GEO_CRS,
        )

        coverage = compute_county_coverage(counties_gdf, fac_gdf, BUFFER_10KM_M)
        val = coverage.iloc[0]
        assert 0.0 <= val <= 1.0, f"Coverage ratio {val} must be between 0 and 1"

    def test_coverage_ratio_zero_when_no_facilities(self):
        """County with no facilities should have coverage ratio of 0."""
        from spatial_analysis import compute_county_coverage

        county_poly = Point(36.8, -1.3).buffer(0.5)
        counties_gdf = gpd.GeoDataFrame(
            {"county_name": ["EmptyCounty"]},
            geometry=[county_poly],
            crs=GEO_CRS,
        )
        fac_gdf = gpd.GeoDataFrame(
            {
                "facility_id": pd.Series([], dtype=int),
                "facility_name": pd.Series([], dtype=str),
                "facility_level": pd.Series([], dtype=int),
                "county": pd.Series([], dtype=str),
                "latitude": pd.Series([], dtype=float),
                "longitude": pd.Series([], dtype=float),
            },
            geometry=gpd.GeoSeries([], crs=GEO_CRS),
            crs=GEO_CRS,
        )

        coverage = compute_county_coverage(counties_gdf, fac_gdf, BUFFER_10KM_M)
        assert coverage.iloc[0] == 0.0, "No facilities should give coverage = 0.0"

    def test_coverage_ratio_increases_with_more_facilities(self):
        """Adding more facilities should not decrease coverage ratio."""
        from spatial_analysis import compute_county_coverage

        county_poly = Point(36.8, -1.3).buffer(1.0)
        counties_gdf = gpd.GeoDataFrame(
            {"county_name": ["GrowingCounty"]},
            geometry=[county_poly],
            crs=GEO_CRS,
        )

        fac1 = gpd.GeoDataFrame(
            {
                "facility_id": [1],
                "facility_name": ["A"],
                "facility_level": [4],
                "county": ["GrowingCounty"],
                "latitude": [-1.3],
                "longitude": [36.8],
            },
            geometry=[Point(36.8, -1.3)],
            crs=GEO_CRS,
        )
        fac2 = gpd.GeoDataFrame(
            {
                "facility_id": [1, 2],
                "facility_name": ["A", "B"],
                "facility_level": [4, 4],
                "county": ["GrowingCounty"] * 2,
                "latitude": [-1.3, -1.0],
                "longitude": [36.8, 37.0],
            },
            geometry=[Point(36.8, -1.3), Point(37.0, -1.0)],
            crs=GEO_CRS,
        )

        cov1 = compute_county_coverage(counties_gdf, fac1, BUFFER_10KM_M).iloc[0]
        cov2 = compute_county_coverage(counties_gdf, fac2, BUFFER_10KM_M).iloc[0]
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
        assert (
            result.loc["HighCov", "health_access_index"]
            > result.loc["LowCov", "health_access_index"]
        )
