"""The user's own holdings, fetched with their session token.

READ ONLY. Only two SmartAPI endpoints are ever called:
    portfolio/v1/getHolding      — per-holding detail
    portfolio/v1/getAllHolding   — portfolio totals

Nothing in this repository can place, modify or cancel an order.

Scope caveat worth stating plainly to users: these are DEMAT holdings. Equities
appear, and so do mutual fund units held in demat form — but units held in the
traditional statement (SoA) form do not. Most Indian SIP investors hold SoA
units, so the CAS upload path remains the complete picture.

`api_key` is threaded through every call here rather than read from settings
directly, because a visitor may have signed in with their own SmartAPI app key
(bring-your-own-key) rather than this server's shared default — see
serving/api/services/auth.py.
"""
from __future__ import annotations

import requests

from fundxray_core.config import settings
from fundxray_core.utils.logging import get_logger
from fundxray_core.utils.throttle import TokenBucket

log = get_logger(__name__)

BASE = "https://apiconnect.angelone.in"
HOLDING_URL = f"{BASE}/rest/secure/angelbroking/portfolio/v1/getHolding"
ALL_HOLDING_URL = f"{BASE}/rest/secure/angelbroking/portfolio/v1/getAllHolding"

_bucket = TokenBucket(settings.smartapi_rate_limit_rps)


def _headers(auth_token: str, api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-PrivateKey": api_key,
        "X-ClientLocalIP": "127.0.0.1",
        "X-ClientPublicIP": "127.0.0.1",
        "X-MACAddress": "00:00:00:00:00:00",
    }


def _get(url: str, auth_token: str, api_key: str, timeout: int = 30) -> dict:
    _bucket.acquire()
    r = requests.get(url, headers=_headers(auth_token, api_key), timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("status", True):
        raise RuntimeError(payload.get("message") or "SmartAPI returned an error")
    return payload.get("data") or {}


def fetch_holdings(auth_token: str, api_key: str) -> list[dict]:
    data = _get(HOLDING_URL, auth_token, api_key)
    rows = data if isinstance(data, list) else data.get("holdings", [])
    out = []
    for h in rows or []:
        try:
            qty = float(h.get("quantity") or 0)
            ltp = float(h.get("ltp") or 0)
            out.append({
                "isin": h.get("isin"),
                "symbol": h.get("tradingsymbol"),
                "exchange": h.get("exchange"),
                "quantity": qty,
                "average_price": float(h.get("averageprice") or 0),
                "ltp": ltp,
                "value": round(qty * ltp, 2),
                "pnl": float(h.get("profitandloss") or 0),
                "pnl_pct": float(h.get("pnlpercentage") or 0),
            })
        except (TypeError, ValueError):
            log.warning("skipping unparseable holding: %s", h.get("tradingsymbol"))
    return out


def fetch_totals(auth_token: str, api_key: str) -> dict:
    data = _get(ALL_HOLDING_URL, auth_token, api_key)
    t = (data or {}).get("totalholding") or {}
    return {
        "total_value": float(t.get("totalholdingvalue") or 0),
        "total_invested": float(t.get("totalinvvalue") or 0),
        "total_pnl": float(t.get("totalprofitandloss") or 0),
        "total_pnl_pct": float(t.get("totalpnlpercentage") or 0),
    }


def analyse(holdings: list[dict]) -> dict:
    """Run the FundXRay lens over the user's own demat holdings.

    Direct equity needs no look-through — it already IS the exposure — so the
    useful outputs are concentration, sector mix, and crowding/liquidity risk
    from the DTL mart.
    """
    import pandas as pd

    from pipelines.spark.analytics import metrics

    from . import xray as svc

    if not holdings:
        return {"holdings": [], "summary": {}, "note": "No demat holdings returned."}

    df = pd.DataFrame(holdings)
    total = float(df.value.sum())
    if total <= 0:
        return {"holdings": holdings, "summary": {}, "note": "Holdings have no market value."}

    df["weight_pct"] = df.value / total * 100
    lt = df.rename(columns={"isin": "company_id", "symbol": "instrument_name_raw"})
    lt["asset_class"] = "equity"
    summary = metrics.concentration_summary(lt)

    enriched, dtl_rows = df, []
    try:
        companies = svc.q("SELECT isin, company_name, sector, cap_bucket FROM companies")
        enriched = df.merge(companies, on="isin", how="left")
        isins = [i for i in df.isin.dropna().tolist()]
        if isins:
            dtl_rows = svc.q(
                "SELECT isin, company_name, days_to_liquidate, mf_pct_of_free_float "
                "FROM dtl WHERE isin IN ? ORDER BY days_to_liquidate DESC", [isins]
            ).to_dict("records")
    except Exception as e:            # artifact absent or ISIN not covered
        log.warning("enrichment unavailable: %s", e)

    sectors = []
    if "sector" in enriched:
        sectors = (enriched.dropna(subset=["sector"])
                   .groupby("sector", as_index=False).weight_pct.sum()
                   .round(2).sort_values("weight_pct", ascending=False)
                   .to_dict("records"))

    return {
        "holdings": enriched.round(2).to_dict("records"),
        "summary": summary,
        "sectors": sectors,
        "crowding": dtl_rows,
        "note": ("Demat holdings only. Mutual fund units held in statement (SoA) "
                 "form do not appear here — upload your CAS for the full picture."),
        "disclaimer": "Informational only. Not investment advice.",
    }
