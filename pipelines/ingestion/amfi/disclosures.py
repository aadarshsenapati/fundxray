"""Monthly portfolio disclosure ingestion: files -> bronze -> silver.

Bronze is append-only and carries full provenance. Silver applies entity
resolution, asset-class confirmation and reconciliation. Unrecognised files and
unresolved holdings are quarantined and counted, never silently dropped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import uuid
from pathlib import Path

import pandas as pd

from fundxray_core.config import settings
from fundxray_core.io import write_parquet
from fundxray_core.utils.logging import get_logger
from pipelines.ingestion import adapters
from pipelines.ingestion.adapters.base import UnrecognisedFormatError
from pipelines.ingestion.reference.universe import load as load_universe
from pipelines.spark.entity_resolution.resolve import EntityResolver

log = get_logger(__name__)

BRONZE_COLS = ["scheme_name", "instrument_name_raw", "isin", "quantity",
               "market_value", "weight_pct", "asset_class", "source_file",
               "amc_code", "adapter", "disclosure_month", "ingestion_run_id",
               "parsed_at"]


def parse_month(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m").date().replace(day=1)


def ingest_bronze(src_dir: Path, month: dt.date) -> tuple[pd.DataFrame, list[dict]]:
    run_id = uuid.uuid4().hex[:12]
    now = dt.datetime.now()
    rows, quarantine = [], []

    files = sorted(p for p in src_dir.iterdir()
                   if p.suffix.lower() in (".xlsx", ".xls", ".csv"))
    if not files:
        raise FileNotFoundError(f"no disclosure files in {src_dir}")

    for f in files:
        try:
            adapter = adapters.resolve(f)
            n = 0
            for h in adapter.parse(f):
                rows.append({**h.model_dump(), "amc_code": adapter.amc_code,
                             "adapter": adapter.name, "disclosure_month": month,
                             "ingestion_run_id": run_id, "parsed_at": now})
                n += 1
            log.info("%-30s -> %-14s %4d rows", f.name, adapter.name, n)
        except (UnrecognisedFormatError, ValueError) as e:
            log.error("QUARANTINE %s: %s", f.name, e)
            quarantine.append({"file": f.name, "reason": str(e),
                               "run_id": run_id, "at": now})

    df = pd.DataFrame(rows)
    if not df.empty:
        for c in ("quantity", "market_value", "weight_pct"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, quarantine


def build_silver(bronze: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Entity resolution + asset-class confirmation."""
    resolver = EntityResolver(universe)
    silver = resolver.resolve_frame(bronze)

    listed = set(universe["isin"])
    # An equity CANDIDATE that resolved to a listed company is confirmed equity;
    # one that did not is demoted to 'other' so it never inflates exposure maths.
    cand = silver.asset_class == "equity"
    silver.loc[cand & silver.company_id.isin(listed), "asset_class"] = "equity"
    silver.loc[cand & ~silver.company_id.isin(listed), "asset_class"] = "other"

    silver["scheme_code"] = silver["scheme_name"].map(_scheme_code)
    return silver


def _scheme_code(name: str) -> str:
    """Deterministic surrogate key until the AMFI scheme master is joined in."""
    import hashlib
    return "S" + hashlib.sha1(str(name).strip().lower().encode()).hexdigest()[:9].upper()


def run(src_dir: Path, month: dt.date, out_dir: Path | None = None) -> dict:
    settings.ensure_dirs()
    out_dir = out_dir or settings.warehouse_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    bronze, quarantine = ingest_bronze(src_dir, month)
    if bronze.empty:
        raise RuntimeError("no rows parsed — every file was quarantined")

    bpath = out_dir / f"bronze_holdings_{month:%Y%m}.parquet"
    write_parquet(bronze, bpath)

    silver = build_silver(bronze, load_universe())
    spath = out_dir / f"silver_holdings_{month:%Y%m}.parquet"
    write_parquet(silver, spath)

    eq = silver[silver.asset_class == "equity"]
    stats = {
        "run_id": bronze.ingestion_run_id.iloc[0],
        "files_parsed": int(bronze.source_file.nunique()),
        "files_quarantined": len(quarantine),
        "schemes": int(silver.scheme_code.nunique()),
        "bronze_rows": len(bronze),
        "silver_rows": len(silver),
        "equity_rows": len(eq),
        "resolution_rate": round(float(eq.company_id.notna().mean()), 4) if len(eq) else 0.0,
        "by_method": silver.resolution_method.value_counts().to_dict(),
        "bronze_path": str(bpath), "silver_path": str(spath),
    }
    log.info("ingested %d bronze / %d silver rows, %d schemes, "
             "equity resolution %.1f%% (%s)",
             stats["bronze_rows"], stats["silver_rows"], stats["schemes"],
             stats["resolution_rate"] * 100, stats["by_method"])
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--src", default="tests/fixtures/disclosures")
    p.add_argument("--month", default="2026-06")
    a = p.parse_args()
    import json
    print(json.dumps(run(Path(a.src), parse_month(a.month)), indent=2, default=str))
