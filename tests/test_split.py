from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from tester_framework.split import oos_cutoff, rank_variants, selection_summary, walk_forward_efficiency


# 48 hourly UTC bars from Monday 2025-01-06 00:00. In New York with an 18:00 session start,
# market sessions start at 23:00 UTC (bars 23 and 47); in UTC with 00:00 they start at bars 0 and 24.
INDEX = pd.date_range("2025-01-06 00:00", periods=48, freq="h")


@pytest.fixture
def new_york_cfg(test_asset_cfg):
    return replace(test_asset_cfg, session_timezone="America/New_York", session_start="18:00")


def test_oos_start_cuts_at_first_bar_of_first_market_session_starting_on_or_after_the_date(test_asset_cfg, new_york_cfg):
    assert oos_cutoff(INDEX, test_asset_cfg, oos_start="2025-01-07") == 24
    # The 2025-01-06 New York market session opens at 18:00 local, 23:00 UTC.
    assert oos_cutoff(INDEX, new_york_cfg, oos_start="2025-01-06") == 23
    assert INDEX[oos_cutoff(INDEX, new_york_cfg, oos_start="2025-01-07")] == pd.Timestamp("2025-01-07 23:00")


def test_oos_fraction_moves_forward_to_next_market_session_start_unless_already_on_one(test_asset_cfg, new_york_cfg):
    # ceil(0.5 * 48) = bar 24: a market session start in UTC, mid-session in New York.
    assert oos_cutoff(INDEX, test_asset_cfg, oos_fraction=0.5) == 24
    assert oos_cutoff(INDEX, new_york_cfg, oos_fraction=0.5) == 47
    # ceil(0.75 * 48) = bar 36, moved to the 23:00 UTC market session start.
    assert oos_cutoff(INDEX, new_york_cfg, oos_fraction=0.25) == 47


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"oos_start": "2025-01-01"}, "in-sample period with no bars"),
        ({"oos_start": "2025-01-08"}, "out-of-sample period with no bars"),
    ],
)
def test_cutoff_that_empties_a_period_is_rejected(test_asset_cfg, kwargs, message):
    with pytest.raises(ValueError, match=message):
        oos_cutoff(INDEX, test_asset_cfg, **kwargs)


def test_fraction_cutoff_past_the_last_market_session_start_is_rejected(new_york_cfg):
    # ceil(0.87 * 46) = bar 41 of 46, inside the last market session, which started at bar 23.
    with pytest.raises(ValueError, match="out-of-sample period with no bars"):
        oos_cutoff(INDEX[:46], new_york_cfg, oos_fraction=0.13)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "--oos_start or --oos_fraction"),
        ({"oos_start": "2025-01-07", "oos_fraction": 0.5}, "not both"),
        ({"oos_fraction": 0.0}, r"between 0 and 1"),
        ({"oos_fraction": 1.0}, r"between 0 and 1"),
        ({"oos_start": "07/01/2025"}, "YYYY-MM-DD"),
    ],
)
def test_invalid_split_arguments_are_rejected(test_asset_cfg, kwargs, message):
    with pytest.raises(ValueError, match=message):
        oos_cutoff(INDEX, test_asset_cfg, **kwargs)


def test_walk_forward_efficiency_matches_a_hand_calculation():
    # In-sample: 100 -> 110 over 2 bars; out-of-sample: 110 -> 121 over 1 bar; 2 bars per year.
    # IS annualized = 1.1 ** (2 / 2) - 1 = 10%; OOS annualized = 1.1 ** (2 / 1) - 1 = 21%; WFE = 210%.
    assert walk_forward_efficiency(np.array([100.0, 105.0, 110.0, 121.0]), 2, 2) == pytest.approx(210.0)


@pytest.mark.parametrize("curve", [[100.0, 100.0, 100.0, 120.0], [100.0, 95.0, 90.0, 120.0]])
def test_walk_forward_efficiency_is_blank_for_zero_or_negative_in_sample_return(curve):
    assert math.isnan(walk_forward_efficiency(np.array(curve), 2, 2))


def _variants(rows):
    return pd.DataFrame(
        [
            {"Strategy": strategy, "Asset": asset, "RR": rr, "Return": ret, "Return / DD": ret_dd, "Sharpe Ratio": sharpe,
             "OOS Return": oos, "OOS Return / DD": oos, "OOS Sharpe": oos}
            for strategy, asset, rr, ret, ret_dd, sharpe, oos in rows
        ]
    )


def test_rank_variants_ranks_each_strategy_and_asset_on_the_chosen_metric_and_sorts_by_is_rank():
    table = _variants(
        [
            ("a", "MGC", "1", 5.0, 1.0, 0.2, 3.0),
            ("a", "MGC", "2", 4.0, 3.0, 0.1, 1.0),
            ("a", "MGC", "3", 3.0, 2.0, 0.3, 2.0),
            ("a", "MNQ", "1", 1.0, 9.0, 0.0, 0.0),
            ("b", "MGC", "1", 1.0, 9.0, 0.0, 0.0),
        ]
    )

    by_return_dd = rank_variants(table, "return_dd", 2)
    assert list(zip(by_return_dd["Strategy"], by_return_dd["Asset"], by_return_dd["RR"], by_return_dd["IS Rank"])) == [
        ("a", "MGC", "2", 1), ("a", "MGC", "3", 2), ("a", "MGC", "1", 3), ("a", "MNQ", "1", 1), ("b", "MGC", "1", 1)
    ]
    assert list(by_return_dd["OOS Rank"]) == [3, 2, 1, 1, 1]
    assert list(by_return_dd["Selected"]) == [True, True, False, True, True]
    assert list(rank_variants(table, "sharpe", 1)["RR"]) == ["3", "1", "2", "1", "1"]
    assert list(rank_variants(table, "return", 1)["RR"]) == ["1", "2", "3", "1", "1"]


def test_rank_variants_uses_net_of_gross_net_pairs_and_keeps_table_order_on_ties():
    table = _variants(
        [
            ("a", "MGC", "1", (9.0, 1.0), (9.0, 1.0), 0.0, (0.0, 2.0)),
            ("a", "MGC", "2", (1.0, 2.0), (1.0, 2.0), 0.0, (5.0, 2.0)),
            ("a", "MGC", "3", (5.0, 2.0), (5.0, 2.0), 0.0, (0.0, 1.0)),
        ]
    )

    ranked = rank_variants(table, "return", 5)
    assert list(ranked["RR"]) == ["2", "3", "1"]
    assert list(ranked["IS Rank"]) == [1, 2, 3]
    # The OOS tie between RR 1 and RR 2 keeps the incoming table order: RR 1 came first.
    assert list(ranked.sort_values("OOS Rank")["RR"]) == ["1", "2", "3"]
    assert ranked["Selected"].all()


def test_rank_variants_ranks_nan_last():
    table = _variants([("a", "MGC", "1", math.nan, math.nan, math.nan, math.nan), ("a", "MGC", "2", -5.0, -5.0, -5.0, -5.0)])

    ranked = rank_variants(table, "return_dd", 1)
    assert list(zip(ranked["RR"], ranked["IS Rank"], ranked["OOS Rank"], ranked["Selected"])) == [
        ("2", 1, 1, True), ("1", 2, 2, False)
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"select_by": "profit"}, "select_by must be one of return_dd, sharpe, return, got: profit"),
        ({"select_top": 0}, "select_top must be a positive integer"),
        ({"select_top": -1}, "select_top must be a positive integer"),
        ({"select_top": 1.5}, "select_top must be a positive integer"),
        ({"select_top": True}, "select_top must be a positive integer"),
    ],
)
def test_rank_variants_rejects_unknown_metric_and_non_positive_top(kwargs, message):
    table = _variants([("a", "MGC", "1", 1.0, 1.0, 1.0, 1.0)])

    with pytest.raises(ValueError, match=message):
        rank_variants(table, **{"select_by": "return_dd", "select_top": 1, **kwargs})


@pytest.mark.parametrize("cutoff", [0, 3, 4, -1])
def test_walk_forward_efficiency_rejects_a_cutoff_outside_the_curve(cutoff):
    # Four curve points are three bars, so the cutoff bar must be 1 or 2.
    with pytest.raises(ValueError, match="cutoff must be between 1 and 2"):
        walk_forward_efficiency(np.array([100.0, 105.0, 110.0, 121.0]), cutoff, 2)


@pytest.mark.parametrize(
    ("curve", "bars_per_year"),
    [
        # Equity of zero at the curve start would divide by zero.
        ([0.0, 10.0, 20.0, 30.0], 2),
        # Both ends negative: the ratio looks like +100% in sample.
        ([-100.0, -150.0, -200.0, 10.0], 2),
        # Negative equity at the cutoff raised to an even power looks positive.
        ([100.0, 50.0, -200.0, 10.0], 4),
    ],
)
def test_walk_forward_efficiency_is_blank_when_equity_at_the_start_or_cutoff_is_not_positive(curve, bars_per_year):
    assert math.isnan(walk_forward_efficiency(np.array(curve), 2, bars_per_year))


def _summary_variants(with_costs):
    # Hand-built out-of-sample curves (gross, net) of three variants of one strategy and asset.
    # RR 3 entered no out-of-sample trades; its OOS Return comes only from a spanning trade.
    curves = {
        "1": ([100, 110, 105], [100, 108, 99], 4),
        "2": ([200, 190, 196], [200, 188, 192], 2),
        "3": ([50, 51, 51], [50, 50.5, 50.5], 0),
    }
    rows = []
    for rr, (gross, net, oos_trades) in curves.items():
        gross_return, net_return = ((curve[-1] / curve[0] - 1) * 100 for curve in (gross, net))
        rows.append({
            "Strategy": "a", "Asset": "MGC", "RR": rr, "OOS Trades": oos_trades,
            "OOS Return": (gross_return, net_return) if with_costs else gross_return,
        })
    rows.append({"Strategy": "a", "Asset": "MNQ", "RR": "1", "OOS Trades": 1, "OOS Return": (4.0, 3.0) if with_costs else 4.0})
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    ("with_costs", "profitable", "equal_weight"),
    [
        # Gross: rescaled ends 1.05, 0.98 and flat 1.00 average to 1.01; only RR 1 is above 0.
        (False, 1 / 3, 1.0),
        # Net: rescaled ends 0.99, 0.96 and flat 1.00 average to 0.98333; none is above 0.
        (True, 0.0, -5 / 3),
    ],
)
def test_selection_summary_counts_variants_profitable_share_and_equal_weight_oos_return(with_costs, profitable, equal_weight):
    summary = selection_summary(_summary_variants(with_costs))

    assert [(group["Strategy"], group["Asset"], group["Tried"]) for group in summary] == [("a", "MGC", 3), ("a", "MNQ", 1)]
    assert summary[0]["OOS profitable"] == pytest.approx(profitable)
    assert summary[0]["Equal-weight OOS"] == pytest.approx(equal_weight)
    assert summary[1]["OOS profitable"] == 1.0
    assert summary[1]["Equal-weight OOS"] == pytest.approx(3.0 if with_costs else 4.0)
