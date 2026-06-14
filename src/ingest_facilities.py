"""Ingest Kenya health facility data from energydata.info into DuckDB.

Source: Kenya Healthcare Facilities dataset (CC-BY 4.0)
URL: https://energydata.info/dataset/kenya-healthcare-facilities
Fields used: Facility_N, Type, Owner, County, Sub_County, Latitude, Longitude
"""

import os
import sys
from pathlib import Path

import pandas as pd

# Allow running from project root or src/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import get_conn, get_data_dir, get_logger, download_file, normalise_county

logger = get_logger(__name__)

FACILITY_CSV_URL = os.getenv(
    "FACILITY_CSV_URL",
    "https://energydata.info/dataset/7e456b65-1c58-4031-9a91-397787c1334c"
    "/resource/7178b14e-f6fb-4023-8ab2-3fe1ffd7d3ab/download/healthcare_facilities.csv",
)

# Accepted facility type values (normalised)
KNOWN_TYPES = {
    "Dispensary",
    "Health Centre",
    "Hospital",
    "Medical Clinic",
    "Nursing Home",
    "Maternity Home",
    "Eye Clinic",
    "Dental Clinic",
    "Radiology Clinic",
    "Laboratory",
    "Pharmacy",
    "Other",
}


def _normalise_type(raw: str) -> str:
    """Map raw facility type strings to a controlled vocabulary."""
    if not isinstance(raw, str):
        return "Other"
    raw = raw.strip()
    if "Hospital" in raw:
        return "Hospital"
    if "Dispensary" in raw:
        return "Dispensary"
    if "Health Centre" in raw or "Health Center" in raw:
        return "Health Centre"
    if "Nursing Home" in raw or "Maternity" in raw:
        return "Nursing Home"
    if "Medical Clinic" in raw or "Clinic" in raw:
        return "Medical Clinic"
    if "Laboratory" in raw or "Lab" in raw:
        return "Laboratory"
    if "Pharmacy" in raw:
        return "Pharmacy"
    return "Other"


def _assign_level(facility_type: str, owner: str) -> int:
    """Assign a KEPH-like level (2-6) based on type and owner.

    Level mapping (approximate):
      2 = Community health unit / dispensary
      3 = Health centre
      4 = Primary hospital
      5 = Secondary hospital (county referral)
      6 = Tertiary / national referral hospital
    """
    if not isinstance(facility_type, str):
        return 2
    ft = facility_type.strip().lower()
    own = (owner or "").strip().lower()

    if ft == "hospital":
        if "national" in own or "referral" in own:
            return 6
        if "county" in own or "government" in own or "ministry" in own:
            return 5
        return 4
    if ft == "health centre":
        return 3
    if ft in ("dispensary", "nursing home"):
        return 2
    return 2


def load_facilities(force_download: bool = False) -> pd.DataFrame:
    """Download (or load cached) facilities CSV and return cleaned DataFrame."""
    data_dir = get_data_dir()
    dest = data_dir / "healthcare_facilities.csv"

    download_file(FACILITY_CSV_URL, dest, force=force_download)

    # Try UTF-8 first, fall back to latin-1 (which never fails)
    try:
        df = pd.read_csv(dest, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(dest, encoding="latin-1")
    logger.info("Raw CSV rows: %d, columns: %s", len(df), list(df.columns))

    # Rename columns to standard names
    rename_map = {
        "OBJECTID": "object_id",
        "Facility_N": "facility_name",
        "Type": "raw_type",
        "Owner": "owner",
        "County": "county_raw",
        "Sub_County": "sub_county",
        "Division": "division",
        "Location": "location",
        "Sub_Locati": "sub_location",
        "Constituen": "constituency",
        "Nearest_To": "nearest_town",
        "Latitude": "latitude",
        "Longitude": "longitude",
    }
    # Only rename columns that exist
    actual_rename = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=actual_rename)

    # Drop rows without valid GPS
    before = len(df)
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])

    # Kenya bounding box filter (rough): lat -5 to 5, lon 33.9 to 41.9
    df = df[
        (df["latitude"].between(-5.0, 5.0))
        & (df["longitude"].between(33.9, 41.9))
    ]
    after = len(df)
    logger.info("Dropped %d rows with invalid/out-of-bounds GPS → %d remaining", before - after, after)

    # Normalise facility type
    df["facility_type"] = df["raw_type"].apply(_normalise_type)

    # Normalise county name
    df["county"] = df["county_raw"].apply(normalise_county)

    # Assign KEPH level
    df["facility_level"] = df.apply(
        lambda row: _assign_level(row["facility_type"], row.get("owner", "")),
        axis=1,
    )

    # Generate a stable facility_id
    df["facility_id"] = (
        df["facility_name"].fillna("").str.strip().str.lower()
        + "_"
        + df["county"].str.lower()
    ).apply(lambda s: abs(hash(s)) % (10**9))

    # Deduplicate on facility_id (keep first)
    df = df.drop_duplicates(subset=["facility_id"])

    # Select final columns
    keep = [
        "facility_id", "facility_name", "facility_type", "facility_level",
        "owner", "county", "sub_county", "latitude", "longitude",
    ]
    # Only keep columns that actually exist
    keep = [c for c in keep if c in df.columns]
    df = df[keep]

    logger.info("Cleaned facilities: %d rows", len(df))
    return df


def write_to_duckdb(df: pd.DataFrame) -> None:
    """Write cleaned facilities DataFrame to DuckDB raw_facilities table."""
    conn = get_conn()
    conn.execute("DROP TABLE IF EXISTS raw_facilities")
    conn.execute("""
        CREATE TABLE raw_facilities (
            facility_id   BIGINT,
            facility_name VARCHAR,
            facility_type VARCHAR,
            facility_level INTEGER,
            owner         VARCHAR,
            county        VARCHAR,
            sub_county    VARCHAR,
            latitude      DOUBLE,
            longitude     DOUBLE
        )
    """)
    conn.execute("INSERT INTO raw_facilities SELECT * FROM df")
    count = conn.execute("SELECT COUNT(*) FROM raw_facilities").fetchone()[0]
    logger.info("Wrote %d rows to raw_facilities", count)
    conn.close()


def main() -> None:
    logger.info("=== Ingest: Health Facilities ===")
    df = load_facilities()
    write_to_duckdb(df)
    logger.info("Done.")


if __name__ == "__main__":
    main()
