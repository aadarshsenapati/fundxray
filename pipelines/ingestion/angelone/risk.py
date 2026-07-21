"""Historical risk metrics from SmartAPI candles.

Feeds two things:
  * Days-to-Liquidate — needs 30-session average daily volume
  * Portfolio risk panel — volatility, beta, max drawdown on look-through exposure
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

TRADING_DAYS = 252


def average_daily_volume(candles: pd.DataFrame, sessions: int = 30) -> float | None:
    if candles is None or candles.empty:
        return None
    return float(candles.tail(sessions)["volume"].mean())


def daily_returns(candles: pd.DataFrame) -> pd.Series:
    c = candles.sort_values("timestamp")["close"].astype(float)
    return np.log(c / c.shift(1)).dropna()


def realised_volatility(candles: pd.DataFrame) -> float | None:
    r = daily_returns(candles)
    if len(r) < 20:
        return None
    return float(r.std() * np.sqrt(TRADING_DAYS) * 100)


def beta(asset: pd.DataFrame, benchmark: pd.DataFrame) -> float | None:
    ra, rb = daily_returns(asset), daily_returns(benchmark)
    joined = pd.concat([ra.rename("a"), rb.rename("b")], axis=1).dropna()
    if len(joined) < 30 or joined.b.var() == 0:
        return None
    return float(joined.cov().loc["a", "b"] / joined.b.var())


def max_drawdown(candles: pd.DataFrame) -> float | None:
    c = candles.sort_values("timestamp")["close"].astype(float)
    if len(c) < 2:
        return None
    return float(((c / c.cummax()) - 1).min() * 100)


def portfolio_risk(weights: dict[str, float], candles_by_isin: dict[str, pd.DataFrame],
                   benchmark: pd.DataFrame | None = None) -> dict:
    """Risk of the look-through equity exposure, weight-blended from constituents."""
    rows = []
    for isin, w in weights.items():
        c = candles_by_isin.get(isin)
        if c is None or c.empty:
            continue
        rows.append({"isin": isin, "weight": w,
                     "volatility_pct": realised_volatility(c),
                     "beta": beta(c, benchmark) if benchmark is not None else None,
                     "max_drawdown_pct": max_drawdown(c)})
    if not rows:
        return {"coverage": 0.0}

    df = pd.DataFrame(rows).dropna(subset=["volatility_pct"])
    tw = df.weight.sum()
    out = {
        "coverage_pct": round(tw / sum(weights.values()) * 100, 2) if weights else 0.0,
        "weighted_volatility_pct": round(float((df.volatility_pct * df.weight).sum() / tw), 2),
        "weighted_max_drawdown_pct": round(float((df.max_drawdown_pct * df.weight).sum() / tw), 2),
        "constituents_priced": len(df),
    }
    if benchmark is not None and df.beta.notna().any():
        b = df.dropna(subset=["beta"])
        out["weighted_beta"] = round(float((b.beta * b.weight).sum() / b.weight.sum()), 3)
    out["note"] = ("Weighted average of constituent risk. Ignores cross-correlation, "
                   "so it overstates portfolio volatility versus a full covariance model.")
    return out
