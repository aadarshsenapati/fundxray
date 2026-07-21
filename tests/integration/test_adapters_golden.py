"""Golden tests: every adapter, every fixture, every documented quirk.

Adding an AMC means adding a fixture and a row here. If an adapter regresses,
this fails before anything reaches a user.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipelines.ingestion import adapters
from pipelines.ingestion.amfi.disclosures import build_silver, ingest_bronze, parse_month
from pipelines.ingestion.reference.universe import load as load_universe

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "disclosures"

# file, expected adapter, min rows, expected scheme count, quirk under test
CASES = [
    ("bluewater_202606.xlsx", "bluewater", 12, 1, "preamble above header; lakh units"),
    ("northstar_202606.xlsx", "northstar", 16, 2, "two schemes stacked in one sheet"),
    ("meridian_202606.xlsx",  "meridian",  10, 1, "text percentages; Indian digit grouping; footnotes"),
    ("kaveri_202606.xlsx",    "kaveri",     9, 1, "header at row ~12; crore units"),
    ("sentinel_202606.xlsx",  "sentinel",  10, 1, "no ISIN column at all"),
    ("zenith_202606.csv",     "zenith",    11, 1, "CSV; reordered columns; blank ISINs"),
]


@pytest.mark.parametrize("fname,expected_adapter,min_rows,n_schemes,quirk", CASES)
def test_adapter_selection_and_parse(fname, expected_adapter, min_rows, n_schemes, quirk):
    path = FIX / fname
    assert path.exists(), f"missing fixture {fname} — run make_disclosure_fixtures.py"

    adapter = adapters.resolve(path)
    assert adapter.name == expected_adapter, quirk

    rows = list(adapter.parse(path))
    assert len(rows) >= min_rows, quirk
    assert len({r.scheme_name for r in rows}) == n_schemes, quirk


@pytest.mark.parametrize("fname,_a,_m,_n,_q", CASES)
def test_weights_reconcile_to_one_hundred_per_scheme(fname, _a, _m, _n, _q):
    rows = list(adapters.parse(FIX / fname))
    by_scheme: dict[str, float] = {}
    for r in rows:
        by_scheme[r.scheme_name] = by_scheme.get(r.scheme_name, 0.0) + float(r.weight_pct or 0)
    for scheme, total in by_scheme.items():
        assert abs(total - 100.0) < 0.5, f"{scheme} sums to {total}"


def test_unit_scaling_lakhs_vs_crores():
    """Kaveri declares crores, Bluewater lakhs. Both must land in rupees."""
    kaveri = [r for r in adapters.parse(FIX / "kaveri_202606.xlsx")
              if r.asset_class == "equity"]
    blue = [r for r in adapters.parse(FIX / "bluewater_202606.xlsx")
            if r.asset_class == "equity"]
    assert max(float(r.market_value) for r in kaveri) > 1e8   # crores -> rupees
    assert max(float(r.market_value) for r in blue) > 1e8     # lakhs  -> rupees


def test_text_percentages_are_parsed():
    rows = list(adapters.parse(FIX / "meridian_202606.xlsx"))
    assert all(r.weight_pct is not None for r in rows)
    assert all(0 < float(r.weight_pct) < 100 for r in rows)


def test_footnotes_and_totals_are_excluded():
    names = [r.instrument_name_raw.lower()
             for r in adapters.parse(FIX / "meridian_202606.xlsx")]
    assert not any(n.startswith("notes") for n in names)
    assert not any("grand total" in n for n in names)
    blue = [r.instrument_name_raw.lower()
            for r in adapters.parse(FIX / "bluewater_202606.xlsx")]
    assert not any("total" in n for n in blue)


def test_end_to_end_bronze_to_silver_resolves_names_without_isin():
    bronze, quarantine = ingest_bronze(FIX, parse_month("2026-06"))
    assert not quarantine, quarantine
    assert len(bronze) > 60

    silver = build_silver(bronze, load_universe())
    eq = silver[silver.asset_class == "equity"]
    assert eq.company_id.notna().mean() == 1.0, "every equity holding must resolve"

    methods = silver.resolution_method.value_counts().to_dict()
    # Sentinel has no ISIN column and Zenith has blanks: fuzzy matching must fire.
    assert methods.get("fuzzy_name", 0) >= 10
    assert methods.get("isin", 0) >= 40


def test_unrecognised_file_is_quarantined_not_dropped(tmp_path):
    bad = tmp_path / "not_a_disclosure.csv"
    bad.write_text("hello,world\n1,2\n")
    (tmp_path / "bluewater_202606.xlsx").write_bytes(
        (FIX / "bluewater_202606.xlsx").read_bytes())

    bronze, quarantine = ingest_bronze(tmp_path, parse_month("2026-06"))
    assert len(quarantine) == 1
    assert quarantine[0]["file"] == "not_a_disclosure.csv"
    assert len(bronze) > 0          # the good file still ingested
