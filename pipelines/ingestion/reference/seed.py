"""Generate a realistic sample warehouse so the project runs end-to-end offline.

This is NOT a substitute for real AMFI data — it exists so the app is
demonstrable before you complete the ingestion backfill. Every scheme here is
synthetic. Real ingestion writes to the same tables with the same schema.
"""
from __future__ import annotations

import datetime as dt
import uuid

import numpy as np
import pandas as pd

from fundxray_core.config import settings
from fundxray_core.io import write_parquet
from fundxray_core.utils.logging import get_logger

from .universe import load as load_universe

# NOTE: row['isin'] not row.isin — `.isin` is a pandas Series method.

log = get_logger(__name__)
RNG = np.random.default_rng(20260721)

# (scheme_code, scheme_name, amc, category, benchmark, ter_regular, ter_direct, large/mid/small tilt)
SCHEMES = [
    ("FX0001", "Bluewater Large Cap Fund",      "Bluewater AMC", "Large Cap",  "Nifty 100", 1.82, 0.68, (0.95, 0.05, 0.00)),
    ("FX0002", "Bluewater Flexi Cap Fund",      "Bluewater AMC", "Flexi Cap",  "Nifty 500", 1.74, 0.71, (0.78, 0.17, 0.05)),
    ("FX0003", "Northstar Bluechip Fund",       "Northstar AMC", "Large Cap",  "Nifty 100", 1.91, 0.85, (0.97, 0.03, 0.00)),
    ("FX0004", "Northstar Emerging Equity Fund","Northstar AMC", "Mid Cap",    "Nifty Midcap 150", 1.95, 0.92, (0.22, 0.63, 0.15)),
    ("FX0005", "Meridian Multi Cap Fund",       "Meridian AMC",  "Multi Cap",  "Nifty 500", 1.79, 0.74, (0.62, 0.26, 0.12)),
    ("FX0006", "Meridian Small Cap Fund",       "Meridian AMC",  "Small Cap",  "Nifty Smallcap 250", 2.05, 1.02, (0.12, 0.28, 0.60)),
    ("FX0007", "Kaveri Index Fund - Nifty 50",  "Kaveri AMC",    "Index",      "Nifty 50",  0.35, 0.18, (1.00, 0.00, 0.00)),
    ("FX0008", "Kaveri Focused 25 Fund",        "Kaveri AMC",    "Focused",    "Nifty 500", 1.88, 0.79, (0.80, 0.20, 0.00)),
    ("FX0009", "Sentinel Value Discovery Fund", "Sentinel AMC",  "Value",      "Nifty 500", 1.86, 0.81, (0.70, 0.22, 0.08)),
    ("FX0010", "Sentinel ELSS Tax Saver Fund",  "Sentinel AMC",  "ELSS",       "Nifty 500", 1.77, 0.66, (0.75, 0.20, 0.05)),
]

MONTHS = 24


def _dim_scheme() -> pd.DataFrame:
    rows = []
    for code, name, amc, cat, bm, ter_r, ter_d, _ in SCHEMES:
        rows.append(dict(scheme_code=code, scheme_name=name, amc_code=amc.split()[0].upper(),
                         amc_name=amc, category=cat, benchmark=bm,
                         ter_regular_pct=ter_r, ter_direct_pct=ter_d,
                         aum_cr=round(float(RNG.uniform(150, 3500)))))
    return pd.DataFrame(rows)


def _holdings() -> pd.DataFrame:
    uni = load_universe()
    by_cap = {c: uni[uni.cap_bucket == c].reset_index(drop=True) for c in ("large", "mid", "small")}
    end = dt.date.today().replace(day=1)
    months = [(end - pd.DateOffset(months=i)).date() for i in range(MONTHS)][::-1]
    run_id = uuid.uuid4().hex[:12]
    now = dt.datetime.now()

    rows = []
    for code, name, amc, cat, bm, _, _, tilt in SCHEMES:
        n_hold = {"Focused": 25, "Index": 22, "Small Cap": 45}.get(cat, 38)
        for mi, month in enumerate(months):
            # style drift: flexi/multi cap funds creep toward large caps over time
            drift = 0.0
            if cat in ("Flexi Cap", "Multi Cap"):
                drift = 0.012 * mi
            w_large = min(0.98, tilt[0] + drift)
            rem = 1 - w_large
            base = tilt[1] + tilt[2]
            w_mid = rem * (tilt[1] / base) if base else 0.0
            w_small = rem - w_mid

            picks = []
            for bucket, share in (("large", w_large), ("mid", w_mid), ("small", w_small)):
                pool = by_cap[bucket]
                k = max(0, min(len(pool), int(round(n_hold * share))))
                if k == 0:
                    continue
                # Concentration is deliberate: every fund gravitates to the same
                # mega caps. That is the phenomenon the product exposes.
                p = np.linspace(1.6, 0.4, len(pool))
                p = p / p.sum()
                idx = RNG.choice(len(pool), size=k, replace=False, p=p)
                sel = pool.iloc[idx].copy()
                raw = RNG.dirichlet(np.linspace(2.2, 0.6, k)) * share * 100
                sel["weight_pct"] = raw
                picks.append(sel)

            if not picks:
                continue
            h = pd.concat(picks, ignore_index=True)
            h["weight_pct"] *= 96.5 / h["weight_pct"].sum()   # ~3.5% cash

            for _, r in h.iterrows():
                rows.append(dict(
                    scheme_code=code, amc_code=amc.split()[0].upper(),
                    disclosure_month=month, isin=r["isin"], company_id=r["isin"],
                    instrument_name_raw=r.company_name,
                    quantity=round(float(RNG.uniform(1e5, 9e6))),
                    market_value=None, weight_pct=round(float(r.weight_pct), 4),
                    asset_class="equity", source_file="seed://synthetic",
                    ingestion_run_id=run_id, parsed_at=now,
                    resolution_method="seed", resolution_confidence=1.0))
            rows.append(dict(
                scheme_code=code, amc_code=amc.split()[0].upper(),
                disclosure_month=month, isin=None, company_id=None,
                instrument_name_raw="TREPS / Cash & Net Receivables",
                quantity=None, market_value=None, weight_pct=3.5,
                asset_class="cash", source_file="seed://synthetic",
                ingestion_run_id=run_id, parsed_at=now,
                resolution_method="seed", resolution_confidence=1.0))
    return pd.DataFrame(rows)


def _prices(uni: pd.DataFrame) -> pd.DataFrame:
    """Stand-in for SmartAPI historical candles until credentials are configured."""
    rows = []
    for _, r in uni.iterrows():
        turnover_factor = {"large": 0.0040, "mid": 0.0022, "small": 0.0011}[r.cap_bucket]
        adv = r.free_float_shares_cr * 1e7 * turnover_factor
        rows.append(dict(isin=r["isin"], nse_symbol=r.nse_symbol,
                         smartapi_token=r.smartapi_token,
                         close=round(float(RNG.uniform(180, 4200)), 2),
                         adv_shares_30d=float(round(adv)),
                         source="seed://synthetic"))
    return pd.DataFrame(rows)


def run() -> None:
    settings.ensure_dirs()
    wh = settings.warehouse_dir
    uni = load_universe()

    write_parquet(uni, wh / "dim_company.parquet")
    write_parquet(_dim_scheme(), wh / "dim_scheme.parquet")
    h = _holdings()
    write_parquet(h, wh / "silver_holdings.parquet")
    write_parquet(_prices(uni), wh / "fact_price.parquet")

    log.info("seeded %d schemes, %d companies, %d holding rows across %d months",
             len(SCHEMES), len(uni), len(h), MONTHS)


if __name__ == "__main__":
    run()
