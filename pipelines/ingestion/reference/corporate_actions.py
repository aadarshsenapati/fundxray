"""Corporate action adjustment.

Without this, historical comparisons are silently wrong. A 1:5 stock split
multiplies share count fivefold and divides price by five — the position is
unchanged, but raw quantities imply the fund bought 4× more stock. Any turnover
or holdings-delta metric computed on unadjusted quantities will invent trading
that never happened.

Convention: adjust everything to the LATEST basis (as-of today), so recent data
needs no adjustment and history is restated. Weights (% to NAV) are unaffected
by splits and bonuses — only quantities and per-share prices are.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Literal

import pandas as pd

from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

ActionType = Literal["split", "bonus", "merger", "demerger"]


@dataclass(frozen=True)
class CorporateAction:
    isin: str
    ex_date: dt.date
    action_type: ActionType
    ratio: float           # shares held AFTER per share held BEFORE
    new_isin: str | None = None    # mergers/demergers map to a successor
    note: str = ""

    @property
    def quantity_multiplier(self) -> float:
        return self.ratio

    @property
    def price_divisor(self) -> float:
        return self.ratio


def from_records(records: Iterable[dict]) -> list[CorporateAction]:
    out = []
    for r in records:
        out.append(CorporateAction(
            isin=r["isin"],
            ex_date=pd.to_datetime(r["ex_date"]).date(),
            action_type=r["action_type"],
            ratio=float(r["ratio"]),
            new_isin=r.get("new_isin"),
            note=r.get("note", ""),
        ))
    return out


def cumulative_factors(actions: list[CorporateAction],
                       as_of: dt.date | None = None) -> pd.DataFrame:
    """For each (isin, ex_date), the factor that restates PRE-action quantities
    onto the latest basis. A quantity observed before an ex-date must be
    multiplied by the product of all ratios on or after that date."""
    as_of = as_of or dt.date.today()
    rows = []
    for isin in {a.isin for a in actions}:
        acts = sorted([a for a in actions if a.isin == isin and a.ex_date <= as_of],
                      key=lambda a: a.ex_date)
        for i, a in enumerate(acts):
            factor = 1.0
            for later in acts[i:]:
                factor *= later.quantity_multiplier
            rows.append({"isin": isin, "ex_date": a.ex_date,
                         "action_type": a.action_type, "ratio": a.ratio,
                         "cumulative_factor": factor})
    return pd.DataFrame(rows, columns=["isin", "ex_date", "action_type",
                                       "ratio", "cumulative_factor"])


def adjust_holdings(holdings: pd.DataFrame, actions: list[CorporateAction],
                    as_of: dt.date | None = None,
                    date_col: str = "disclosure_month",
                    isin_col: str = "company_id") -> pd.DataFrame:
    """Restate quantities onto the latest basis and remap merged identifiers.

    Weights are intentionally untouched: a split changes neither the value of
    the position nor its share of net assets.
    """
    df = holdings.copy()
    if not actions:
        df["qty_adjustment_factor"] = 1.0
        return df

    factors = cumulative_factors(actions, as_of)
    dates = pd.to_datetime(df[date_col])

    adj = pd.Series(1.0, index=df.index)
    for isin, grp in factors.groupby("isin"):
        mask_isin = df[isin_col] == isin
        if not mask_isin.any():
            continue
        for r in grp.itertuples():
            mask = mask_isin & (dates < pd.Timestamp(r.ex_date))
            adj.loc[mask] *= r.ratio

    df["qty_adjustment_factor"] = adj
    if "quantity" in df:
        df["quantity_adjusted"] = pd.to_numeric(df["quantity"], errors="coerce") * adj

    # Successor mapping for mergers/demergers.
    remap = {a.isin: a.new_isin for a in actions
             if a.action_type in ("merger", "demerger") and a.new_isin}
    if remap:
        df["company_id_original"] = df[isin_col]
        df[isin_col] = df[isin_col].replace(remap)
        log.info("remapped %d identifiers through mergers/demergers", len(remap))

    n_adj = int((adj != 1.0).sum())
    if n_adj:
        log.info("corporate actions: adjusted %d holding rows", n_adj)
    return df


def load(path) -> list[CorporateAction]:
    df = pd.read_csv(path)
    return from_records(df.to_dict("records"))
