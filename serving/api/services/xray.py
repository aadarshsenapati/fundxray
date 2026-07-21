from __future__ import annotations

import threading
from typing import Any, Optional

import duckdb
import pandas as pd

from fundxray_core.config import settings
from pipelines.spark.analytics import metrics

_lock = threading.Lock()
_con: Optional[duckdb.DuckDBPyConnection] = None


def con() -> duckdb.DuckDBPyConnection:
    global _con
    with _lock:
        if _con is None:
            if not settings.artifact_path.exists():
                raise FileNotFoundError(
                    f"artifact not found at {settings.artifact_path}. "
                    "Run `make seed && make artifact`.")
            _con = duckdb.connect(str(settings.artifact_path), read_only=True)
        return _con


def q(sql: str, params: list | None = None) -> pd.DataFrame:
    return con().execute(sql, params or []).fetch_df()


def meta() -> dict:
    return q("SELECT * FROM meta").iloc[0].to_dict()


def schemes() -> list[dict]:
    return q("SELECT * FROM schemes ORDER BY scheme_name").to_dict("records")


def xray(portfolio: dict[str, float]) -> dict[str, Any]:
    holdings = q("SELECT * FROM holdings")
    valid = {k: v for k, v in portfolio.items()
             if k in set(holdings.scheme_code) and v > 0}
    if not valid:
        raise ValueError("no valid schemes in portfolio")

    lt = metrics.look_through(valid, holdings)
    summary = metrics.concentration_summary(lt)
    total = sum(valid.values())

    eq = lt[lt.asset_class == "equity"].head(15).copy()
    eq["weight_pct"] = eq.weight_pct.round(2)
    eq["value"] = eq.value.round(0)

    companies = q("SELECT isin, sector, cap_bucket FROM companies")
    m = lt.merge(companies, left_on="company_id", right_on="isin", how="left")
    sectors = (m[m.asset_class == "equity"].groupby("sector", as_index=False)
                 .weight_pct.sum().round(2)
                 .sort_values("weight_pct", ascending=False))
    caps = (m[m.asset_class == "equity"].groupby("cap_bucket", as_index=False)
              .weight_pct.sum().round(2))

    codes = list(valid)
    ov = q("SELECT * FROM overlap WHERE scheme_a IN ? AND scheme_b IN ? "
           "AND scheme_a < scheme_b ORDER BY overlap_pct DESC", [codes, codes])
    names = dict(zip(q("SELECT scheme_code, scheme_name FROM schemes").scheme_code,
                     q("SELECT scheme_code, scheme_name FROM schemes").scheme_name))
    ov["scheme_a_name"] = ov.scheme_a.map(names)
    ov["scheme_b_name"] = ov.scheme_b.map(names)

    sch = q("SELECT * FROM schemes WHERE scheme_code IN ?", [codes])
    weighted_ter = sum(valid[r.scheme_code] * r.ter_regular_pct
                       for r in sch.itertuples()) / total
    weighted_ter_direct = sum(valid[r.scheme_code] * r.ter_direct_pct
                              for r in sch.itertuples()) / total

    return {
        "as_of": str(meta()["disclosure_month"]),
        "total_value": total,
        "fund_count": len(valid),
        "summary": summary,
        "top_holdings": eq.to_dict("records"),
        "sectors": sectors.to_dict("records"),
        "cap_split": caps.to_dict("records"),
        "overlaps": ov.round(2).to_dict("records"),
        "weighted_ter_regular_pct": round(weighted_ter, 3),
        "weighted_ter_direct_pct": round(weighted_ter_direct, 3),
        "annual_fee_regular": round(total * weighted_ter / 100, 0),
        "annual_fee_direct": round(total * weighted_ter_direct / 100, 0),
        "disclaimer": "Informational only. Not investment advice. "
                      "Holdings are as of the disclosure date shown.",
    }


def active_share_table() -> list[dict]:
    return q("SELECT * FROM active_share ORDER BY active_share_pct").to_dict("records")


def dtl_table(limit: int = 25) -> list[dict]:
    return q("SELECT * FROM dtl ORDER BY days_to_liquidate DESC LIMIT ?",
             [limit]).to_dict("records")


def drift(scheme_code: str) -> list[dict]:
    return q("SELECT * FROM style_drift WHERE scheme_code = ? "
             "ORDER BY disclosure_month", [scheme_code]).to_dict("records")


def quality() -> list[dict]:
    return q("SELECT * FROM quality").to_dict("records")
