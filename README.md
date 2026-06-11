# Kenya Health Facility Coverage

A geospatial analytics pipeline that ingests Kenya Ministry of Health facility GPS data, county administrative boundaries (GADM 4.1), and World Bank health indicators to compute service catchment buffers and population-coverage gaps per county, transformed with dbt (DuckDB), and served as an interactive Folium coverage map via Streamlit.

---

## Pipeline Overview

```
energydata.info (MOH CSV)     GADM 4.1 GeoPackage     World Bank API
       │                              │                       │
  ingest_facilities.py          ingest_boundaries.py   ingest_worldbank.py
       │                              │                       │
       └──────────────┬───────────────┘                       │
                      │                                       │
                      ▼                                       ▼
                 DuckDB (raw_*)               raw_wb_indicators / raw_county_population
                      │
                 spatial_analysis.py  ←  Shapely buffers + coverage ratios
                      │
                      ▼
               dbt (stg → marts)
                      │
                      ▼
             Streamlit + Folium Dashboard
```

---

## Key Metrics

| Metric | Value |
|---|---|
| Health facilities ingested | 9,992 |
| Kenya counties covered | 47 / 47 |
| World Bank indicator records | 200 |
| Level 4+ hospitals (buffer analysis) | 534 |
| Underserved counties flagged | 10 |
| dbt models | 6 (3 staging views + 3 mart tables) |
| dbt tests | 24 / 24 PASS |
| pytest tests | 41 / 41 PASS |
| Health access index range | 32.2 – 86.8 (mean 62.2) |

---

## Spatial Analysis

### Coverage Buffers
For each health facility, Shapely buffer circles are created at 3 radii:

| Radius | Degrees (approx.) | Use |
|---|---|---|
| 5 km | 0.045° | Primary care reach |
| 10 km | 0.090° | County coverage metric |
| 15 km | 0.135° | Extended catchment |

### County Coverage Ratio
`coverage_ratio = area(union(all facility buffers) ∩ county polygon) / county_area`

Counties below 50% coverage at 10km are flagged as underserved.

### Composite Health Access Index (0–100)

| Component | Weight | Source |
|---|---|---|
| 10km coverage ratio | 40% | Shapely spatial union |
| Facilities per 100k population | 30% | MOH count ÷ KNBS population |
| WB health indicators (mortality / physicians) | 30% | World Bank API |

---

## Data Sources

| Source | URL | License |
|---|---|---|
| Kenya MOH Health Facilities | energydata.info (Gov of Kenya, CC-BY 4.0) | CC-BY 4.0 |
| GADM 4.1 Kenya Boundaries | geodata.ucdavis.edu | Academic / non-commercial |
| World Bank Health Indicators | api.worldbank.org/v2 | CC-BY 4.0 |
| County Population (KNBS 2019 Census projected) | Built-in from public census data | Public domain |

---

## World Bank Indicators

| Indicator | Code | Latest Year |
|---|---|---|
| Hospital beds per 1,000 people | SH.MED.BEDS.ZS | 2010 |
| Physicians per 1,000 people | SH.MED.PHYS.ZS | 2019 |
| Under-5 mortality rate | SH.DYN.MORT | 2023 |
| Maternal mortality ratio | SH.STA.MMRT | 2020 |
| Total population | SP.POP.TOTL | 2023 |

---

## dbt Models

```
models/
├── staging/
│   ├── stg_facilities.sql      — 9,992 rows; standardised types, KEPH levels
│   ├── stg_counties.sql        — 47 rows; area_km2, size category
│   └── stg_wb_health.sql       — wide pivot of latest WB indicator values
└── marts/
    ├── facility_coverage.sql   — county-level facility counts + spatial metrics
    ├── county_health_index.sql — composite index, WB indicators, tiers, ranking
    └── coverage_gaps.sql       — underserved counties + gap severity + facilities needed
```

---

## Dashboard (4 Pages)

| Page | Contents |
|---|---|
| Coverage Map | County choropleth (green=high, red=critical) + facility dots + 10km buffer toggle |
| County Ranking | Bar chart of all 47 counties by health access index |
| Coverage Gaps | Table of underserved counties with gap score and facilities needed |
| Indicators | WB health indicator time-series line charts + Kenya snapshot |

---

## Project Structure

```
kenya-health-coverage/
├── dbt/
│   ├── profiles.yml
│   ├── dbt_project.yml
│   └── models/
│       ├── staging/            — 3 views
│       └── marts/              — 3 tables + schema.yml (24 tests)
├── src/
│   ├── ingest_facilities.py
│   ├── ingest_boundaries.py
│   ├── ingest_worldbank.py
│   ├── spatial_analysis.py
│   └── utils.py
├── data/
│   └── .gitkeep
├── dashboard/
│   └── app.py
├── tests/
│   ├── test_ingest.py          — 18 tests
│   ├── test_spatial.py         — 11 tests
│   └── test_dashboard.py       — 12 tests
├── requirements.txt
├── .env.example
├── .gitignore
├── run.ps1
└── run.sh
```

---

## Quick Start

```powershell
# Windows (PowerShell)
cd C:/Users/Administrator/OneDrive/Luxdev/kenya-health-coverage
.\run.ps1
```

```bash
# Linux / macOS
cd kenya-health-coverage
bash run.sh
```

### Manual Steps

```powershell
uv venv .venv --python 3.11
.\.venv\Scripts\Activate.ps1
$env:UV_LINK_MODE = "copy"      # required on OneDrive
uv pip install -r requirements.txt

python src/ingest_facilities.py      # 9,992 facilities → DuckDB
python src/ingest_boundaries.py      # 47 counties GADM → DuckDB
python src/ingest_worldbank.py       # 200 WB records → DuckDB
python src/spatial_analysis.py       # buffers + index → DuckDB

$env:DUCKDB_PATH = "data/kenya_health.duckdb"
dbt run   --project-dir dbt --profiles-dir dbt
dbt test  --project-dir dbt --profiles-dir dbt

python -m pytest tests/ -v

streamlit run dashboard/app.py
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Database | DuckDB (local file) | No server required; dbt-duckdb handles OLAP queries on geospatial WKT natively |
| Geometry storage | WKT in VARCHAR columns | DuckDB lacks native geometry type; Shapely loads WKT at query time in the dashboard |
| Buffer projection | Geographic CRS (degree-based) | Trade-off: simple, no reprojection needed; accepted 1–3% area error at Kenya's equatorial latitudes |
| Population data | KNBS 2019 Census projected to 2023 | World Bank provides national totals only; county-level requires KNBS census data |
| Facility data source | energydata.info (CC-BY 4.0 CSV) | HDX blocked automated access; KMHFL API endpoint returned 404; energydata.info confirmed live with 9,992 records |
| No Airflow | venv-only | Project scope does not require scheduling; reduces operational complexity |

---

## Skills Demonstrated

- Geospatial data engineering: GeoPandas, Shapely, GADM GeoPackage, WKT storage
- Spatial analysis: buffer union coverage ratios, centroid-to-facility distance computation
- Multi-source ingestion: HTTP streaming downloads, REST API pagination, encoding normalization
- dbt-DuckDB: 6 models, 24 tests, staging/mart separation, QUALIFY window functions
- Streamlit + Folium: interactive choropleth map, layer toggles, Altair charts
- Testing: 41 pytest tests across ingest, spatial, and dashboard layers
- Data quality: GPS bounding box validation, controlled vocabulary normalisation, deduplication

---

## County Health Tiers (Sample)

| Tier | Count | Criteria |
|---|---|---|
| High | ~15 | Index ≥ 60 |
| Medium | ~20 | Index 40–60 |
| Low | ~8 | Index 20–40 |
| Critical | ~4 | Index < 20 |
