from __future__ import annotations

import math

import pandas as pd
import pytest

from strategy.utils.sr_channels import build_channels
from tester_framework.data import resample_data


BASE = 90.0
EXTREMES = {10: 100.0, 31: 100.3, 52: 110.0, 73: 80.0}


def make_bars(prices: list[float], start: str = "2024-01-02 00:00", freq: str = "15min") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(prices), freq=freq, name="time")
    return pd.DataFrame({"open": prices, "high": prices, "low": prices, "close": prices, "volume": 1.0}, index=index)


def pivot_bars(n: int = 95, extremes: dict[int, float] = EXTREMES) -> pd.DataFrame:
    return make_bars([extremes.get(i, BASE) for i in range(n)])


def channels_at(result: pd.DataFrame, row: int) -> list[tuple[float, float, float]]:
    found = []
    for slot in range(1, 7):
        top = result[f"top_{slot}"].iloc[row]
        if not math.isnan(top):
            found.append((top, result[f"bottom_{slot}"].iloc[row], result[f"strength_{slot}"].iloc[row]))
    return found


def test_channels_cluster_pivots_and_rank_by_strength():
    result = build_channels(pivot_bars(), min_width=0, max_width=0.4, width_unit="%", pip_size=0.1)

    assert channels_at(result, 84) == [(100.3, 100.0, 42), (80.0, 80.0, 21), (110.0, 110.0, 21)]


def test_channels_in_force_on_a_bar_come_from_the_previous_close():
    result = build_channels(pivot_bars(), min_width=0, max_width=0.4, width_unit="%", pip_size=0.1)

    assert channels_at(result, 83) == [(100.3, 100.0, 42), (110.0, 110.0, 21)]
    assert channels_at(result, 0) == []
    assert math.isnan(result["reference_close"].iloc[0])
    assert result["reference_close"].iloc[53] == 110.0


def test_minimum_width_filter_rejects_narrow_channels_in_both_units():
    expected = [(100.3, 100.0, 42)]

    percent = build_channels(pivot_bars(), min_width=0.1, max_width=0.4, width_unit="%", pip_size=0.1)
    pips = build_channels(pivot_bars(), min_width=2, max_width=4, width_unit="pips", pip_size=0.1)

    assert channels_at(percent, 84) == expected
    assert channels_at(pips, 84) == expected


def test_maximum_width_splits_pivots_and_equal_strengths_keep_the_newer_pivot_first():
    expected = [(80.0, 80.0, 21), (110.0, 110.0, 21), (100.3, 100.3, 21), (100.0, 100.0, 21)]

    percent = build_channels(pivot_bars(), min_width=0, max_width=0.2, width_unit="%", pip_size=0.1)
    pips = build_channels(pivot_bars(), min_width=0, max_width=2, width_unit="pips", pip_size=0.1)

    assert channels_at(percent, 84) == expected
    assert channels_at(pips, 84) == expected


def test_a_candidate_inside_an_accepted_channel_is_dropped():
    bars = pivot_bars(n=74, extremes={10: 100.0, 31: 100.15, 52: 100.3})

    result = build_channels(bars, min_width=0, max_width=0.2, width_unit="%", pip_size=0.1)

    assert channels_at(result, 63) == [(100.3, 100.15, 42)]


def test_maximum_width_is_inclusive_and_measured_from_the_lower_price():
    pips = build_channels(pivot_bars(n=53, extremes={10: 100.0, 31: 100.5}), 0, 2, "pips", 0.25)
    percent = build_channels(pivot_bars(n=53, extremes={10: 100.0, 31: 100.401}), 0, 0.4, "%", 0.1)

    assert channels_at(pips, 42) == [(100.5, 100.0, 42)]
    assert channels_at(percent, 42) == [(100.401, 100.401, 21), (100.0, 100.0, 21)]


@pytest.mark.parametrize(
    ("second_pivot", "expected"),
    [
        (290, [(110.0, 110.0, 21), (100.0, 100.0, 21)]),
        (300, [(110.0, 110.0, 21), (100.0, 100.0, 20)]),
        (301, [(110.0, 110.0, 21)]),
    ],
)
def test_loopback_limits_pivots_and_touches(second_pivot, expected):
    bars = pivot_bars(n=320, extremes={10: 100.0, second_pivot: 110.0})

    result = build_channels(bars, min_width=0, max_width=0.4, width_unit="%", pip_size=0.1)

    assert channels_at(result, second_pivot + 11) == expected


def test_pivot_may_equal_a_bar_on_its_left_but_not_on_its_right_unverified_against_ta_pivothigh():
    left_tie = pivot_bars(n=40, extremes={9: 100.0, 10: 100.0})
    right_tie = pivot_bars(n=40, extremes={10: 100.0, 11: 100.0})

    left = build_channels(left_tie, 0, 0.4, "%", 0.1)
    right = build_channels(right_tie, 0, 0.4, "%", 0.1)

    assert channels_at(left, 21) == [(100.0, 100.0, 22)]
    assert channels_at(right, 21) == []
    assert channels_at(right, 22) == [(100.0, 100.0, 22)]


def test_a_bar_of_the_current_high_timeframe_period_never_affects_its_channels():
    extremes = {10: 100.0, 12: 80.0}
    prices = [extremes.get(i, BASE) for i in range(30) for _ in range(15)]
    minute_bars = make_bars(prices, freq="1min")
    touched = minute_bars.copy()
    touched.iloc[22 * 15 + 14, touched.columns.get_loc("high")] = 100.0

    before = build_channels(resample_data(minute_bars, "15min"), 0, 0.4, "%", 0.1)
    after = build_channels(resample_data(touched, "15min"), 0, 0.4, "%", 0.1)

    assert channels_at(after, 22) == channels_at(before, 22) == [(100.0, 100.0, 21)]
    assert channels_at(before, 23) == [(80.0, 80.0, 21), (100.0, 100.0, 21)]
    assert channels_at(after, 23) == [(100.0, 100.0, 22), (80.0, 80.0, 21)]


@pytest.mark.parametrize(
    ("min_width", "max_width", "width_unit", "pip_size"),
    [(0, 0.4, "ticks", 0.1), (-0.1, 0.4, "%", 0.1), (0, 0, "%", 0.1), (0.5, 0.4, "%", 0.1), (0, 0.4, "pips", 0)],
)
def test_invalid_width_settings_are_rejected(min_width, max_width, width_unit, pip_size):
    with pytest.raises(ValueError):
        build_channels(pivot_bars(), min_width, max_width, width_unit, pip_size)
