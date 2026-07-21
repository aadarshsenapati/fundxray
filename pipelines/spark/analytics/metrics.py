"""Analytical engine. Formal definitions live in docs/metrics.md.

Everything here is deterministic and reproducible from disclosed data. No LLM
touches any number in this module.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# --- 1. look-through -------------------------------------------------------
def look_through(portfolio: dict[str, float], holdings: pd.DataFrame) -> pd.DataFrame:
    """portfolio: {scheme_code: rupee_value}. Returns true company-level weights."""
    total = sum(portfolio.values())
    if total <= 0:
        return pd.DataFrame(columns=["company_id", "instrument_name_raw", "weight_pct", "value"])

    frames = []
    for code, value in portfolio.items():
        h = holdings[holdings.scheme_code == code]
        if h.empty:
            continue
        f = h[["company_id", "instrument_name_raw", "asset_class", "weight_pct"]].copy()
        f["contrib"] = f.weight_pct * (value / total)
        frames.append(f)
    if not frames:
        return pd.DataFrame(columns=["company_id", "instrument_name_raw", "weight_pct", "value"])

    allh = pd.concat(frames, ignore_index=True)
    allh["company_id"] = allh.company_id.fillna(allh.instrument_name_raw)
    out = (allh.groupby(["company_id", "asset_class"], as_index=False)
                .agg(weight_pct=("contrib", "sum"),
                     instrument_name_raw=("instrument_name_raw", "first")))
    out["value"] = out.weight_pct / 100.0 * total
    return out.sort_values("weight_pct", ascending=False).reset_index(drop=True)


# --- 2. pairwise overlap ---------------------------------------------------
def overlap(a: pd.DataFrame, b: pd.DataFrame, key: str = "company_id") -> float:
    """Overlap(A,B) = sum_c min(h(c,A), h(c,B))."""
    ga = a.groupby(key).weight_pct.sum()
    gb = b.groupby(key).weight_pct.sum()
    common = ga.index.intersection(gb.index)
    if len(common) == 0:
        return 0.0
    return float(np.minimum(ga.loc[common], gb.loc[common]).sum())


def overlap_matrix(holdings: pd.DataFrame, schemes: list[str]) -> pd.DataFrame:
    """O(n^2) in schemes. Fine for one portfolio; this is the Spark job at
    industry scale (10k schemes) — see pipelines/spark/analytics/spark_overlap.py."""
    eq = holdings[(holdings.asset_class == "equity") & holdings.company_id.notna()]
    parts = {s: eq[eq.scheme_code == s] for s in schemes}
    m = pd.DataFrame(index=schemes, columns=schemes, dtype=float)
    for i, s1 in enumerate(schemes):
        for s2 in schemes[i:]:
            v = 100.0 if s1 == s2 else round(overlap(parts[s1], parts[s2]), 2)
            m.loc[s1, s2] = m.loc[s2, s1] = v
    return m


# --- 3. active share -------------------------------------------------------
def active_share(scheme: pd.DataFrame, benchmark: pd.DataFrame,
                 key: str = "company_id") -> float:
    """0.5 * sum_c |h(c,fund) - h(c,index)|. 0% = index clone, 100% = disjoint."""
    gf = scheme.groupby(key).weight_pct.sum()
    gb = benchmark.set_index("isin").weight_pct if "isin" in benchmark else benchmark.groupby(key).weight_pct.sum()
    idx = gf.index.union(gb.index)
    diff = gf.reindex(idx).fillna(0) - gb.reindex(idx).fillna(0)
    return float(0.5 * diff.abs().sum())


# --- 4. style drift --------------------------------------------------------
def style_drift(holdings: pd.DataFrame, universe: pd.DataFrame,
                scheme_code: str) -> pd.DataFrame:
    """Cap composition month by month. Uses the AMFI classification in force at
    time t — using today's list for historical months is a serious error."""
    caps = universe.set_index("isin").cap_bucket
    h = holdings[(holdings.scheme_code == scheme_code) &
                 (holdings.asset_class == "equity")].copy()
    h["cap_bucket"] = h.company_id.map(caps)
    g = (h.groupby(["disclosure_month", "cap_bucket"]).weight_pct.sum()
          .unstack(fill_value=0.0))
    for c in ("large", "mid", "small"):
        if c not in g:
            g[c] = 0.0
    g = g[["large", "mid", "small"]]
    return g.div(g.sum(axis=1), axis=0).mul(100).round(2).reset_index()


# --- 5. fee drag -----------------------------------------------------------
def fee_drag(monthly_contribution: float, years: int, gross_return_pct: float,
             ter_a_pct: float, ter_b_pct: float) -> dict:
    """Terminal-value difference between two expense ratios. This is a
    sensitivity illustration under a stated assumption, not a prediction."""
    n = years * 12

    def fv(ter: float) -> float:
        r = (gross_return_pct - ter) / 100.0 / 12.0
        return monthly_contribution * (((1 + r) ** n - 1) / r) if r else monthly_contribution * n

    a, b = fv(ter_a_pct), fv(ter_b_pct)
    return {
        "invested": monthly_contribution * n,
        "terminal_value_a": round(a, 2), "ter_a_pct": ter_a_pct,
        "terminal_value_b": round(b, 2), "ter_b_pct": ter_b_pct,
        "difference": round(a - b, 2),
        "gross_return_assumption_pct": gross_return_pct,
        "years": years,
        "note": "Illustration under a stated return assumption. Not a prediction.",
    }


# --- 6. inferred turnover --------------------------------------------------
def inferred_turnover(holdings: pd.DataFrame, scheme_code: str) -> pd.DataFrame:
    """Lower bound only: intra-month round trips are invisible to monthly
    disclosure, and price movement changes weights without any trading."""
    h = holdings[(holdings.scheme_code == scheme_code) &
                 (holdings.asset_class == "equity")]
    piv = h.pivot_table(index="disclosure_month", columns="company_id",
                        values="weight_pct", aggfunc="sum").fillna(0.0)
    if len(piv) < 2:
        return pd.DataFrame(columns=["disclosure_month", "inferred_turnover_pct"])
    d = piv.diff().abs().sum(axis=1).iloc[1:] * 0.5 * 12
    return d.round(2).rename("inferred_turnover_pct").reset_index()


# --- 7. crowding -----------------------------------------------------------
def crowding(holdings: pd.DataFrame, schemes: pd.DataFrame,
             universe: pd.DataFrame, month=None) -> pd.DataFrame:
    """Aggregate MF ownership as a share of free float."""
    h = holdings[holdings.asset_class == "equity"].copy()
    month = month or h.disclosure_month.max()
    h = h[h.disclosure_month == month]
    h = h.merge(schemes[["scheme_code", "aum_cr"]], on="scheme_code", how="left")
    h["value_cr"] = h.weight_pct / 100.0 * h.aum_cr.fillna(0)

    agg = h.groupby("company_id", as_index=False).value_cr.sum()
    uni = universe[["isin", "company_name", "cap_bucket", "free_float_shares_cr"]]
    out = agg.merge(uni, left_on="company_id", right_on="isin", how="inner")
    out["mf_holding_cr"] = out.value_cr.round(1)
    return out.sort_values("mf_holding_cr", ascending=False).reset_index(drop=True)


# --- 8. days to liquidate (flagship) ---------------------------------------
def days_to_liquidate(crowd: pd.DataFrame, prices: pd.DataFrame,
                      participation: float = 0.20) -> pd.DataFrame:
    """DTL = total MF shares held / (participation * ADV).

    How many sessions the industry would need to exit without dominating the
    tape. High DTL = everyone standing at the same narrow exit. Present as a
    RELATIVE ranking, not a precise forecast: ADV is backward-looking and
    collapses exactly when it matters.
    """
    df = crowd.merge(prices[["isin", "close", "adv_shares_30d", "nse_symbol"]],
                     on="isin", how="inner")
    df["mf_shares"] = df.mf_holding_cr * 1e7 / df.close
    denom = (participation * df.adv_shares_30d).replace(0, np.nan)
    df["days_to_liquidate"] = (df.mf_shares / denom).round(1)
    df["mf_pct_of_free_float"] = (
        df.mf_shares / (df.free_float_shares_cr * 1e7) * 100).round(2)
    df["participation_assumption"] = participation
    cols = ["isin", "company_name", "nse_symbol", "cap_bucket", "mf_holding_cr",
            "mf_pct_of_free_float", "adv_shares_30d", "days_to_liquidate",
            "participation_assumption"]
    return df[cols].sort_values("days_to_liquidate", ascending=False).reset_index(drop=True)


# --- 9. concentration ------------------------------------------------------
def hhi(look_through_df: pd.DataFrame) -> float:
    """Herfindahl-Hirschman Index over look-through weights."""
    w = look_through_df[look_through_df.asset_class == "equity"].weight_pct
    return float(round((w ** 2).sum(), 1))


def concentration_summary(lt: pd.DataFrame) -> dict:
    eq = lt[lt.asset_class == "equity"].sort_values("weight_pct", ascending=False)
    return {
        "top_holding": eq.instrument_name_raw.iloc[0] if len(eq) else None,
        "top_holding_pct": round(float(eq.weight_pct.iloc[0]), 2) if len(eq) else 0.0,
        "top_5_pct": round(float(eq.weight_pct.head(5).sum()), 2),
        "top_10_pct": round(float(eq.weight_pct.head(10).sum()), 2),
        "unique_companies": int(eq.company_id.nunique()),
        "hhi": hhi(lt),
    }
