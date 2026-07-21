"""Entity resolution: raw instrument names -> stable company identities.

ISIN first (regulator-backed, stable). Blocked fuzzy name matching for the gaps,
which are common in older disclosures. Every row records how it was resolved and
with what confidence — unresolved rows are quarantined and counted, and the
match rate is a published quality metric rather than a hidden one.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd
from rapidfuzz import fuzz, process

from fundxray_core.identifiers.isin import is_valid, normalise
from fundxray_core.identifiers.names import blocking_key, normalise_name
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

FUZZY_THRESHOLD = 88


class EntityResolver:
    def __init__(self, universe: pd.DataFrame):
        self.by_isin = set(universe["isin"])   # NB: .isin is a Series method
        self._norm = {normalise_name(n): i for n, i in
                      zip(universe.company_name, universe["isin"])}
        self._blocks: dict[str, list[str]] = {}
        for name in self._norm:
            self._blocks.setdefault(name[:4], []).append(name)

    def resolve(self, name: str, isin: Optional[str]) -> tuple[Optional[str], str, float]:
        s = normalise(isin)
        if s and is_valid(s) and s in self.by_isin:
            return s, "isin", 1.0
        if s and is_valid(s):
            return s, "isin_unmapped", 0.9

        key = blocking_key(name)
        candidates = self._blocks.get(key) or list(self._norm)
        n = normalise_name(name)
        if not n:
            return None, "unresolved", 0.0
        match = process.extractOne(n, candidates, scorer=fuzz.token_sort_ratio)
        if match and match[1] >= FUZZY_THRESHOLD:
            return self._norm[match[0]], "fuzzy_name", match[1] / 100.0
        return None, "unresolved", 0.0

    def resolve_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        res = [self.resolve(r.instrument_name_raw, r.isin) for r in out.itertuples()]
        out["company_id"] = [r[0] for r in res]
        out["resolution_method"] = [r[1] for r in res]
        out["resolution_confidence"] = [r[2] for r in res]

        equity = out[out.asset_class == "equity"]
        if len(equity):
            rate = equity.company_id.notna().mean()
            log.info("equity resolution match rate: %.2f%% (%d unresolved)",
                     rate * 100, int(equity.company_id.isna().sum()))
        return out
