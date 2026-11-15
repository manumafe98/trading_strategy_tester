from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from tester_framework.split import oos_cutoff


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
