"""AMFI daily NAV ingestion.

Source: https://www.amfiindia.com/spages/NAVAll.txt (redirects to portal.amfiindia.com)
Free, no authentication, whole industry, ~20M+ historical records.

Real format quirks handled (observed against live data, July 2026):
  * Semicolon-delimited, but section headers are bare lines with no delimiter
  * Two kinds of section header interleaved: scheme TYPE lines, which contain
    "Schemes(" and a category in parentheses, and AMC NAME lines, which do not
  * Separator lines are a single space, not empty
  * Missing ISINs are the literal string "-", not blank
  * Some schemes carry stale NAV dates (dormant bonus options from years ago)
  * NAV can be blank or "N.A." for suspended schemes
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import requests

from fundxray_core.config import settings
from fundxray_core.io import write_parquet
from fundxray_core.identifiers.isin import is_valid
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
RAW_COLS = ["scheme_code", "isin_growth", "isin_reinvest", "scheme_name", "nav", "nav_date"]
SCHEME_TYPE_MARKERS = ("Schemes(", "Scheme(s)(", "Schemes (")


def fetch(url: str = NAV_URL, timeout: int = 90) -> str:
    log.info("fetching %s", url)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def _is_scheme_type_header(line: str) -> bool:
    return any(m in line for m in SCHEME_TYPE_MARKERS)


def parse(text: str) -> pd.DataFrame:
    rows: list[list] = []
    amc: str | None = None
    scheme_type: str | None = None
    skipped = 0

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Scheme Code"):
            continue
        if ";" not in line:
            if _is_scheme_type_header(line):
                scheme_type = line
            else:
                amc = line
            continue
        parts = line.split(";")
        if len(parts) < 6:
            skipped += 1
            continue
        rows.append(parts[:6] + [amc, scheme_type])

    df = pd.DataFrame(rows, columns=RAW_COLS + ["amc_name", "scheme_type"])
    if df.empty:
        return df

    # "-" is AMFI's null marker for ISIN columns.
    for c in ("isin_growth", "isin_reinvest"):
        df[c] = df[c].replace({"-": None, "": None})
        df[c + "_valid"] = df[c].map(is_valid)

    df["nav"] = pd.to_numeric(df["nav"].replace({"N.A.": None, "": None}), errors="coerce")
    df["nav_date"] = pd.to_datetime(df["nav_date"], format="%d-%b-%Y", errors="coerce")
    df["plan_type"] = df["scheme_name"].str.contains(
        r"\bdirect\b", case=False, regex=True, na=False).map({True: "direct", False: "regular"})
    df["is_growth"] = df["scheme_name"].str.contains("growth", case=False, na=False)

    # Category sits inside the parentheses of the scheme-type header.
    df["category"] = (df["scheme_type"].str.extract(r"\((.*?)\)", expand=False)
                        .str.strip())

    before = len(df)
    quarantine = df[df.nav.isna() | df.nav_date.isna()].copy()
    clean = df.dropna(subset=["nav", "nav_date"]).copy()
    clean["ingested_at"] = dt.datetime.now()

    log.info("parsed %d NAV rows | %d quarantined (missing nav/date) | %d malformed lines",
             len(clean), before - len(clean) + 0, skipped)
    if len(quarantine):
        log.warning("quarantined schemes e.g. %s",
                    quarantine.scheme_name.head(3).tolist())
    return clean


def parse_with_quarantine(text: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (clean, quarantined). Bad rows are never silently dropped —
    they are surfaced so the quality layer can count and alert on them."""
    rows, amc, scheme_type = [], None, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Scheme Code"):
            continue
        if ";" not in line:
            if _is_scheme_type_header(line):
                scheme_type = line
            else:
                amc = line
            continue
        parts = line.split(";")
        if len(parts) >= 6:
            rows.append(parts[:6] + [amc, scheme_type])

    raw = pd.DataFrame(rows, columns=RAW_COLS + ["amc_name", "scheme_type"])
    if raw.empty:
        return raw, raw
    nav = pd.to_numeric(raw["nav"].replace({"N.A.": None, "": None}), errors="coerce")
    date = pd.to_datetime(raw["nav_date"], format="%d-%b-%Y", errors="coerce")
    bad_mask = nav.isna() | date.isna()
    return parse(text), raw[bad_mask].copy()


def run(out_dir: Path | None = None, url: str = NAV_URL) -> Path:
    settings.ensure_dirs()
    out_dir = out_dir or settings.warehouse_dir / "fact_nav"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = parse(fetch(url))
    out = out_dir / f"nav_{dt.date.today():%Y%m%d}.parquet"
    write_parquet(df, out)
    log.info("wrote %s (%d rows, %d AMCs, %d categories)",
             out, len(df), df.amc_name.nunique(), df.category.nunique())
    return out


if __name__ == "__main__":
    run()
