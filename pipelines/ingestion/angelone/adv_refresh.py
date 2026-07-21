"""Refresh real ADV and prices for the DTL mart.

Runs against live SmartAPI when credentials are present. Without them it leaves
the synthetic seed in place and says so loudly, rather than silently serving
made-up liquidity figures.
"""
from __future__ import annotations

import pandas as pd

from fundxray_core.config import settings
from fundxray_core.io import write_parquet
from fundxray_core.utils.logging import get_logger

from .client import get_client
from .risk import average_daily_volume

log = get_logger(__name__)


def run(sessions: int = 30) -> dict:
    wh = settings.warehouse_dir
    if not settings.smartapi_enabled:
        log.warning("SmartAPI credentials absent — keeping synthetic ADV. "
                    "Days-to-Liquidate will be illustrative, not real.")
        return {"status": "skipped", "reason": "no credentials"}

    inst_path = wh / "dim_instrument.parquet"
    if not inst_path.exists():
        from .instruments import run as build_instruments
        build_instruments()
    inst = pd.read_parquet(inst_path).dropna(subset=["smartapi_token"])

    client = get_client()
    rows = []
    for r in inst.itertuples():
        try:
            candles = client.candles(r.smartapi_token, days=max(sessions * 2, 60))
            adv = average_daily_volume(candles, sessions)
            close = float(candles.tail(1)["close"].iloc[0]) if len(candles) else None
            rows.append({"isin": r.isin, "nse_symbol": r.nse_symbol,
                         "smartapi_token": r.smartapi_token,
                         "close": close, "adv_shares_30d": adv,
                         "source": "smartapi"})
        except Exception as e:      # one bad symbol must not kill the run
            log.warning("SmartAPI failed for %s: %s", r.nse_symbol, e)

    if not rows:
        return {"status": "failed", "rows": 0}

    df = pd.DataFrame(rows)
    write_parquet(df, wh / "fact_price.parquet")
    log.info("refreshed %d instruments from SmartAPI", len(df))
    return {"status": "ok", "rows": len(df),
            "with_adv": int(df.adv_shares_30d.notna().sum())}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
