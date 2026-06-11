"""Kenya Health Facility Coverage — Streamlit + Folium Dashboard.

4 pages:
  1. Coverage Map   — county choropleth + facility dots + 10km buffer toggle
  2. County Ranking — bar chart of 47 counties by health access index
  3. Coverage Gaps  — table of underserved counties with facility gap analysis
  4. Indicators     — WB health indicator time-series charts for Kenya
"""

import os
import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
import duckdb
from shapely import wkt
import json

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Allow running from any working directory
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

_DB_PATH = os.getenv("DUCKDB_PATH", "data/kenya_health.duckdb")
if not Path(_DB_PATH).is_absolute():
    _DB_PATH = str(_project_root / _DB_PATH)

st.set_page_config(
    page_title="Kenya Health Facility Coverage",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Data loading (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_county_health_index() -> pd.DataFrame:
    conn = duckdb.connect(_DB_PATH, read_only=True)
    df = conn.execute("SELECT * FROM county_health_index ORDER BY index_rank").df()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_coverage_gaps() -> pd.DataFrame:
    conn = duckdb.connect(_DB_PATH, read_only=True)
    df = conn.execute("SELECT * FROM coverage_gaps ORDER BY gap_severity_score DESC").df()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_facilities() -> pd.DataFrame:
    conn = duckdb.connect(_DB_PATH, read_only=True)
    df = conn.execute(
        "SELECT facility_id, facility_name, facility_type, facility_level, "
        "county, latitude, longitude FROM stg_facilities"
    ).df()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_wb_timeseries() -> pd.DataFrame:
    conn = duckdb.connect(_DB_PATH, read_only=True)
    df = conn.execute(
        "SELECT indicator_code, indicator_label, year, value "
        "FROM raw_wb_indicators ORDER BY indicator_code, year"
    ).df()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_facility_buffers() -> pd.DataFrame:
    conn = duckdb.connect(_DB_PATH, read_only=True)
    df = conn.execute(
        "SELECT facility_id, facility_name, facility_level, county, "
        "latitude, longitude, buffer_wkt FROM raw_facility_buffers_10km"
    ).df()
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_county_wkt() -> dict[str, str]:
    """Return {county_name: geometry_wkt} mapping."""
    conn = duckdb.connect(_DB_PATH, read_only=True)
    df = conn.execute(
        "SELECT county_name, geometry_wkt FROM raw_counties"
    ).df()
    conn.close()
    return dict(zip(df["county_name"], df["geometry_wkt"]))


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _index_to_colour(value: float) -> str:
    """Map health access index [0, 100] to a hex colour (red → green)."""
    if pd.isna(value):
        return "#cccccc"
    # Use a red-yellow-green gradient
    v = max(0.0, min(1.0, value / 100.0))
    if v < 0.5:
        # Red to yellow
        r = 220
        g = int(v * 2 * 200)
        b = 0
    else:
        # Yellow to green
        r = int((1 - (v - 0.5) * 2) * 220)
        g = 200
        b = 0
    return f"#{r:02x}{g:02x}{b:02x}"


_LEVEL_COLOURS = {
    2: "#a6cee3",  # light blue
    3: "#1f78b4",  # blue
    4: "#33a02c",  # green
    5: "#ff7f00",  # orange
    6: "#e31a1c",  # red
}


def _level_colour(level: int) -> str:
    return _LEVEL_COLOURS.get(int(level), "#888888")


# ---------------------------------------------------------------------------
# Folium map builder
# ---------------------------------------------------------------------------

def build_coverage_map(
    county_index: pd.DataFrame,
    county_wkt: dict[str, str],
    facilities: pd.DataFrame,
    buffers: pd.DataFrame,
    show_facilities: bool = True,
    show_buffers: bool = True,
) -> folium.Map:
    """Build Folium map with county choropleth + optional facility/buffer layers."""

    # Centre on Kenya
    m = folium.Map(
        location=[0.0236, 37.9062],
        zoom_start=6,
        tiles="CartoDB positron",
        prefer_canvas=True,
    )

    # ---- County choropleth layer ----
    county_layer = folium.FeatureGroup(name="County Health Index", show=True)

    index_lookup = county_index.set_index("county_name")

    for county_name, geom_wkt in county_wkt.items():
        try:
            geom = wkt.loads(geom_wkt)
        except Exception:
            continue

        # Get metrics for popup
        if county_name in index_lookup.index:
            row = index_lookup.loc[county_name]
            idx = row["health_access_index"]
            tier = row.get("access_tier", "N/A")
            fac_count = int(row.get("total_facilities", 0))
            nearest = row.get("nearest_hospital_km", 999.0)
            cov_pct = row.get("coverage_pct", 0.0)
            mortality = row.get("under5_mortality_rate", None)
            pop = int(row.get("population_2023", 0))
        else:
            idx = 0.0
            tier = "No Data"
            fac_count = 0
            nearest = 999.0
            cov_pct = 0.0
            mortality = None
            pop = 0

        colour = _index_to_colour(idx)

        mortality_str = f"{mortality:.1f}" if mortality is not None and not pd.isna(mortality) else "N/A"
        pop_str = f"{pop:,}"

        popup_html = f"""
        <div style="font-family: Arial, sans-serif; min-width:200px;">
            <h4 style="margin:0 0 6px 0; color:#2c3e50;">{county_name}</h4>
            <table style="border-collapse:collapse; width:100%;">
                <tr><td><b>Health Access Index</b></td><td style="color:{colour}; font-weight:bold;">{idx:.1f} / 100</td></tr>
                <tr><td><b>Access Tier</b></td><td>{tier}</td></tr>
                <tr><td><b>10km Coverage</b></td><td>{cov_pct:.1f}%</td></tr>
                <tr><td><b>Total Facilities</b></td><td>{fac_count}</td></tr>
                <tr><td><b>Nearest Hospital</b></td><td>{nearest:.1f} km</td></tr>
                <tr><td><b>Population</b></td><td>{pop_str}</td></tr>
                <tr><td><b>Under-5 Mortality</b></td><td>{mortality_str} per 1,000</td></tr>
            </table>
        </div>
        """

        # Convert Shapely geometry to GeoJSON
        geo_j = folium.GeoJson(
            data=geom.__geo_interface__,
            style_function=lambda feat, c=colour: {
                "fillColor": c,
                "color": "#444444",
                "weight": 0.8,
                "fillOpacity": 0.65,
            },
            tooltip=folium.Tooltip(
                f"{county_name} — Index: {idx:.1f}",
                sticky=False,
            ),
            popup=folium.Popup(popup_html, max_width=280),
        )
        geo_j.add_to(county_layer)

    county_layer.add_to(m)

    # ---- Facility dots layer ----
    if show_facilities:
        fac_layer = folium.FeatureGroup(name="Health Facilities", show=True)
        for _, row in facilities.iterrows():
            colour = _level_colour(row["facility_level"])
            folium.CircleMarker(
                location=[row["latitude"], row["longitude"]],
                radius=4,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.8,
                popup=folium.Popup(
                    f"<b>{row['facility_name']}</b><br>"
                    f"Type: {row['facility_type']}<br>"
                    f"Level: {row['facility_level']}<br>"
                    f"County: {row['county']}",
                    max_width=220,
                ),
                tooltip=f"{row['facility_name']} (L{row['facility_level']})",
            ).add_to(fac_layer)
        fac_layer.add_to(m)

    # ---- 10km buffer circles for Level 4+ ----
    if show_buffers and not buffers.empty:
        buf_layer = folium.FeatureGroup(name="10km Catchment Buffers (L4+)", show=False)
        for _, row in buffers.iterrows():
            try:
                geom = wkt.loads(row["buffer_wkt"])
            except Exception:
                continue
            folium.GeoJson(
                data=geom.__geo_interface__,
                style_function=lambda feat: {
                    "fillColor": "#2ecc71",
                    "color": "#27ae60",
                    "weight": 1,
                    "fillOpacity": 0.12,
                },
                tooltip=f"{row['facility_name']} — 10km catchment",
            ).add_to(buf_layer)
        buf_layer.add_to(m)

    # ---- Legend ----
    legend_html = """
    <div style="position:fixed; bottom:40px; right:10px; z-index:9999;
                background:white; padding:10px; border:1px solid #ccc;
                border-radius:6px; font-family:Arial; font-size:12px;">
        <b>Health Access Index</b><br>
        <span style="color:#dc0000;">■</span> 0–20 Critical<br>
        <span style="color:#dc6400;">■</span> 20–40 Low<br>
        <span style="color:#c8c800;">■</span> 40–60 Medium<br>
        <span style="color:#00c800;">■</span> 60–100 High<br>
        <hr style="margin:4px 0;">
        <b>Facility Level</b><br>
        <span style="color:#a6cee3;">●</span> L2 Dispensary<br>
        <span style="color:#1f78b4;">●</span> L3 Health Centre<br>
        <span style="color:#33a02c;">●</span> L4 Hospital<br>
        <span style="color:#ff7f00;">●</span> L5 County Referral<br>
        <span style="color:#e31a1c;">●</span> L6 National Referral<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl(collapsed=False).add_to(m)

    return m


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def page_coverage_map() -> None:
    st.header("Kenya Health Facility Coverage Map")
    st.caption(
        "County choropleth shows composite health access index (0–100). "
        "Green = high access, Red = critical. Click a county for details."
    )

    col1, col2 = st.columns([3, 1])
    with col2:
        show_facilities = st.checkbox("Show facility locations", value=True)
        show_buffers = st.checkbox("Show 10km catchment buffers (Level 4+)", value=False)
        st.markdown("---")
        st.markdown("**Summary**")

    try:
        county_index = load_county_health_index()
        county_wkt = load_county_wkt()
        facilities = load_facilities()
        buffers = load_facility_buffers()
    except Exception as e:
        st.error(f"Could not load data from DuckDB: {e}")
        st.info("Run the pipeline first: `python src/ingest_facilities.py && python src/ingest_boundaries.py && python src/ingest_worldbank.py && python src/spatial_analysis.py && dbt run --project-dir dbt --profiles-dir dbt`")
        return

    with col2:
        n_counties = len(county_index)
        n_facilities = len(facilities)
        n_underserved = len(county_index[county_index["access_tier"].isin(["Low", "Critical"])])
        avg_index = county_index["health_access_index"].mean()

        st.metric("Counties", n_counties)
        st.metric("Facilities", f"{n_facilities:,}")
        st.metric("Underserved Counties", n_underserved)
        st.metric("Avg Access Index", f"{avg_index:.1f}")

    with col1:
        folium_map = build_coverage_map(
            county_index, county_wkt, facilities, buffers,
            show_facilities=show_facilities,
            show_buffers=show_buffers,
        )
        st_folium(folium_map, width=900, height=600)


def page_county_ranking() -> None:
    st.header("County Health Access Ranking")
    st.caption("All 47 Kenya counties ranked by composite health access index (0–100). Higher is better.")

    try:
        df = load_county_health_index()
    except Exception as e:
        st.error(f"Could not load data: {e}")
        return

    # Bar chart
    import streamlit as st

    tier_colours = {
        "High": "#27ae60",
        "Medium": "#f39c12",
        "Low": "#e67e22",
        "Critical": "#c0392b",
    }
    df_sorted = df.sort_values("health_access_index", ascending=True)
    colours = [tier_colours.get(t, "#888888") for t in df_sorted["access_tier"]]

    import altair as alt

    chart_df = df_sorted[["county_name", "health_access_index", "access_tier"]].copy()
    chart_df.columns = ["County", "Health Access Index", "Tier"]

    colour_scale = alt.Scale(
        domain=["High", "Medium", "Low", "Critical"],
        range=["#27ae60", "#f39c12", "#e67e22", "#c0392b"],
    )

    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            x=alt.X("Health Access Index:Q", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("County:N", sort="-x"),
            color=alt.Color("Tier:N", scale=colour_scale),
            tooltip=["County", "Health Access Index", "Tier"],
        )
        .properties(height=700, title="Kenya County Health Access Index")
        .interactive()
    )
    st.altair_chart(chart, use_container_width=True)

    st.subheader("Top 10 & Bottom 10 Counties")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Top 10 (Highest Access)**")
        top10 = df[["county_name", "health_access_index", "access_tier", "total_facilities"]].head(10)
        top10.columns = ["County", "Index", "Tier", "Facilities"]
        st.dataframe(top10, use_container_width=True, hide_index=True)
    with c2:
        st.markdown("**Bottom 10 (Lowest Access)**")
        bot10 = df[["county_name", "health_access_index", "access_tier", "total_facilities"]].tail(10)
        bot10.columns = ["County", "Index", "Tier", "Facilities"]
        st.dataframe(bot10, use_container_width=True, hide_index=True)


def page_coverage_gaps() -> None:
    st.header("Coverage Gaps — Underserved Counties")
    st.caption(
        "Counties flagged as underserved: 10km coverage < 50% OR "
        "facility density < 2 per 100,000 population."
    )

    try:
        df = load_coverage_gaps()
    except Exception as e:
        st.error(f"Could not load data: {e}")
        return

    if df.empty:
        st.success("No underserved counties detected.")
        return

    # Summary metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Underserved Counties", len(df))
    c2.metric("Facilities Needed", int(df["facilities_needed"].sum()))
    c3.metric("Avg Coverage", f"{df['coverage_pct'].mean():.1f}%")
    c4.metric("Worst County", df.iloc[0]["county_name"])

    st.markdown("---")

    # Priority table
    display_cols = {
        "county_name": "County",
        "access_tier": "Tier",
        "health_access_index": "Access Index",
        "coverage_pct": "10km Coverage %",
        "facilities_per_100k": "Facilities/100k",
        "nearest_hospital_km": "Nearest Hospital (km)",
        "total_facilities": "Current Facilities",
        "facilities_needed": "Facilities Needed",
        "gap_severity_score": "Gap Score",
        "population_2023": "Population",
    }
    show_df = df[[c for c in display_cols.keys() if c in df.columns]].rename(columns=display_cols)
    show_df["Population"] = show_df["Population"].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "N/A")

    # Colour rows by tier
    def highlight_tier(row):
        tier_bg = {
            "Critical": "background-color: #fde8e8",
            "Low": "background-color: #fef3e2",
            "Medium": "background-color: #fffde7",
            "High": "background-color: #e8f5e9",
        }
        bg = tier_bg.get(row.get("Tier", ""), "")
        return [bg] * len(row)

    styled = show_df.style.apply(highlight_tier, axis=1).format(
        {
            "Access Index": "{:.1f}",
            "10km Coverage %": "{:.1f}",
            "Facilities/100k": "{:.2f}",
            "Nearest Hospital (km)": "{:.1f}",
            "Gap Score": "{:.1f}",
        }
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    st.info(
        "**Methodology:** Gap Severity Score = (1 - coverage_ratio) × 50 + "
        "max(0, 2 - facilities_per_100k) × 25 + min(nearest_hospital_km / 100, 1) × 25"
    )


def page_indicators() -> None:
    st.header("World Bank Health Indicators — Kenya")
    st.caption("National-level time-series data from the World Bank Open Data API.")

    try:
        df = load_wb_timeseries()
        index_df = load_county_health_index()
    except Exception as e:
        st.error(f"Could not load data: {e}")
        return

    import altair as alt

    indicator_labels = {
        "SH.MED.BEDS.ZS": "Hospital Beds per 1,000 People",
        "SH.MED.PHYS.ZS": "Physicians per 1,000 People",
        "SH.DYN.MORT": "Under-5 Mortality Rate (per 1,000 live births)",
        "SH.STA.MMRT": "Maternal Mortality Ratio (per 100,000 live births)",
        "SP.POP.TOTL": "Total Population",
    }

    selected_code = st.selectbox(
        "Select indicator",
        options=list(indicator_labels.keys()),
        format_func=lambda c: indicator_labels.get(c, c),
    )

    ind_df = df[df["indicator_code"] == selected_code].copy()

    if ind_df.empty:
        st.warning(f"No data available for {selected_code}")
    else:
        latest_year = int(ind_df["year"].max())
        latest_val = float(ind_df.loc[ind_df["year"] == latest_year, "value"].iloc[0])

        col1, col2 = st.columns([2, 1])
        with col2:
            st.metric(
                f"Latest Value ({latest_year})",
                f"{latest_val:,.2f}" if latest_val < 1000 else f"{latest_val:,.0f}",
            )
            n_years = ind_df["year"].nunique()
            st.metric("Years of Data", n_years)

        with col1:
            chart = (
                alt.Chart(ind_df)
                .mark_line(point=True, strokeWidth=2)
                .encode(
                    x=alt.X("year:O", title="Year"),
                    y=alt.Y(
                        "value:Q",
                        title=indicator_labels.get(selected_code, "Value"),
                        scale=alt.Scale(zero=False),
                    ),
                    tooltip=[
                        alt.Tooltip("year:O", title="Year"),
                        alt.Tooltip("value:Q", title="Value", format=",.2f"),
                    ],
                )
                .properties(
                    height=350,
                    title=indicator_labels.get(selected_code, selected_code),
                )
                .interactive()
            )
            st.altair_chart(chart, use_container_width=True)

    # Summary snapshot from latest WB data
    st.markdown("---")
    st.subheader("Kenya Health Snapshot (Latest Available)")

    wb_wide_row = index_df.iloc[0] if not index_df.empty else {}

    snap_data = {
        "Indicator": [
            "Hospital Beds per 1,000",
            "Physicians per 1,000",
            "Under-5 Mortality Rate",
            "Maternal Mortality Ratio",
        ],
        "Latest Value": [
            f"{wb_wide_row.get('hospital_beds_per_1000', 'N/A'):.2f}" if pd.notna(wb_wide_row.get("hospital_beds_per_1000")) else "N/A",
            f"{wb_wide_row.get('physicians_per_1000', 'N/A'):.3f}" if pd.notna(wb_wide_row.get("physicians_per_1000")) else "N/A",
            f"{wb_wide_row.get('under5_mortality_rate', 'N/A'):.1f}" if pd.notna(wb_wide_row.get("under5_mortality_rate")) else "N/A",
            f"{wb_wide_row.get('maternal_mortality_ratio', 'N/A'):.0f}" if pd.notna(wb_wide_row.get("maternal_mortality_ratio")) else "N/A",
        ],
        "Year": [
            wb_wide_row.get("hospital_beds_year", "N/A"),
            wb_wide_row.get("physicians_year", "N/A"),
            wb_wide_row.get("under5_mortality_year", "N/A"),
            wb_wide_row.get("maternal_mortality_year", "N/A"),
        ],
        "Source": ["World Bank"] * 4,
    }
    st.dataframe(pd.DataFrame(snap_data), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def main() -> None:
    st.sidebar.title("Kenya Health Coverage")
    st.sidebar.markdown("---")

    pages = {
        "Coverage Map": page_coverage_map,
        "County Ranking": page_county_ranking,
        "Coverage Gaps": page_coverage_gaps,
        "Indicators": page_indicators,
    }

    page = st.sidebar.radio("Navigate", list(pages.keys()))
    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Data: Kenya MOH via energydata.info, GADM 4.1, World Bank API. "
        "Spatial analysis: Shapely + GeoPandas. "
        "Pipeline: dbt-DuckDB."
    )

    pages[page]()


if __name__ == "__main__":
    main()
