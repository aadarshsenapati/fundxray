"""Scheme master from AMFI NAV data.

Disclosure workbooks name schemes in prose ("Bluewater Large Cap Fund - Direct
Plan - Growth"); AMFI NAV data carries the canonical scheme code and ISIN. This
joins the two so surrogate hash keys can be replaced by real AMFI scheme codes,
and TER/plan/category metadata becomes available.
"""
from __future__ import annotations

import pandas as pd
from rapidfuzz import fuzz, process

from fundxray_core.identifiers.names import normalise_name
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

MATCH_THRESHOLD = 82

_PLAN_NOISE = [
    "direct plan", "regular plan", "direct", "regular", "growth option",
    "growth", "idcw", "dividend", "payout", "reinvestment", "bonus option",
    "daily", "weekly", "monthly", "quarterly", "option", "plan",
]


def canonical_scheme_name(name: str) -> str:
    """Strip plan/option suffixes so 'X - Direct Plan - Growth' and
    'X - Regular - IDCW' collapse to the same underlying scheme."""
    n = normalise_name(name)
    for token in _PLAN_NOISE:
        n = n.replace(token, " ")
    return " ".join(n.split())


def build(nav: pd.DataFrame) -> pd.DataFrame:
    """One row per underlying scheme, preferring the growth plan for identity."""
    df = nav.copy()
    df["canonical_name"] = df.scheme_name.map(canonical_scheme_name)
    df["rank"] = (~df.is_growth).astype(int)      # growth plans first
    df = df.sort_values(["canonical_name", "rank"])

    agg = (df.groupby("canonical_name")
             .agg(scheme_code=("scheme_code", "first"),
                  scheme_name=("scheme_name", "first"),
                  amc_name=("amc_name", "first"),
                  category=("category", "first"),
                  plans=("scheme_code", "count"))
             .reset_index())
    log.info("scheme master: %d underlying schemes from %d NAV rows", len(agg), len(nav))
    return agg


def match_disclosure_schemes(disclosure_names: list[str],
                             master: pd.DataFrame) -> pd.DataFrame:
    """Fuzzy-match scheme names found in disclosure workbooks to the master."""
    choices = master.canonical_name.tolist()
    rows = []
    for raw in disclosure_names:
        canon = canonical_scheme_name(raw)
        hit = process.extractOne(canon, choices, scorer=fuzz.token_sort_ratio) if choices else None
        if hit and hit[1] >= MATCH_THRESHOLD:
            m = master.iloc[hit[2]]
            rows.append({"disclosure_scheme_name": raw, "canonical_name": canon,
                         "scheme_code": m.scheme_code, "matched_name": m.scheme_name,
                         "amc_name": m.amc_name, "category": m.category,
                         "match_score": hit[1] / 100.0, "matched": True})
        else:
            rows.append({"disclosure_scheme_name": raw, "canonical_name": canon,
                         "scheme_code": None, "matched_name": None,
                         "amc_name": None, "category": None,
                         "match_score": (hit[1] / 100.0) if hit else 0.0,
                         "matched": False})
    out = pd.DataFrame(rows)
    log.info("scheme matching: %d/%d matched (>= %d)",
             int(out.matched.sum()), len(out), MATCH_THRESHOLD)
    return out
