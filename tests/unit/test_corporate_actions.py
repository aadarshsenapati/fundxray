import datetime as dt

import pandas as pd
import pytest

from pipelines.ingestion.reference.corporate_actions import (
    CorporateAction,
    adjust_holdings,
    cumulative_factors,
)

SPLIT = CorporateAction("INE040A01034", dt.date(2025, 3, 10), "split", 5.0)
BONUS = CorporateAction("INE040A01034", dt.date(2026, 1, 15), "bonus", 2.0)
MERGER = CorporateAction("INE009A01021", dt.date(2025, 6, 1), "merger", 1.0,
                         new_isin="INE467B01029")


def _holdings():
    return pd.DataFrame([
        {"company_id": "INE040A01034", "disclosure_month": dt.date(2024, 12, 1),
         "quantity": 1000, "weight_pct": 5.0},
        {"company_id": "INE040A01034", "disclosure_month": dt.date(2025, 6, 1),
         "quantity": 5000, "weight_pct": 5.0},
        {"company_id": "INE040A01034", "disclosure_month": dt.date(2026, 6, 1),
         "quantity": 10000, "weight_pct": 5.0},
    ])


def test_cumulative_factor_compounds_across_actions():
    f = cumulative_factors([SPLIT, BONUS], dt.date(2026, 7, 1))
    pre_split = f[f.ex_date == dt.date(2025, 3, 10)].cumulative_factor.iloc[0]
    assert pre_split == pytest.approx(10.0)      # 5 x 2


def test_same_economic_position_across_a_split_and_bonus():
    """The fund never traded; only the share count changed. After adjustment all
    three observations must show the identical position."""
    adj = adjust_holdings(_holdings(), [SPLIT, BONUS], dt.date(2026, 7, 1))
    assert adj.quantity_adjusted.nunique() == 1
    assert adj.quantity_adjusted.iloc[0] == pytest.approx(10000.0)


def test_weights_are_not_touched_by_splits():
    adj = adjust_holdings(_holdings(), [SPLIT, BONUS], dt.date(2026, 7, 1))
    assert (adj.weight_pct == 5.0).all()


def test_merger_remaps_identifier_to_successor():
    h = pd.DataFrame([{"company_id": "INE009A01021",
                       "disclosure_month": dt.date(2025, 1, 1),
                       "quantity": 800, "weight_pct": 2.0}])
    adj = adjust_holdings(h, [MERGER], dt.date(2026, 7, 1))
    assert adj.company_id.iloc[0] == "INE467B01029"
    assert adj.company_id_original.iloc[0] == "INE009A01021"


def test_no_actions_is_a_no_op():
    adj = adjust_holdings(_holdings(), [], dt.date(2026, 7, 1))
    assert (adj.qty_adjustment_factor == 1.0).all()


def test_future_actions_are_ignored():
    f = cumulative_factors([SPLIT, BONUS], as_of=dt.date(2025, 12, 1))
    assert dt.date(2026, 1, 15) not in set(f.ex_date)
