"""Artifact builder — the reason hosting costs nothing.

Collapses the gold marts into a single DuckDB file (tens of MB for the whole
industry). The API loads it and resolves any user portfolio in milliseconds,
never running a distributed job at request time. See docs/adr/0002.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger

from pipelines.ingestion.reference.universe import nifty50_weights
from pipelines.quality.reconciliation import reconcile
from pipelines.spark.analytics import metrics

log = get_logger(__name__)


def load_warehouse() -> dict[str, pd.DataFrame]:
    wh = settings.warehouse_dir
    need = ["silver_holdings", "dim_scheme", "dim_company", "fact_price"]
    missing = [n for n in need if not (wh / f"{n}.parquet").exists()]
    if missing:
        raise FileNotFoundError(
            f"missing warehouse tables: {missing}. Run `make seed` first.")
    return {n: pd.read_parquet(wh / f"{n}.parquet") for n in need}


def build() -> str:
    settings.ensure_dirs()
    w = load_warehouse()
    holdings, schemes, universe, prices = (
        w["silver_holdings"], w["dim_scheme"], w["dim_company"], w["fact_price"])

    latest = holdings.disclosure_month.max()
    current = holdings[holdings.disclosure_month == latest]
    log.info("building artifact from disclosure month %s", latest)

    # overlap matrix
    codes = sorted(schemes.scheme_code.tolist())
    om = metrics.overlap_matrix(current, codes)
    overlap_long = (om.stack().rename("overlap_pct").reset_index()
                      .rename(columns={"level_0": "scheme_a", "level_1": "scheme_b"}))

    # active share
    bm = nifty50_weights()
    rows = []
    for code in codes:
        s = current[(current.scheme_code == code) &
                    (current.asset_class == "equity") & current.company_id.notna()]
        if s.empty:
            continue
        a = metrics.active_share(s, bm)
        meta = schemes[schemes.scheme_code == code].iloc[0]
        rows.append(dict(scheme_code=code, scheme_name=meta.scheme_name,
                         category=meta.category, benchmark="Nifty 100 (proxy)",
                         active_share_pct=round(a, 2),
                         ter_regular_pct=meta.ter_regular_pct,
                         ter_direct_pct=meta.ter_direct_pct,
                         closet_index_flag=bool(a < 40)))
    active = pd.DataFrame(rows)

    # style drift
    drift = pd.concat(
        [metrics.style_drift(holdings, universe, c).assign(scheme_code=c) for c in codes],
        ignore_index=True)

    # turnover
    turn = pd.concat(
        [metrics.inferred_turnover(holdings, c).assign(scheme_code=c) for c in codes],
        ignore_index=True)

    # crowding + DTL
    crowd = metrics.crowding(holdings, schemes, universe, latest)
    dtl = metrics.days_to_liquidate(crowd, prices, settings.dtl_participation_rate)

    quality = reconcile.run_all(holdings, dtl)

    path = settings.artifact_path
    con = duckdb.connect(str(path))
    tables = {
        "holdings": current, "holdings_history": holdings, "schemes": schemes,
        "companies": universe, "prices": prices, "overlap": overlap_long,
        "active_share": active, "style_drift": drift, "turnover": turn,
        "crowding": crowd, "dtl": dtl, "quality": quality,
        "meta": pd.DataFrame([{"disclosure_month": str(latest),
                               "built_at": pd.Timestamp.now(),
                               "participation_rate": settings.dtl_participation_rate}]),
    }
    for name, df in tables.items():
        con.register(f"_{name}", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _{name}")
    con.close()

    mb = path.stat().st_size / 1e6
    log.info("artifact -> %s (%.2f MB, %d tables)", path, mb, len(tables))
    return str(path)


if __name__ == "__main__":
    build()
