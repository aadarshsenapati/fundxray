"""Data quality gates.

A parsing bug here means showing someone wrong numbers about their savings.
Violations quarantine; they never propagate to serving.
"""
from __future__ import annotations

import pandas as pd

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)


class QualityFailure(Exception):
    pass


def check_weights_sum(holdings: pd.DataFrame, tolerance_pct: float | None = None) -> pd.DataFrame:
    """Every scheme-month must sum to ~100% of net assets."""
    tol = tolerance_pct if tolerance_pct is not None else settings.reconciliation_tolerance_pct
    g = (holdings.groupby(["scheme_code", "disclosure_month"], as_index=False)
                 .weight_pct.sum().rename(columns={"weight_pct": "total_weight"}))
    g["deviation"] = (g.total_weight - 100.0).abs()
    g["passed"] = g.deviation <= tol
    bad = g[~g.passed]
    if len(bad):
        log.warning("%d scheme-months failed weight reconciliation (tolerance %.2f%%)",
                    len(bad), tol)
    return g


def check_isin_resolution(holdings: pd.DataFrame, min_rate: float = 0.95) -> dict:
    eq = holdings[holdings.asset_class == "equity"]
    rate = float(eq.company_id.notna().mean()) if len(eq) else 1.0
    return {"metric": "equity_isin_resolution_rate", "value": round(rate, 4),
            "threshold": min_rate, "passed": rate >= min_rate,
            "unresolved_rows": int(eq.company_id.isna().sum()) if len(eq) else 0}


def check_no_duplicates(holdings: pd.DataFrame) -> dict:
    key = ["scheme_code", "disclosure_month", "company_id", "instrument_name_raw"]
    dupes = int(holdings.duplicated(subset=key).sum())
    return {"metric": "duplicate_holdings", "value": dupes,
            "threshold": 0, "passed": dupes == 0}


def check_freshness(holdings: pd.DataFrame, max_age_days: int = 75) -> dict:
    latest = pd.to_datetime(holdings.disclosure_month).max()
    age = (pd.Timestamp.today() - latest).days
    return {"metric": "disclosure_age_days", "value": int(age),
            "threshold": max_age_days, "passed": age <= max_age_days}


def check_free_float_sanity(dtl: pd.DataFrame) -> dict:
    """MF ownership above 100% of free float is physically impossible and always
    indicates a data error — bad AUM, bad share count, or a stale price."""
    if dtl is None or dtl.empty:
        return {"metric": "mf_ownership_within_free_float", "value": 0,
                "threshold": 0, "passed": True}
    breaches = int((dtl.mf_pct_of_free_float > 100).sum())
    if breaches:
        log.warning("%d securities show MF ownership >100%% of free float", breaches)
    return {"metric": "mf_ownership_within_free_float", "value": breaches,
            "threshold": 0, "passed": breaches == 0}


def run_all(holdings: pd.DataFrame, dtl: pd.DataFrame | None = None,
            strict: bool = False) -> pd.DataFrame:
    recon = check_weights_sum(holdings)
    checks = [
        {"metric": "scheme_month_weight_reconciliation",
         "value": round(float(recon.passed.mean()), 4), "threshold": 0.99,
         "passed": bool(recon.passed.mean() >= 0.99)},
        check_isin_resolution(holdings),
        check_no_duplicates(holdings),
        check_freshness(holdings),
        check_free_float_sanity(dtl),
    ]
    for c in checks:
        c.setdefault("unresolved_rows", 0)
    df = pd.DataFrame(checks).fillna(0)
    for c in checks:
        log.info("%-40s %-10s %s", c["metric"], c["value"],
                 "PASS" if c["passed"] else "FAIL")
    if strict and not df.passed.all():
        raise QualityFailure(f"quality gates failed: {df[~df.passed].metric.tolist()}")
    return df
