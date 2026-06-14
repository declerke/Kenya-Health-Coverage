"""Shared utilities for Kenya Health Coverage pipeline."""

import os
import logging
import hashlib
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str) -> logging.Logger:
    """Return a consistently formatted logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def get_db_path() -> Path:
    """Return the resolved DuckDB file path from .env or default."""
    raw = os.getenv("DUCKDB_PATH", "data/kenya_health.duckdb")
    # Resolve relative to project root (two levels up from src/)
    project_root = Path(__file__).resolve().parent.parent
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    raw = os.getenv("DATA_DIR", "data")
    project_root = Path(__file__).resolve().parent.parent
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_conn() -> duckdb.DuckDBPyConnection:
    """Open (or create) the shared DuckDB connection."""
    db_path = get_db_path()
    return duckdb.connect(str(db_path))


# ---------------------------------------------------------------------------
# Download helper with local caching
# ---------------------------------------------------------------------------

def download_file(url: str, dest: Path, force: bool = False) -> Path:
    """Download *url* to *dest*, skipping if file already exists.

    Uses streaming to handle large files (e.g. the 45 MB GADM GPKG).
    """
    import requests

    logger = get_logger(__name__)

    if dest.exists() and not force:
        logger.info("Cache hit — skipping download: %s", dest.name)
        return dest

    logger.info("Downloading %s → %s", url, dest.name)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    with requests.get(url, stream=True, timeout=120, headers=headers, allow_redirects=True) as resp:
        resp.raise_for_status()
        downloaded = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
                downloaded += len(chunk)
        logger.info(
            "Downloaded %s (%.1f MB)", dest.name, downloaded / 1_048_576
        )

    return dest


# ---------------------------------------------------------------------------
# DuckDB schema helpers
# ---------------------------------------------------------------------------

COUNTY_NAME_MAP: dict[str, str] = {
    # Normalise common spelling variants from source data to match GADM names
    "NAIROBI CITY": "Nairobi",
    "NAIROBI": "Nairobi",
    "MOMBASA": "Mombasa",
    "KWALE": "Kwale",
    "KILIFI": "Kilifi",
    "TANA RIVER": "Tana River",
    "LAMU": "Lamu",
    "TAITA/TAVETA": "Taita Taveta",
    "TAITA TAVETA": "Taita Taveta",
    "GARISSA": "Garissa",
    "WAJIR": "Wajir",
    "MANDERA": "Mandera",
    "MARSABIT": "Marsabit",
    "ISIOLO": "Isiolo",
    "MERU": "Meru",
    "THARAKA-NITHI": "Tharaka Nithi",
    "THARAKA NITHI": "Tharaka Nithi",
    "EMBU": "Embu",
    "KITUI": "Kitui",
    "MACHAKOS": "Machakos",
    "MAKUENI": "Makueni",
    "NYANDARUA": "Nyandarua",
    "NYERI": "Nyeri",
    "KIRINYAGA": "Kirinyaga",
    "MURANG'A": "Murang'a",
    "MURANGA": "Murang'a",
    "KIAMBU": "Kiambu",
    "TURKANA": "Turkana",
    "WEST POKOT": "West Pokot",
    "SAMBURU": "Samburu",
    "TRANS NZOIA": "Trans Nzoia",
    "UASIN GISHU": "Uasin Gishu",
    "ELGEYO/MARAKWET": "Elgeyo Marakwet",
    "ELGEYO MARAKWET": "Elgeyo Marakwet",
    "NANDI": "Nandi",
    "BARINGO": "Baringo",
    "LAIKIPIA": "Laikipia",
    "NAKURU": "Nakuru",
    "NAROK": "Narok",
    "KAJIADO": "Kajiado",
    "KERICHO": "Kericho",
    "BOMET": "Bomet",
    "KAKAMEGA": "Kakamega",
    "VIHIGA": "Vihiga",
    "BUNGOMA": "Bungoma",
    "BUSIA": "Busia",
    "SIAYA": "Siaya",
    "KISUMU": "Kisumu",
    "HOMA BAY": "Homa Bay",
    "MIGORI": "Migori",
    "KISII": "Kisii",
    "NYAMIRA": "Nyamira",
}


def normalise_county(name: str) -> str:
    """Convert raw county name to canonical title-case form matching GADM."""
    if not name:
        return ""
    key = name.strip().upper()
    return COUNTY_NAME_MAP.get(key, name.strip().title())
