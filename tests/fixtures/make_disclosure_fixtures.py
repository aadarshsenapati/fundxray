"""Generate disclosure fixtures replicating real AMC format quirks.

These are SYNTHETIC files, but each reproduces a documented structural quirk
observed in real SEBI-mandated monthly portfolio disclosures. They exist so the
adapter suite can be tested deterministically in CI without redistributing
AMC files. Run: python tests/fixtures/make_disclosure_fixtures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipelines.ingestion.reference.universe import load as load_universe  # noqa: E402

OUT = Path(__file__).parent / "disclosures"
OUT.mkdir(parents=True, exist_ok=True)
U = load_universe()


def _rows(n=12, start=0):
    sub = U.iloc[start:start + n]
    w = [round(x, 4) for x in
         pd.Series(range(n, 0, -1)).div(sum(range(1, n + 1))).mul(96.5)]
    return list(zip(sub.company_name, sub["isin"], w))  # sub.isin is a DataFrame method


def bluewater():
    """Quirk: header at row 5, preamble rows above, clean layout."""
    rows = [["Bluewater Mutual Fund"], ["Monthly Portfolio Statement"],
            ["Bluewater Large Cap Fund"], ["As on 30-Jun-2026"], [],
            ["Name of the Instrument", "ISIN", "Quantity", "Market Value (Rs. in Lakhs)", "% to NAV"]]
    for name, isin, w in _rows(12):
        rows.append([name, isin, 250000, round(w * 1200, 2), w])
    rows.append(["TREPS / Cash & Net Receivables", "", "", 4200.0, 3.5])
    rows.append(["Grand Total", "", "", 120000.0, 100.0])
    pd.DataFrame(rows).to_excel(OUT / "bluewater_202606.xlsx", header=False, index=False)


def northstar():
    """Quirk: TWO stacked schemes in one sheet, scheme name as separator row."""
    rows = [["Northstar Asset Management"], []]
    for scheme, start in (("Northstar Bluechip Fund", 0), ("Northstar Emerging Equity Fund", 10)):
        rows.append([scheme])
        rows.append(["Name of the Instrument", "ISIN", "Quantity", "Market value", "% to Net Assets"])
        for name, isin, w in _rows(8, start):
            rows.append([name, isin, 180000, round(w * 900, 2), w])
        rows.append(["Cash & Cash Equivalent", "", "", 3000.0, 3.5])
        rows.append([])
    pd.DataFrame(rows).to_excel(OUT / "northstar_202606.xlsx", header=False, index=False)


def meridian():
    """Quirk: percentages stored as TEXT with a % suffix; footnote rows at bottom."""
    rows = [["Meridian Multi Cap Fund - Monthly Portfolio"], [],
            ["Name of Instrument", "ISIN", "Qty", "Market Value", "% to NAV"]]
    for name, isin, w in _rows(10, 2):
        rows.append([name, isin, "3,20,000", f"{w * 1100:,.2f}", f"{w}%"])
    rows.append(["Net Receivables / (Payables)", "-", "", "3,500.00", "3.5%"])
    rows.append([])
    rows.append(["Notes:"])
    rows.append(["1. Market value is as on the last business day of the month."])
    rows.append(["2. % to NAV may not total 100 due to rounding."])
    pd.DataFrame(rows).to_excel(OUT / "meridian_202606.xlsx", header=False, index=False)


def kaveri():
    """Quirk: header at row 12, quantity named 'No. of Shares', values in crores."""
    rows = [[f"Kaveri AMC — regulatory filing line {i}"] for i in range(10)]
    rows.append([])
    rows.append(["Name of the Instrument", "ISIN", "No. of Shares",
                 "Market Value (Rs. in Crores)", "% to NAV"])
    for name, isin, w in _rows(9, 1):
        rows.append([name, isin, 410000, round(w * 12, 3), w])
    rows.append(["TREPS", "", "", 4.2, 3.5])
    pd.DataFrame(rows).to_excel(OUT / "kaveri_202606.xlsx", header=False, index=False)


def sentinel():
    """Quirk: legacy layout with NO ISIN column — name-only resolution required."""
    rows = [["Sentinel Value Discovery Fund"], ["Portfolio as on 30-Jun-2026"], [],
            ["Name of the Instrument", "Industry", "Quantity", "Market Value", "% to NAV"]]
    for name, isin, w in _rows(10, 3):
        rows.append([name, "Equity", 275000, round(w * 1000, 2), w])
    rows.append(["Cash and Other Receivables", "", "", 3500.0, 3.5])
    pd.DataFrame(rows).to_excel(OUT / "sentinel_202606.xlsx", header=False, index=False)


def zenith_csv():
    """Quirk: CSV export, reordered columns, some ISINs blank, name variants."""
    recs = []
    for i, (name, isin, w) in enumerate(_rows(11, 4)):
        nm = name.replace(" Ltd", " Limited") if i % 3 == 0 else name.upper()
        recs.append({"% to NAV": w, "Security Name": nm,
                     "ISIN Code": "" if i % 4 == 0 else isin,
                     "Market Value (Rs Lakhs)": round(w * 850, 2), "Quantity": 190000})
    recs.append({"% to NAV": 3.5, "Security Name": "Net Current Assets",
                 "ISIN Code": "", "Market Value (Rs Lakhs)": 2975.0, "Quantity": ""})
    pd.DataFrame(recs).to_csv(OUT / "zenith_202606.csv", index=False)


if __name__ == "__main__":
    for fn in (bluewater, northstar, meridian, kaveri, sentinel, zenith_csv):
        fn()
        print("wrote fixture:", fn.__name__)
