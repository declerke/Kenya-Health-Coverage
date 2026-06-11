"""Ingest World Bank health indicators for Kenya via the public REST API.

Indicators fetched:
  SH.MED.BEDS.ZS  — Hospital beds per 1,000 people
  SH.MED.PHYS.ZS  — Physicians per 1,000 people
  SH.DYN.MORT     — Under-5 mortality rate (per 1,000 live births)
  SH.STA.MMRT     — Maternal mortality ratio (per 100,000 live births)
  SP.POP.TOTL     — Total population (used for facility density denominator)

Data is stored in two tables:
  raw_wb_indicators  — long format (one row per indicator × year)
  raw_county_pop     — county-level population estimates (latest available)
"""

import os
import sys
import time
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils import get_conn, get_logger

logger = get_logger(__name__)

WB_API_BASE = os.getenv("WB_API_BASE", "https://api.worldbank.org/v2")

INDICATORS = {
    "SH.MED.BEDS.ZS": "hospital_beds_per_1000",
    "SH.MED.PHYS.ZS": "physicians_per_1000",
    "SH.DYN.MORT": "under5_mortality_rate",
    "SH.STA.MMRT": "maternal_mortality_ratio",
    "SP.POP.TOTL": "total_population",
}

# Approximate 2023 county-level population (Kenya National Bureau of Statistics
# 2019 Census projected to 2023 at 2.2% annual growth).
# Source: KNBS 2019 Kenya Population and Housing Census, Vol. III
COUNTY_POPULATION_2023 = {
    "Nairobi": 5_765_000,
    "Kiambu": 2_756_000,
    "Nakuru": 2_464_000,
    "Kakamega": 2_050_000,
    "Bungoma": 1_893_000,
    "Meru": 1_737_000,
    "Kilifi": 1_635_000,
    "Machakos": 1_569_000,
    "Murang'a": 1_259_000,
    "Kisumu": 1_236_000,
    "Mombasa": 1_284_000,
    "Siaya": 1_061_000,
    "Nyeri": 962_000,
    "Embu": 672_000,
    "Kitui": 1_274_000,
    "Trans Nzoia": 1_076_000,
    "Uasin Gishu": 1_318_000,
    "Kajiado": 1_286_000,
    "Kirinyaga": 686_000,
    "Nyandarua": 730_000,
    "Makueni": 1_018_000,
    "Laikipia": 596_000,
    "Narok": 1_225_000,
    "Kericho": 963_000,
    "Bomet": 903_000,
    "Homa Bay": 1_246_000,
    "Migori": 1_360_000,
    "Kisii": 1_539_000,
    "Nyamira": 756_000,
    "Busia": 1_012_000,
    "Vihiga": 748_000,
    "Turkana": 1_206_000,
    "West Pokot": 741_000,
    "Samburu": 371_000,
    "Baringo": 726_000,
    "Elgeyo Marakwet": 479_000,
    "Nandi": 940_000,
    "Kwale": 949_000,
    "Tana River": 368_000,
    "Lamu": 187_000,
    "Taita Taveta": 407_000,
    "Garissa": 898_000,
    "Wajir": 862_000,
    "Mandera": 1_259_000,
    "Marsabit": 500_000,
    "Isiolo": 296_000,
    "Tharaka Nithi": 415_000,
}


def fetch_wb_indicator(indicator_code: str) -> list[dict]:
    """Fetch all pages of a World Bank indicator time-series for Kenya."""
    url = f"{WB_API_BASE}/country/KE/indicator/{indicator_code}"
    params = {"format": "json", "per_page": "100", "page": "1"}

    records = []
    page = 1
    while True:
        params["page"] = str(page)
        # Retry up to 3 times with increasing timeout
        last_exc = None
        for attempt, timeout in enumerate([45, 60, 90], start=1):
            try:
                resp = requests.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                last_exc = None
                break
            except requests.exceptions.ReadTimeout as exc:
                logger.warning("  Timeout on %s page %d attempt %d/%d", indicator_code, page, attempt, 3)
                last_exc = exc
                time.sleep(2 * attempt)
        if last_exc is not None:
            logger.error("  Giving up on %s after 3 timeouts", indicator_code)
            break
        payload = resp.json()

        if len(payload) < 2 or not payload[1]:
            break

        meta, data = payload[0], payload[1]
        for item in data:
            if item.get("value") is not None:
                records.append(
                    {
                        "indicator_code": indicator_code,
                        "year": int(item["date"]),
                        "value": float(item["value"]),
                    }
                )

        total_pages = meta.get("pages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.2)  # polite rate limiting

    logger.info(
        "  %s — fetched %d non-null records", indicator_code, len(records)
    )
    return records


def load_wb_indicators() -> pd.DataFrame:
    """Fetch all indicators and return a long-format DataFrame."""
    all_records = []
    for code, label in INDICATORS.items():
        records = fetch_wb_indicator(code)
        for r in records:
            r["indicator_label"] = label
        all_records.extend(records)
        time.sleep(0.3)

    df = pd.DataFrame(all_records, columns=["indicator_code", "indicator_label", "year", "value"])
    df = df.sort_values(["indicator_code", "year"])
    logger.info("Total WB indicator records: %d", len(df))
    return df


def build_county_population_df() -> pd.DataFrame:
    """Return a DataFrame of county-level population estimates."""
    rows = [
        {"county_name": county, "population_2023": pop}
        for county, pop in COUNTY_POPULATION_2023.items()
    ]
    df = pd.DataFrame(rows)
    logger.info("County population table: %d counties", len(df))
    return df


def write_to_duckdb(df_indicators: pd.DataFrame, df_pop: pd.DataFrame) -> None:
    """Write WB indicators and county population to DuckDB."""
    conn = get_conn()

    conn.execute("DROP TABLE IF EXISTS raw_wb_indicators")
    conn.execute("""
        CREATE TABLE raw_wb_indicators (
            indicator_code  VARCHAR,
            indicator_label VARCHAR,
            year            INTEGER,
            value           DOUBLE
        )
    """)
    conn.execute("INSERT INTO raw_wb_indicators SELECT * FROM df_indicators")
    count = conn.execute("SELECT COUNT(*) FROM raw_wb_indicators").fetchone()[0]
    logger.info("Wrote %d rows to raw_wb_indicators", count)

    conn.execute("DROP TABLE IF EXISTS raw_county_population")
    conn.execute("""
        CREATE TABLE raw_county_population (
            county_name     VARCHAR,
            population_2023 BIGINT
        )
    """)
    conn.execute("INSERT INTO raw_county_population SELECT * FROM df_pop")
    count = conn.execute("SELECT COUNT(*) FROM raw_county_population").fetchone()[0]
    logger.info("Wrote %d rows to raw_county_population", count)

    conn.close()


def main() -> None:
    logger.info("=== Ingest: World Bank Indicators ===")
    df_indicators = load_wb_indicators()
    df_pop = build_county_population_df()
    write_to_duckdb(df_indicators, df_pop)
    logger.info("Done.")


if __name__ == "__main__":
    main()
