"""Angel One instrument master.

Published as a single public JSON — no authentication needed — mapping every
tradable instrument to the `symboltoken` the SmartAPI historical and WebSocket
endpoints require. Holdings carry ISINs; SmartAPI speaks tokens; this is the
bridge between them.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from fundxray_core.config import settings
from fundxray_core.io import write_parquet
from fundxray_core.utils.logging import get_logger

log = get_logger(__name__)

SCRIP_MASTER_URL = ("https://margincalculator.angelbroking.com/OpenAPI_File/"
                    "files/OpenAPIScripMaster.json")


def fetch(url: str = SCRIP_MASTER_URL, timeout: int = 90) -> list[dict]:
    log.info("fetching Angel One scrip master")
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def to_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    eq = df[(df.get("exch_seg") == "NSE") & (df.get("symbol").astype(str).str.endswith("-EQ"))]
    return eq.assign(nse_symbol=eq["symbol"].str.replace("-EQ", "", regex=False))[
        ["token", "nse_symbol", "name", "exch_seg", "lotsize", "tick_size"]
    ].rename(columns={"token": "smartapi_token"})


def build_isin_map(instruments: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """ISIN -> smartapi_token, joined on NSE symbol."""
    m = universe[["isin", "nse_symbol", "company_name"]].merge(
        instruments[["smartapi_token", "nse_symbol"]], on="nse_symbol", how="left")
    missing = int(m.smartapi_token.isna().sum())
    if missing:
        log.warning("%d/%d universe symbols have no SmartAPI token", missing, len(m))
    return m


def run(out: Path | None = None) -> Path:
    from pipelines.ingestion.reference.universe import load as load_universe

    settings.ensure_dirs()
    out = out or settings.warehouse_dir / "dim_instrument.parquet"
    inst = to_frame(fetch())
    log.info("scrip master: %d NSE equity instruments", len(inst))
    write_parquet(build_isin_map(inst, load_universe()), out)
    return out


if __name__ == "__main__":
    run()
