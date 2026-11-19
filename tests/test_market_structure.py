from __future__ import annotations

import math

import pandas as pd
import pytest

from strategy.utils.market_structure import market_structure


def make_bars(highs, lows, closes) -> pd.DataFrame:
    times = pd.date_range("2025-01-02 14:30", periods=len(highs), freq="1min")
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes}, index=times)


def labels(series: pd.Series) -> dict[int, str]:
    return {i: value for i, value in enumerate(series) if isinstance(value, str)}


def test_swing_highs_are_labelled_on_their_confirmation_bar():
    df = make_bars(
        highs=[10, 12, 10, 13, 10, 11, 10, 11, 10],
        lows=[5] * 9,
        closes=[9] * 9,
    )

    result = market_structure(df, swing_length=1)

    # The first swing high has no previous one to beat, so it is LH, as in the Pine; an equal high is HH.
    assert labels(result["swing_high_label"]) == {2: "LH", 4: "HH", 6: "LH", 8: "HH"}
    assert result["swing_high_price"].iloc[4] == 13
    assert result["swing_high_time"].iloc[4] == df.index[3]


def test_swing_lows_are_labelled_on_their_confirmation_bar():
    df = make_bars(
        highs=[20] * 9,
        lows=[10, 8, 10, 7, 10, 9, 10, 9, 10],
        closes=[11] * 9,
    )

    result = market_structure(df, swing_length=1)

    # The first swing low has no previous one to stay above, so it is LL, as in the Pine; an equal low is HL.
    assert labels(result["swing_low_label"]) == {2: "LL", 4: "LL", 6: "HL", 8: "HL"}
    assert result["swing_low_price"].iloc[4] == 7
    assert result["swing_low_time"].iloc[4] == df.index[3]


def test_flat_top_swing_high_is_the_later_bar():
    df = make_bars(highs=[10, 12, 12, 10, 10], lows=[5] * 5, closes=[9] * 5)

    result = market_structure(df, swing_length=1)

    assert labels(result["swing_high_label"]) == {3: "LH"}
    assert result["swing_high_time"].iloc[3] == df.index[2]


# Bar 1 is a swing high at 12 and bar 2 a swing low at 7. Bar 4 closes above 12 (the first break, a BOS);
# bar 5 closes below 7, against the previous break, so it is a CHoCH. Bar 5 also confirms bar 4 as an HH at 13.
BREAK_BARS = dict(
    highs=[10, 12, 11, 11.5, 13, 12, 12],
    lows=[8, 9, 7, 9, 10, 6.5, 6],
    closes=[9, 11, 10, 11, 12.5, 6.9, 6.2],
)


def test_first_break_is_bos_and_a_break_the_other_way_is_choch():
    df = make_bars(**BREAK_BARS)

    result = market_structure(df, swing_length=1)

    assert result["bullish_break_price"].dropna().to_dict() == {df.index[4]: 12}
    assert result["bullish_break_swing_time"].iloc[4] == df.index[1]
    assert not result["bullish_choch"].iloc[4]
    assert result["bearish_break_price"].dropna().to_dict() == {df.index[5]: 7}
    assert result["bearish_break_swing_time"].iloc[5] == df.index[2]
    assert result["bearish_choch"].iloc[5]
    assert result["bullish_choch"].sum() + result["bearish_choch"].sum() == 1


def test_a_close_equal_to_the_swing_is_not_a_break():
    bars = {key: list(values) for key, values in BREAK_BARS.items()}
    bars["closes"][4] = 12
    df = make_bars(**bars)

    result = market_structure(df, swing_length=1)

    assert result["bullish_break_price"].isna().all()
    # Bar 5's break is now the first one, so it is a BOS.
    assert result["bearish_break_price"].dropna().to_dict() == {df.index[5]: 7}
    assert not result["bearish_choch"].any()


def test_a_swing_high_breaks_only_once():
    df = make_bars(highs=[10, 12, 11, 13, 14], lows=[8, 9, 9.5, 10, 11], closes=[9, 11, 10, 12.5, 13.5])

    result = market_structure(df, swing_length=1)

    assert result["bullish_break_price"].dropna().to_dict() == {df.index[3]: 12}


def test_a_new_swing_high_rearms_the_bullish_break():
    bars = {key: list(values) + [extra] for (key, values), extra in zip(BREAK_BARS.items(), [14, 7, 13.5])}
    df = make_bars(**bars)

    result = market_structure(df, swing_length=1)

    # Bar 5 confirmed the HH at 13; bar 7 closes above it after the bearish CHoCH.
    assert result["bullish_break_price"].dropna().to_dict() == {df.index[4]: 12, df.index[7]: 13}
    assert result["bullish_break_swing_time"].iloc[7] == df.index[4]
    assert result["bullish_choch"].iloc[7]


def test_last_marked_ll_and_hh_carry_their_confirmation_bar():
    df = make_bars(**BREAK_BARS)

    result = market_structure(df, swing_length=1)

    assert math.isnan(result["last_ll_price"].iloc[2])
    assert result["last_ll_price"].iloc[3:].tolist() == [7] * 4
    assert result["last_ll_time"].iloc[6] == df.index[3]
    # The first swing high is LH, so there is no HH until bar 5 confirms bar 4.
    assert result["last_hh_price"].iloc[:5].isna().all()
    assert result["last_hh_price"].iloc[5] == 13
    assert result["last_hh_time"].iloc[5] == df.index[5]


def test_last_swing_of_any_label_is_kept_after_its_break():
    df = make_bars(**BREAK_BARS)

    result = market_structure(df, swing_length=1)

    assert result["last_swing_high_price"].iloc[4] == 12
    assert result["last_swing_high_time"].iloc[4] == df.index[1]
    assert result["last_swing_high_price"].iloc[5] == 13
    assert result["last_swing_high_time"].iloc[5] == df.index[4]
    assert result["last_swing_low_price"].iloc[6] == 7
    assert result["last_swing_low_time"].iloc[6] == df.index[2]


# Bar 1 is a swing high at 12 and bar 2 a swing low at 7; bar 4's wick goes beyond both, its close beyond neither.
BOTH_SIDES_BARS = dict(
    highs=[10, 12, 11, 11, 13],
    lows=[8, 9, 7, 9, 6],
    closes=[9, 11, 10, 10, 10],
)


def test_wick_confirmation_breaks_both_sides_high_first():
    df = make_bars(**BOTH_SIDES_BARS)

    result = market_structure(df, swing_length=1, confirmation="wick", tick_size=1)

    assert result["bullish_break_price"].iloc[4] == 12
    assert not result["bullish_choch"].iloc[4]
    # The high side is processed first, so the low break on the same bar is against it: a CHoCH.
    assert result["bearish_break_price"].iloc[4] == 7
    assert result["bearish_choch"].iloc[4]


def test_close_confirmation_ignores_wicks():
    df = make_bars(**BOTH_SIDES_BARS)

    result = market_structure(df, swing_length=1)

    assert result["bullish_break_price"].isna().all()
    assert result["bearish_break_price"].isna().all()


def test_each_bar_uses_only_past_and_current_bars():
    df = make_bars(**BREAK_BARS)
    full = market_structure(df, swing_length=1)

    for end in range(1, len(df) + 1):
        partial = market_structure(df.iloc[:end], swing_length=1)
        pd.testing.assert_frame_equal(partial, full.iloc[:end])


def test_input_is_not_mutated():
    df = make_bars(**BREAK_BARS)
    before = df.copy()

    market_structure(df, swing_length=1)

    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"swing_length": 0},
        {"swing_length": 1.0},
        {"swing_length": True},
        {"swing_length": 1, "confirmation": "body"},
        {"swing_length": 1, "confirmation": "wick"},
        {"swing_length": 1, "confirmation": "wick", "tick_size": 0},
    ],
)
def test_invalid_settings_raise(kwargs):
    with pytest.raises(ValueError):
        market_structure(make_bars(**BREAK_BARS), **kwargs)


@pytest.mark.parametrize(
    ("swing_high", "bar_high", "broken"),
    [(100.0, 100.05, False), (100.0, 100.1, True), (100.03, 100.15, False), (100.03, 100.2, True)],
)
def test_wick_confirmation_breaks_a_swing_high_at_its_stop_entry_level(swing_high, bar_high, broken):
    df = make_bars(highs=[99.8, swing_high, 99.9, bar_high], lows=[99.5, 99.6, 99.5, 99.6], closes=[99.7, 99.8, 99.7, 99.8])

    result = market_structure(df, swing_length=1, confirmation="wick", tick_size=0.1)

    assert result["bullish_break_price"].notna().iloc[3] == broken


@pytest.mark.parametrize(
    ("swing_low", "bar_low", "broken"),
    [(100.0, 99.95, False), (100.0, 99.9, True), (99.97, 99.85, False), (99.97, 99.8, True)],
)
def test_wick_confirmation_breaks_a_swing_low_at_its_stop_entry_level(swing_low, bar_low, broken):
    df = make_bars(highs=[100.5, 100.4, 100.5, 100.4], lows=[100.2, swing_low, 100.1, bar_low], closes=[100.3, 100.2, 100.3, 100.2])

    result = market_structure(df, swing_length=1, confirmation="wick", tick_size=0.1)

    assert result["bearish_break_price"].notna().iloc[3] == broken


def test_wick_confirmation_open_short_of_the_stop_entry_level_follows_the_nearer_extreme():
    df = make_bars(highs=[10, 12, 11, 11, 20], lows=[8, 9, 7, 9, 6], closes=[9.0, 11, 10, 10, 10])
    df.iloc[4, df.columns.get_loc("open")] = 12.5

    result = market_structure(df, swing_length=1, confirmation="wick", tick_size=1)

    assert (result["bearish_choch"].iloc[4], result["bullish_choch"].iloc[4]) == (False, True)
