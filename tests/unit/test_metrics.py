import pandas as pd
import pytest

from pipelines.spark.analytics import metrics


def _h(scheme, pairs):
    return pd.DataFrame([
        {"scheme_code": scheme, "company_id": c, "instrument_name_raw": c,
         "asset_class": "equity", "weight_pct": w, "disclosure_month": "2026-06-01"}
        for c, w in pairs])


def test_identical_portfolios_overlap_fully():
    a = _h("A", [("X", 50), ("Y", 50)])
    b = _h("B", [("X", 50), ("Y", 50)])
    assert metrics.overlap(a, b) == pytest.approx(100.0)


def test_disjoint_portfolios_have_zero_overlap():
    assert metrics.overlap(_h("A", [("X", 100)]), _h("B", [("Y", 100)])) == 0.0


def test_partial_overlap_takes_the_minimum():
    a = _h("A", [("X", 60), ("Y", 40)])
    b = _h("B", [("X", 30), ("Z", 70)])
    assert metrics.overlap(a, b) == pytest.approx(30.0)


def test_look_through_weights_are_value_weighted():
    h = pd.concat([_h("A", [("X", 100)]), _h("B", [("X", 50), ("Y", 50)])])
    lt = metrics.look_through({"A": 750_000, "B": 250_000}, h)
    x = lt[lt.company_id == "X"].weight_pct.iloc[0]
    assert x == pytest.approx(87.5)          # 0.75*100 + 0.25*50
    assert lt.weight_pct.sum() == pytest.approx(100.0)


def test_active_share_bounds():
    bm = pd.DataFrame({"isin": ["X", "Y"], "weight_pct": [50.0, 50.0]})
    clone = _h("A", [("X", 50), ("Y", 50)])
    assert metrics.active_share(clone, bm) == pytest.approx(0.0)
    disjoint = _h("A", [("Z", 100)])
    assert metrics.active_share(disjoint, bm) == pytest.approx(100.0)


def test_fee_drag_matches_published_illustration():
    """₹10k/month, 20y, 12% gross: 0.5% TER ≈ ₹92L, 1.5% TER ≈ ₹81L."""
    r = metrics.fee_drag(10_000, 20, 12.0, 0.5, 1.5)
    assert 90e5 < r["terminal_value_a"] < 94e5
    assert 79e5 < r["terminal_value_b"] < 83e5
    assert 9e5 < r["difference"] < 13e5


def test_higher_ter_always_costs_more():
    r = metrics.fee_drag(5_000, 15, 11.0, 0.6, 1.9)
    assert r["difference"] > 0


def test_hhi_is_higher_for_concentrated_portfolios():
    conc = pd.DataFrame([{"company_id": "X", "asset_class": "equity", "weight_pct": 100.0}])
    diver = pd.DataFrame([{"company_id": f"C{i}", "asset_class": "equity",
                           "weight_pct": 10.0} for i in range(10)])
    assert metrics.hhi(conc) > metrics.hhi(diver)
