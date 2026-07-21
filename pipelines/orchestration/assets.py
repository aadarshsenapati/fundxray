"""Dagster software-defined assets.

NOTE: no `from __future__ import annotations` here — Dagster inspects the
`context` parameter's type at runtime, and postponed evaluation turns it into a
string the framework cannot match.

Monthly partitions make backfills first-class: re-running 2016-01 through
2026-06 is a partitioned, resumable run rather than a bespoke script, and a
failure on one month does not poison the rest.
"""
import datetime as dt
from pathlib import Path

import pandas as pd
from dagster import (
    AssetExecutionContext,
    AssetIn,
    MetadataValue,
    MonthlyPartitionsDefinition,
    Output,
    asset,
)

from fundxray_core.config import settings
from fundxray_core.io import write_parquet
from pipelines.ingestion.amfi import disclosures as disc
from pipelines.ingestion.reference.universe import load as load_universe
from pipelines.quality.reconciliation import reconcile
from pipelines.spark.analytics import metrics

MONTHLY = MonthlyPartitionsDefinition(start_date="2024-08-01")
SRC_DIR = Path("tests/fixtures/disclosures")   # swap for the AMFI download dir


def _month(context: AssetExecutionContext) -> dt.date:
    return dt.datetime.strptime(context.partition_key[:7], "%Y-%m").date().replace(day=1)


@asset(partitions_def=MONTHLY, group_name="bronze",
       description="Raw parsed holdings, one partition per disclosure month. "
                   "Append-only, full provenance, no transformation.")
def bronze_holdings(context: AssetExecutionContext) -> Output[pd.DataFrame]:
    month = _month(context)
    df, quarantine = disc.ingest_bronze(SRC_DIR, month)
    path = settings.warehouse_dir / f"bronze_holdings_{month:%Y%m}.parquet"
    write_parquet(df, path)
    return Output(df, metadata={
        "rows": len(df),
        "files_parsed": int(df.source_file.nunique()) if len(df) else 0,
        "files_quarantined": len(quarantine),
        "path": MetadataValue.path(str(path)),
    })


@asset(partitions_def=MONTHLY, group_name="silver",
       ins={"bronze": AssetIn("bronze_holdings")},
       description="Entity-resolved, asset-class-confirmed holdings.")
def silver_holdings(context: AssetExecutionContext, bronze: pd.DataFrame) -> Output[pd.DataFrame]:
    month = _month(context)
    silver = disc.build_silver(bronze, load_universe())
    path = settings.warehouse_dir / f"silver_holdings_{month:%Y%m}.parquet"
    write_parquet(silver, path)

    eq = silver[silver.asset_class == "equity"]
    rate = float(eq.company_id.notna().mean()) if len(eq) else 1.0
    return Output(silver, metadata={
        "rows": len(silver),
        "equity_rows": len(eq),
        "resolution_rate": round(rate, 4),
        "by_method": MetadataValue.json(silver.resolution_method.value_counts().to_dict()),
    })


@asset(partitions_def=MONTHLY, group_name="quality",
       ins={"silver": AssetIn("silver_holdings")},
       description="Quality gates. Failures block promotion to gold.")
def quality_report(context: AssetExecutionContext, silver: pd.DataFrame) -> Output[pd.DataFrame]:
    report = reconcile.run_all(silver)
    failed = report[~report.passed].metric.tolist()
    if failed:
        context.log.error("quality gates failed: %s", failed)
    return Output(report, metadata={
        "all_passed": bool(report.passed.all()),
        "failed": MetadataValue.json(failed),
        "report": MetadataValue.md(report.to_markdown(index=False)),
    })


@asset(group_name="gold",
       description="Cross-partition analytical marts over the full history.")
def gold_marts(context: AssetExecutionContext) -> Output[dict]:
    wh = settings.warehouse_dir
    parts = sorted(wh.glob("silver_holdings_*.parquet"))
    if parts:
        holdings = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    else:
        holdings = pd.read_parquet(wh / "silver_holdings.parquet")

    schemes = pd.read_parquet(wh / "dim_scheme.parquet")
    universe = load_universe()
    prices = pd.read_parquet(wh / "fact_price.parquet")

    crowd = metrics.crowding(holdings, schemes, universe)
    dtl = metrics.days_to_liquidate(crowd, prices, settings.dtl_participation_rate)
    write_parquet(dtl, wh / "gold" / "mart_dtl.parquet")
    write_parquet(crowd, wh / "gold" / "mart_crowding.parquet")

    return Output({"crowding": len(crowd), "dtl": len(dtl)},
                  metadata={"crowding_rows": len(crowd), "dtl_rows": len(dtl),
                            "months": int(holdings.disclosure_month.nunique())})


@asset(group_name="serving", deps=[gold_marts],
       description="Compacted DuckDB artifact the API serves from.")
def serving_artifact(context: AssetExecutionContext) -> Output[str]:
    from serving.artifacts.build import build
    path = build()
    size_mb = round(Path(path).stat().st_size / 1e6, 2)
    return Output(path, metadata={"path": MetadataValue.path(path), "size_mb": size_mb})
