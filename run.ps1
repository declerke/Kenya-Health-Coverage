# Kenya Health Coverage — Full Pipeline Runner (PowerShell)
# Usage: .\run.ps1
# Optional: .\run.ps1 -SkipDownload   (skip re-downloading cached data files)
#           .\run.ps1 -DashboardOnly   (launch dashboard only, assumes pipeline already ran)

param(
    [switch]$SkipDownload,
    [switch]$DashboardOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Kenya Health Facility Coverage Pipeline" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# Verify Python 3 is available
$python = "python"
$pyVersion = & $python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Python not found. Install Python 3.11+." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $pyVersion" -ForegroundColor Green

# Ensure .env exists
if (-not (Test-Path "$ProjectRoot\.env")) {
    Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
    Write-Host ".env created from .env.example" -ForegroundColor Yellow
}

# Step 1: Create virtual environment
if (-not (Test-Path "$ProjectRoot\.venv")) {
    Write-Host ""
    Write-Host "[1/7] Creating virtual environment with uv..." -ForegroundColor Cyan
    uv venv "$ProjectRoot\.venv" --python 3.11
    if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: uv venv failed" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "[1/7] Virtual environment already exists — skipping" -ForegroundColor Gray
}

# Activate venv
$Activate = "$ProjectRoot\.venv\Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
} else {
    Write-Host "WARNING: Could not activate venv; using global Python" -ForegroundColor Yellow
}

# Step 2: Install dependencies
Write-Host ""
Write-Host "[2/7] Installing dependencies..." -ForegroundColor Cyan
# UV_LINK_MODE=copy is required on OneDrive-backed paths (Windows) to avoid
# cross-device link errors when uv tries to hardlink from its cache.
$env:UV_LINK_MODE = "copy"
uv pip install -r "$ProjectRoot\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: dependency installation failed" -ForegroundColor Red; exit 1 }
Write-Host "Dependencies installed." -ForegroundColor Green

if ($DashboardOnly) {
    Write-Host ""
    Write-Host "[Dashboard Only] Launching Streamlit..." -ForegroundColor Cyan
    streamlit run "$ProjectRoot\dashboard\app.py"
    exit 0
}

# Step 3: Ingest facilities
Write-Host ""
Write-Host "[3/7] Ingesting health facilities from energydata.info..." -ForegroundColor Cyan
& $python "$ProjectRoot\src\ingest_facilities.py"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: facility ingest failed" -ForegroundColor Red; exit 1 }
Write-Host "Facilities ingested." -ForegroundColor Green

# Step 4: Ingest boundaries
Write-Host ""
Write-Host "[4/7] Ingesting Kenya county boundaries from GADM 4.1..." -ForegroundColor Cyan
& $python "$ProjectRoot\src\ingest_boundaries.py"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: boundary ingest failed" -ForegroundColor Red; exit 1 }
Write-Host "County boundaries ingested." -ForegroundColor Green

# Step 5: Ingest World Bank indicators
Write-Host ""
Write-Host "[5/7] Fetching World Bank health indicators..." -ForegroundColor Cyan
& $python "$ProjectRoot\src\ingest_worldbank.py"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: World Bank ingest failed" -ForegroundColor Red; exit 1 }
Write-Host "World Bank indicators fetched." -ForegroundColor Green

# Step 6: Spatial analysis
Write-Host ""
Write-Host "[6/7] Running spatial analysis (buffers, coverage, index)..." -ForegroundColor Cyan
& $python "$ProjectRoot\src\spatial_analysis.py"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: spatial analysis failed" -ForegroundColor Red; exit 1 }
Write-Host "Spatial analysis complete." -ForegroundColor Green

# Step 7: dbt run
Write-Host ""
Write-Host "[7/7] Running dbt models..." -ForegroundColor Cyan
# Set DUCKDB_PATH as an absolute path so dbt's env_var() resolves correctly
# regardless of the working directory when --project-dir is used.
$env:DUCKDB_PATH = "$ProjectRoot\data\kenya_health.duckdb"
dbt run --project-dir "$ProjectRoot\dbt" --profiles-dir "$ProjectRoot\dbt"
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: dbt run failed" -ForegroundColor Red; exit 1 }
Write-Host "dbt models built." -ForegroundColor Green

# Run dbt tests (DUCKDB_PATH already set to absolute path above)
Write-Host ""
Write-Host "[dbt tests] Running dbt tests..." -ForegroundColor Cyan
dbt test --project-dir "$ProjectRoot\dbt" --profiles-dir "$ProjectRoot\dbt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: some dbt tests failed (check dbt logs)" -ForegroundColor Yellow
} else {
    Write-Host "All dbt tests passed." -ForegroundColor Green
}

# Run pytest
Write-Host ""
Write-Host "[pytest] Running unit tests..." -ForegroundColor Cyan
& $python -m pytest "$ProjectRoot\tests" -v --tb=short
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: some pytest tests failed (check output above)" -ForegroundColor Yellow
} else {
    Write-Host "All pytest tests passed." -ForegroundColor Green
}

# Launch dashboard
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  Pipeline complete! Launching dashboard..." -ForegroundColor Cyan
Write-Host "  URL: http://localhost:8501" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

streamlit run "$ProjectRoot\dashboard\app.py"
