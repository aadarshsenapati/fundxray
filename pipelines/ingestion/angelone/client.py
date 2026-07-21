"""Angel One SmartAPI client.

Used for two things FundXRay cannot get from AMFI:
  1. Historical OHLCV  -> average daily volume, the denominator of Days-to-Liquidate
  2. Live ticks        -> real-time marking of look-through exposure

Credentials come from .env. Rate-limited via token bucket — Angel One will
block you for exceeding published limits.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

import pandas as pd

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger
from fundxray_core.utils.throttle import TokenBucket

log = get_logger(__name__)

CANDLE_COLS = ["timestamp", "open", "high", "low", "close", "volume"]


class SmartAPIClient:
    def __init__(self) -> None:
        self._sc: Any = None
        self._bucket = TokenBucket(settings.smartapi_rate_limit_rps)

    # -- session -----------------------------------------------------------
    def connect(self) -> None:
        if self._sc is not None:
            return
        if not settings.smartapi_enabled:
            raise RuntimeError("SmartAPI credentials missing — set them in .env")

        from SmartApi import SmartConnect  # lazy: optional dependency
        import pyotp

        self._sc = SmartConnect(api_key=settings.smartapi_api_key)
        totp = pyotp.TOTP(settings.smartapi_totp_secret).now()
        resp = self._sc.generateSession(
            settings.smartapi_client_id, settings.smartapi_password, totp
        )
        if not resp.get("status"):
            raise RuntimeError(f"SmartAPI login failed: {resp.get('message')}")
        log.info("SmartAPI session established for %s", settings.smartapi_client_id)

    # -- historical --------------------------------------------------------
    def candles(
        self,
        symbol_token: str,
        exchange: str = "NSE",
        interval: str = "ONE_DAY",
        days: int = 90,
        end: Optional[dt.datetime] = None,
    ) -> pd.DataFrame:
        self.connect()
        end = end or dt.datetime.now()
        start = end - dt.timedelta(days=days)
        self._bucket.acquire()
        resp = self._sc.getCandleData({
            "exchange": exchange,
            "symboltoken": str(symbol_token),
            "interval": interval,
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": end.strftime("%Y-%m-%d %H:%M"),
        })
        data = (resp or {}).get("data") or []
        df = pd.DataFrame(data, columns=CANDLE_COLS)
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            for c in CANDLE_COLS[1:]:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def average_daily_volume(self, symbol_token: str, sessions: int = 30, **kw) -> Optional[float]:
        """ADV over trailing sessions — the DTL denominator."""
        df = self.candles(symbol_token, days=max(sessions * 2, 60), **kw)
        if df.empty:
            return None
        return float(df.tail(sessions)["volume"].mean())

    def ltp(self, symbol: str, symbol_token: str, exchange: str = "NSE") -> Optional[float]:
        self.connect()
        self._bucket.acquire()
        resp = self._sc.ltpData(exchange, symbol, str(symbol_token))
        return (resp or {}).get("data", {}).get("ltp")


_client: Optional[SmartAPIClient] = None


def get_client() -> SmartAPIClient:
    global _client
    if _client is None:
        _client = SmartAPIClient()
    return _client
