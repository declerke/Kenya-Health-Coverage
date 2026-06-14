#!/usr/bin/env bash
# Kenya Health Coverage — Full Pipeline Runner (Bash)
# Usage: bash run.sh
# Optional: bash run.sh --dashboard-only

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_ONLY=false

for arg in "$@"; do
    case $arg in
        --dashboard-only) DASHBOARD_ONLY=true ;;
    esac
done

echo ""
echo "======================================================"
echo "  Kenya Health Facility Coverage Pipeline"
echo "======================================================"
echo ""

# Ensure .env exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo ".env created from .env.example"
fi

# Step 1: Virtual environment
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "[1/7] Creating virtual environment with uv..."
    uv venv "$SCRIPT_DIR/.venv" --python 3.11
else
    echo "[1/7] Virtual environment already exists — skipping"
fi

# Activate
source "$SCRIPT_DIR/.venv/bin/activate"

# Step 2: Install dependencies
echo ""
echo "[2/7] Installing dependencies..."
# UV_LINK_MODE=copy avoids cross-device link errors on network-backed filesystems.
UV_LINK_MODE=copy uv pip install -r "$SCRIPT_DIR/requirements.txt"
echo "Dependencies installed."

if [ "$DASHBOARD_ONLY" = true ]; then
    echo ""
    echo "[Dashboard Only] Launching Streamlit..."
    streamlit run "$SCRIPT_DIR/dashboard/app.py"
    exit 0
fi

# Step 3: Ingest facilities
echo ""
echo "[3/7] Ingesting health facilities from energydata.info..."
python "$SCRIPT_DIR/src/ingest_facilities.py"
echo "Facilities ingested."

# Step 4: Ingest boundaries
echo ""
echo "[4/7] Ingesting Kenya county boundaries from GADM 4.1..."
python "$SCRIPT_DIR/src/ingest_boundaries.py"
echo "County boundaries ingested."

# Step 5: Ingest World Bank indicators
echo ""
echo "[5/7] Fetching World Bank health indicators..."
python "$SCRIPT_DIR/src/ingest_worldbank.py"
echo "World Bank indicators fetched."

# Step 6: Spatial analysis
echo ""
echo "[6/7] Running spatial analysis..."
python "$SCRIPT_DIR/src/spatial_analysis.py"
echo "Spatial analysis complete."

# Step 7: dbt run
echo ""
echo "[7/7] Running dbt models..."
# Use absolute path so dbt's env_var() resolves correctly when --project-dir
# changes the effective working directory.
export DUCKDB_PATH="$SCRIPT_DIR/data/kenya_health.duckdb"
dbt run --project-dir "$SCRIPT_DIR/dbt" --profiles-dir "$SCRIPT_DIR/dbt"
echo "dbt models built."

echo ""
echo "[dbt tests] Running dbt tests..."
dbt test --project-dir "$SCRIPT_DIR/dbt" --profiles-dir "$SCRIPT_DIR/dbt" || echo "WARNING: some dbt tests failed"

echo ""
echo "[pytest] Running unit tests..."
python -m pytest "$SCRIPT_DIR/tests" -v --tb=short || echo "WARNING: some pytest tests failed"

echo ""
echo "======================================================"
echo "  Pipeline complete! Launching dashboard..."
echo "  URL: http://localhost:8501"
echo "======================================================"
echo ""

streamlit run "$SCRIPT_DIR/dashboard/app.py"
