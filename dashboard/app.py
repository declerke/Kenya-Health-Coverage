"""Kenya Health Facility Coverage — Streamlit + Folium Dashboard.

4 pages:
  1. Coverage Map   — county choropleth + facility dots + 10km buffer toggle
  2. County Ranking — bar chart of 47 counties by health access index
  3. Coverage Gaps  — table of underserved counties with facility gap analysis
  4. Indicators     — WB health indicator time-series charts for Kenya
"""

import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import folium
from streamlit_folium import st_folium
import duckdb
from shapely import wkt
import plotly.express as px
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Allow running from any working directory
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

from utils import get_db_path
_DB_PATH = str(get_db_path())

st.set_page_config(
    page_title="Kenya Health Facility Coverage",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

BG     = "#060b17"
CARD   = "#0d1929"
ACCENT = "#00d26a"
GOLD   = "#f5a623"
RED    = "#e53e3e"
TEXT   = "#e2e8f0"
MUTED  = "#8896a5"
BLUE   = "#4299e1"

# ---------------------------------------------------------------------------
# Global CSS
# ---------------------------------------------------------------------------

st.markdown(f"""
<style>
html, body, [data-testid="stAppViewContainer"], .main {{
    background-color: {BG} !important; color: {TEXT} !important;
}}
[data-testid="stSidebar"] {{ background-color: {CARD} !important; }}
[data-testid="collapsedControl"],[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapsedControl"],button[aria-label="Close sidebar"],
button[aria-label="Open sidebar"] {{ display:none !important; }}
span.material-symbols-rounded,span.material-symbols-outlined,span.material-icons {{
    visibility:hidden !important; font-size:0 !important;
}}
[data-testid="stMetric"] {{
    background:{CARD} !important; border:1px solid #1e2d3d !important;
    border-radius:8px !important; padding:16px 20px !important;
}}
[data-testid="stMetricValue"] {{ color:{ACCENT} !important; font-size:1.8rem !important; }}
[data-testid="stMetricLabel"] {{ color:{MUTED} !important; }}
[data-testid="stDataFrame"] {{ background:{CARD} !important; }}
h1, h2, h3, h4 {{ color:{TEXT} !important; }}
hr {{ border-color:#1e2d3d !important; }}
[data-testid="stExpander"] {{ background:{CARD} !important; border:1px solid #1e2d3d !important; border-radius:8px !important; }}
[data-baseweb="radio"] label {{ color:{TEXT} !important; }}
[data-baseweb="select"] * {{ background-color:{CARD} !important; color:{TEXT} !important; }}
iframe[title="streamlit_folium.st_folium"] {{
    height: 600px !important;
    min-height: 600px !important;
    max-height: 600px !important;
}}
</style>
""", unsafe_allow_html=True)

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
# UI helpers
# ---------------------------------------------------------------------------

def section_header(title: str, subtitle: str = "") -> None:
    st.markdown(f"""
    <div style="margin-bottom:16px;">
      <div style="font-size:1.25rem;font-weight:700;color:{TEXT};border-bottom:1px solid #1e2d3d;padding-bottom:8px;">{title}</div>
      {"<div style='color:"+MUTED+";font-size:0.85rem;margin-top:6px;'>"+subtitle+"</div>" if subtitle else ""}
    </div>
    """, unsafe_allow_html=True)


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
) -> folium.Figure:
    """Build Folium map with county choropleth + optional facility/buffer layers."""

    fig = folium.Figure(width="100%", height=600)
    # Centre on Kenya
    m = folium.Map(
        location=[0.0236, 37.9062],
        zoom_start=6,
        tiles="CartoDB positron",
        prefer_canvas=True,
    )
    fig.add_child(m)

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
            nearest = row.get("nearest_hospital_km")
            cov_pct = row.get("coverage_pct", 0.0)
            mortality = row.get("under5_mortality_rate", None)
            pop = int(row.get("population_2023", 0))
        else:
            idx = 0.0
            tier = "No Data"
            fac_count = 0
            nearest = None
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
                <tr><td><b>Nearest Hospital</b></td><td>{"N/A" if nearest is None or pd.isna(nearest) else f"{nearest:.1f} km"}</td></tr>
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

    # ---- Facility dots layer (GeoJSON for performance with ~10k points) ----
    if show_facilities and not facilities.empty:
        fac_layer = folium.FeatureGroup(name="Health Facilities", show=True)

        # Build one GeoJSON FeatureCollection per level so each level
        # gets its own colour without a per-feature Python callback.
        from collections import defaultdict
        level_features: dict[int, list] = defaultdict(list)
        for row in facilities.itertuples(index=False):
            level_features[int(row.facility_level)].append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [row.longitude, row.latitude],
                },
                "properties": {
                    "name": row.facility_name,
                    "ftype": row.facility_type,
                    "level": int(row.facility_level),
                    "county": row.county,
                },
            })

        for level, feats in sorted(level_features.items()):
            colour = _level_colour(level)
            folium.GeoJson(
                {"type": "FeatureCollection", "features": feats},
                name=f"Level {level}",
                marker=folium.CircleMarker(
                    radius=4,
                    color=colour,
                    fill=True,
                    fill_color=colour,
                    fill_opacity=0.8,
                    weight=1,
                ),
                tooltip=folium.GeoJsonTooltip(
                    fields=["name", "ftype", "county"],
                    aliases=["Facility:", "Type:", "County:"],
                    localize=True,
                ),
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

    return fig


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def page_coverage_map() -> None:
    try:
        county_index = load_county_health_index()
        county_wkt   = load_county_wkt()
        facilities   = load_facilities()
        buffers      = load_facility_buffers()
    except Exception as e:
        st.error(f"Could not load data from DuckDB: {e}")
        st.info(
            "Run the pipeline first: "
            "`python src/ingest_facilities.py && python src/ingest_boundaries.py && "
            "python src/ingest_worldbank.py && python src/spatial_analysis.py && "
            "dbt run --project-dir dbt --profiles-dir dbt`"
        )
        return

    n_counties    = len(county_index)
    n_facilities  = len(facilities)
    n_underserved = len(county_index[county_index["access_tier"].isin(["Low", "Critical"])])
    avg_index     = county_index["health_access_index"].mean()

    # Header banner
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{CARD} 0%,#0a2040 100%);
                border-left:4px solid {ACCENT};border-radius:8px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:1.9rem;font-weight:800;color:{TEXT};">🏥 Kenya Health Facility Coverage</div>
      <div style="color:{MUTED};font-size:0.95rem;margin-top:6px;">
        County choropleth · composite health access index · {n_facilities:,} facilities · {n_counties} counties
      </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([3, 1])

    with col2:
        st.markdown(f"<div style='color:{MUTED};font-size:0.8rem;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;'>Map Layers</div>", unsafe_allow_html=True)
        show_facilities = st.checkbox("Show facility locations", value=True)
        show_buffers    = st.checkbox("Show 10km catchment buffers (Level 4+)", value=False)
        st.markdown("---")
        st.markdown(f"<div style='color:{MUTED};font-size:0.8rem;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;'>Summary</div>", unsafe_allow_html=True)
        st.metric("Counties",    n_counties)
        st.metric("Facilities",  f"{n_facilities:,}")
        st.metric("Underserved", n_underserved)
        st.metric("Avg Index",   f"{avg_index:.1f}")

    with col1:
        folium_fig = build_coverage_map(
            county_index, county_wkt, facilities, buffers,
            show_facilities=show_facilities,
            show_buffers=show_buffers,
        )
        components.html(folium_fig._repr_html_(), height=620, scrolling=False)


def page_county_ranking() -> None:
    try:
        df = load_county_health_index()
    except Exception as e:
        st.error(f"Could not load data: {e}")
        return

    n_counties = len(df)

    # Header banner
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{CARD} 0%,#0a2040 100%);
                border-left:4px solid {ACCENT};border-radius:8px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:1.9rem;font-weight:800;color:{TEXT};">📊 County Health Access Ranking</div>
      <div style="color:{MUTED};font-size:0.95rem;margin-top:6px;">
        All {n_counties} Kenya counties ranked by composite health access index (0–100) · higher is better
      </div>
    </div>
    """, unsafe_allow_html=True)

    tier_color_map = {
        "High":     "#00d26a",
        "Medium":   "#f5a623",
        "Low":      "#e67e22",
        "Critical": "#e53e3e",
    }

    chart_df = df.sort_values("health_access_index")[["county_name", "health_access_index", "access_tier"]].copy()
    chart_df["color"] = chart_df["access_tier"].map(tier_color_map).fillna("#8896a5")

    fig = go.Figure(go.Bar(
        x=chart_df["health_access_index"],
        y=chart_df["county_name"],
        orientation="h",
        marker=dict(color=chart_df["color"]),
        text=chart_df["health_access_index"].round(1),
        textposition="outside",
        textfont=dict(color="#e2e8f0", size=10),
        hovertemplate="<b>%{y}</b><br>Index: %{x:.1f}<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#060b17",
        plot_bgcolor="#0d1929",
        height=900,
        margin=dict(l=0, r=40, t=20, b=0),
        xaxis=dict(range=[0, 105], gridcolor="#1e2d3d", title="Health Access Index (0-100)"),
        yaxis=dict(gridcolor="#1e2d3d"),
        showlegend=False,
        title=dict(text="Kenya County Health Access Index", font=dict(color="#e2e8f0", size=15)),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Donut — tier distribution
    tier_counts = df["access_tier"].value_counts().reset_index()
    tier_counts.columns = ["Tier", "Count"]
    fig2 = px.pie(
        tier_counts, names="Tier", values="Count",
        color="Tier", color_discrete_map=tier_color_map,
        hole=0.55, template="plotly_dark",
        title="Access Tier Distribution",
    )
    fig2.update_layout(paper_bgcolor="#060b17", height=300, margin=dict(l=0, r=0, t=40, b=0))
    fig2.update_traces(textposition="inside", textinfo="percent+label")
    st.plotly_chart(fig2, use_container_width=True)

    # Top 10 / Bottom 10 tables
    section_header("Top 10 & Bottom 10 Counties")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"<div style='color:{ACCENT};font-weight:600;margin-bottom:8px;'>Top 10 — Highest Access</div>", unsafe_allow_html=True)
        top10 = df[["county_name", "health_access_index", "access_tier", "total_facilities"]].head(10).copy()
        top10.columns = ["County", "Index", "Tier", "Facilities"]
        st.dataframe(top10, use_container_width=True, hide_index=True)
    with c2:
        st.markdown(f"<div style='color:{RED};font-weight:600;margin-bottom:8px;'>Bottom 10 — Lowest Access</div>", unsafe_allow_html=True)
        bot10 = df[["county_name", "health_access_index", "access_tier", "total_facilities"]].tail(10).copy()
        bot10.columns = ["County", "Index", "Tier", "Facilities"]
        st.dataframe(bot10, use_container_width=True, hide_index=True)


def page_coverage_gaps() -> None:
    try:
        df = load_coverage_gaps()
    except Exception as e:
        st.error(f"Could not load data: {e}")
        return

    # Header banner
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{CARD} 0%,#0a2040 100%);
                border-left:4px solid {GOLD};border-radius:8px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:1.9rem;font-weight:800;color:{TEXT};">⚠️ Coverage Gaps — Underserved Counties</div>
      <div style="color:{MUTED};font-size:0.95rem;margin-top:6px;">
        Counties flagged as underserved: 10km coverage &lt; 50% OR facility density &lt; 2 per 100,000 population
      </div>
    </div>
    """, unsafe_allow_html=True)

    if df.empty:
        st.success("No underserved counties detected.")
        return

    # Summary metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Underserved Counties", len(df))
    c2.metric("Facilities Needed",    int(df["facilities_needed"].sum()))
    c3.metric("Avg Coverage",         f"{df['coverage_pct'].mean():.1f}%")
    c4.metric("Worst County",         df.iloc[0]["county_name"])

    st.markdown("---")

    # Scatter — gap severity vs coverage %
    tier_color_map = {
        "High":     "#00d26a",
        "Medium":   "#f5a623",
        "Low":      "#e67e22",
        "Critical": "#e53e3e",
    }

    fig = px.scatter(
        df,
        x="coverage_pct",
        y="gap_severity_score",
        size="facilities_needed",
        color="access_tier",
        text="county_name",
        color_discrete_map=tier_color_map,
        template="plotly_dark",
        labels={
            "coverage_pct":       "10km Coverage %",
            "gap_severity_score": "Gap Severity Score",
        },
        title="Coverage Gap Analysis",
    )
    fig.update_traces(textposition="top center", textfont=dict(size=9, color="#8896a5"))
    fig.update_layout(
        paper_bgcolor="#060b17",
        plot_bgcolor="#0d1929",
        height=380,
        margin=dict(l=0, r=0, t=40, b=0),
        xaxis=dict(gridcolor="#1e2d3d"),
        yaxis=dict(gridcolor="#1e2d3d"),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Priority table
    section_header("Underserved County Priority Table")
    display_cols = {
        "county_name":           "County",
        "access_tier":           "Tier",
        "health_access_index":   "Access Index",
        "coverage_pct":          "10km Coverage %",
        "facilities_per_100k":   "Facilities/100k",
        "nearest_hospital_km":   "Nearest Hospital (km)",
        "total_facilities":      "Current Facilities",
        "facilities_needed":     "Facilities Needed",
        "gap_severity_score":    "Gap Score",
        "population_2023":       "Population",
    }
    show_df = df[[c for c in display_cols if c in df.columns]].rename(columns=display_cols)
    for col in ["Access Index", "10km Coverage %", "Facilities/100k", "Nearest Hospital (km)", "Gap Score"]:
        if col in show_df.columns:
            show_df[col] = show_df[col].round(2)
    show_df["Population"] = show_df["Population"].apply(
        lambda x: f"{int(x):,}" if pd.notna(x) else "N/A"
    )

    def highlight_tier(row):
        tier_bg = {
            "Critical": "background-color: #fde8e8",
            "Low":      "background-color: #fef3e2",
            "Medium":   "background-color: #fffde7",
            "High":     "background-color: #e8f5e9",
        }
        bg = tier_bg.get(row.get("Tier", ""), "")
        return [bg] * len(row)

    styled = show_df.style.apply(highlight_tier, axis=1).format(
        {
            "Access Index":           "{:.1f}",
            "10km Coverage %":        "{:.1f}",
            "Facilities/100k":        "{:.2f}",
            "Nearest Hospital (km)":  "{:.1f}",
            "Gap Score":              "{:.1f}",
        }
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    st.info(
        "**Methodology:** Gap Severity Score = (1 - coverage_ratio) × 50 + "
        "max(0, 2 - facilities_per_100k) × 25 + min(nearest_hospital_km / 100, 1) × 25"
    )


def page_indicators() -> None:
    try:
        df       = load_wb_timeseries()
        index_df = load_county_health_index()
    except Exception as e:
        st.error(f"Could not load data: {e}")
        return

    # Header banner
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,{CARD} 0%,#0a2040 100%);
                border-left:4px solid {BLUE};border-radius:8px;padding:24px 28px;margin-bottom:20px;">
      <div style="font-size:1.9rem;font-weight:800;color:{TEXT};">🌐 World Bank Health Indicators — Kenya</div>
      <div style="color:{MUTED};font-size:0.95rem;margin-top:6px;">
        National-level time-series data from the World Bank Open Data API
      </div>
    </div>
    """, unsafe_allow_html=True)

    indicator_labels = {
        "SH.MED.BEDS.ZS": "Hospital Beds per 1,000 People",
        "SH.MED.PHYS.ZS": "Physicians per 1,000 People",
        "SH.DYN.MORT":    "Under-5 Mortality Rate (per 1,000 live births)",
        "SH.STA.MMRT":    "Maternal Mortality Ratio (per 100,000 live births)",
        "SP.POP.TOTL":    "Total Population",
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
        latest_val  = float(ind_df.loc[ind_df["year"] == latest_year, "value"].iloc[0])

        col1, col2 = st.columns([2, 1])

        with col2:
            st.metric(
                f"Latest Value ({latest_year})",
                f"{latest_val:,.2f}" if latest_val < 1000 else f"{latest_val:,.0f}",
            )
            n_years = ind_df["year"].nunique()
            st.metric("Years of Data", n_years)

        with col1:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=ind_df["year"],
                y=ind_df["value"],
                mode="lines+markers",
                line=dict(color="#00d26a", width=2),
                marker=dict(color="#00d26a", size=7),
                fill="tozeroy",
                fillcolor="rgba(0,210,106,0.08)",
                hovertemplate="Year: %{x}<br>Value: %{y:,.2f}<extra></extra>",
            ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#060b17",
                plot_bgcolor="#0d1929",
                height=380,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(gridcolor="#1e2d3d", title="Year"),
                yaxis=dict(
                    gridcolor="#1e2d3d",
                    title=indicator_labels.get(selected_code, "Value"),
                    zeroline=False,
                ),
            )
            st.plotly_chart(fig, use_container_width=True)

    # Summary snapshot
    st.markdown("---")
    section_header(
        "Kenya Health Snapshot (Latest Available)",
        "Aggregated from the most recent World Bank data across all counties.",
    )

    wb_wide_row = index_df.iloc[0] if not index_df.empty else {}

    snap_data = {
        "Indicator": [
            "Hospital Beds per 1,000",
            "Physicians per 1,000",
            "Under-5 Mortality Rate",
            "Maternal Mortality Ratio",
        ],
        "Latest Value": [
            f"{wb_wide_row.get('hospital_beds_per_1000', 'N/A'):.2f}"  if pd.notna(wb_wide_row.get("hospital_beds_per_1000"))  else "N/A",
            f"{wb_wide_row.get('physicians_per_1000', 'N/A'):.3f}"     if pd.notna(wb_wide_row.get("physicians_per_1000"))     else "N/A",
            f"{wb_wide_row.get('under5_mortality_rate', 'N/A'):.1f}"   if pd.notna(wb_wide_row.get("under5_mortality_rate"))   else "N/A",
            f"{wb_wide_row.get('maternal_mortality_ratio', 'N/A'):.0f}" if pd.notna(wb_wide_row.get("maternal_mortality_ratio")) else "N/A",
        ],
        "Year": [
            wb_wide_row.get("hospital_beds_year",       "N/A"),
            wb_wide_row.get("physicians_year",          "N/A"),
            wb_wide_row.get("under5_mortality_year",    "N/A"),
            wb_wide_row.get("maternal_mortality_year",  "N/A"),
        ],
        "Source": ["World Bank"] * 4,
    }
    st.dataframe(pd.DataFrame(snap_data), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def main() -> None:
    st.sidebar.markdown(f"""
    <div style="padding:4px 0 16px;border-bottom:1px solid #1e2d3d;margin-bottom:16px;">
      <div style="font-size:1.1rem;font-weight:700;color:{TEXT};">🏥 Health Coverage</div>
      <div style="font-size:0.78rem;color:{MUTED};margin-top:2px;">Kenya 47-County Analysis</div>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.markdown(
        f"<div style='color:{MUTED};font-size:0.8rem;font-weight:600;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;'>Navigate</div>",
        unsafe_allow_html=True,
    )

    pages = {
        "Coverage Map":    page_coverage_map,
        "County Ranking":  page_county_ranking,
        "Coverage Gaps":   page_coverage_gaps,
        "Indicators":      page_indicators,
    }

    page = st.sidebar.radio("", list(pages.keys()), label_visibility="collapsed")

    st.sidebar.markdown(f"""
    <div style="margin-top:24px;padding:12px;background:#060b17;border-radius:6px;border:1px solid #1e2d3d;">
      <div style="font-size:0.75rem;color:{MUTED};line-height:1.7;">
        📍 Kenya MOH via energydata.info<br>
        🗺️ GADM 4.1 county boundaries<br>
        🌐 World Bank Open Data API<br>
        🔬 Shapely + GeoPandas spatial analysis<br>
        💾 dbt-DuckDB pipeline
      </div>
    </div>
    """, unsafe_allow_html=True)

    pages[page]()


if __name__ == "__main__":
    main()
