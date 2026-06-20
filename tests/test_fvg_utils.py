from __future__ import annotations

import pandas as pd

from strategy.utils.fvg import FVG, find_qualifying_fvg, track_fvgs_to_bar


def make_day_df(bars: list[dict], start_minute: int = 570) -> pd.DataFrame:
    """Build a 1m day DataFrame with _ny_minute column from OHLC tuples."""
    times = pd.date_range("2025-01-02 14:30", periods=len(bars), freq="1min")
    df = pd.DataFrame(bars, index=times)
    df["_ny_minute"] = [start_minute + i for i in range(len(bars))]
    return df


def test_bull_fvg_detection():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 100, "close": 101, "volume": 0},
        {"open": 101, "high": 103, "low": 102, "close": 103, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 2)
    assert len(fvgs) == 1
    assert fvgs[0].is_bull
    assert fvgs[0].top == 102
    assert fvgs[0].bottom == 100


def test_bear_fvg_detection():
    bars = [
        {"open": 100, "high": 101, "low": 100, "close": 100, "volume": 0},
        {"open": 100, "high": 100, "low": 99, "close": 99, "volume": 0},
        {"open": 99, "high": 98, "low": 97, "close": 97, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 2)
    assert len(fvgs) == 1
    assert not fvgs[0].is_bull
    assert fvgs[0].top == 100
    assert fvgs[0].bottom == 98


def test_no_fvg_when_overlapping():
    bars = [
        {"open": 100, "high": 102, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 102, "low": 99, "close": 100, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 2)
    assert len(fvgs) == 0


def test_fvg_requires_min_two_bars():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 100, "close": 101, "volume": 0},
    ]
    df = make_day_df(bars)
    assert track_fvgs_to_bar(df, 1) == []


def test_bull_fvg_extension_present():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 98, "close": 98, "volume": 0},
        {"open": 99, "high": 103, "low": 102, "close": 103, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 2)
    assert fvgs[0].ext_price == 98


def test_bull_fvg_no_extension_when_inside_gap():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 101, "close": 101, "volume": 0},
        {"open": 101, "high": 103, "low": 102, "close": 103, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 2)
    assert fvgs[0].ext_price is None


def test_bull_fvg_extension_above_gap_matches_pine():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 103, "high": 104, "low": 103, "close": 103, "volume": 0},
        {"open": 102, "high": 105, "low": 102, "close": 104, "volume": 0},
    ]
    fvgs = track_fvgs_to_bar(make_day_df(bars), 2)
    assert fvgs[0].ext_price == 103


def test_bear_fvg_extension_present():
    bars = [
        {"open": 100, "high": 101, "low": 100, "close": 100, "volume": 0},
        {"open": 100, "high": 103, "low": 102, "close": 102, "volume": 0},
        {"open": 102, "high": 98, "low": 97, "close": 97, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 2)
    assert not fvgs[0].is_bull
    assert fvgs[0].ext_price == 103


def test_bear_fvg_extension_below_gap_matches_pine():
    bars = [
        {"open": 100, "high": 101, "low": 100, "close": 100, "volume": 0},
        {"open": 97, "high": 97, "low": 96, "close": 97, "volume": 0},
        {"open": 98, "high": 98, "low": 95, "close": 96, "volume": 0},
    ]
    fvgs = track_fvgs_to_bar(make_day_df(bars), 2)
    assert fvgs[0].ext_price == 97


def test_bull_fvg_mitigated():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 100, "close": 101, "volume": 0},
        {"open": 101, "high": 103, "low": 102, "close": 103, "volume": 0},
        {"open": 103, "high": 103, "low": 99, "close": 99, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 3)
    assert len(fvgs) == 0


def test_bear_fvg_mitigated():
    bars = [
        {"open": 100, "high": 101, "low": 100, "close": 100, "volume": 0},
        {"open": 100, "high": 100, "low": 99, "close": 99, "volume": 0},
        {"open": 99, "high": 98, "low": 97, "close": 97, "volume": 0},
        {"open": 97, "high": 101, "low": 97, "close": 101, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 3)
    assert len(fvgs) == 0


def test_unmitigated_fvg_survives():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 100, "close": 101, "volume": 0},
        {"open": 101, "high": 103, "low": 102, "close": 103, "volume": 0},
        {"open": 103, "high": 104, "low": 101, "close": 103, "volume": 0},
    ]
    df = make_day_df(bars)
    fvgs = track_fvgs_to_bar(df, 3)
    assert len(fvgs) == 1
    assert fvgs[0].is_bull


def test_session_end_clears_all_fvgs():
    bars = [
        {"open": 100, "high": 100, "low": 99, "close": 100, "volume": 0},
        {"open": 100, "high": 101, "low": 100, "close": 101, "volume": 0},
        {"open": 101, "high": 103, "low": 102, "close": 103, "volume": 0},
    ]
    df = make_day_df(bars, start_minute=717)
    fvgs = track_fvgs_to_bar(df, 2)
    assert len(fvgs) == 1

    bars.append({"open": 103, "high": 104, "low": 102, "close": 103, "volume": 0})
    df = make_day_df(bars, start_minute=717)
    fvgs = track_fvgs_to_bar(df, 3)
    assert len(fvgs) == 0


def test_find_qualifying_fvg_direction_match():
    bull_fvg = FVG(bar_index=5, top=102, bottom=100, is_bull=True, ext_price=98)
    bear_fvg = FVG(bar_index=6, top=104, bottom=102, is_bull=False, ext_price=106)

    assert find_qualifying_fvg([bull_fvg], direction=1) is bull_fvg
    assert find_qualifying_fvg([bear_fvg], direction=-1) is bear_fvg
    assert find_qualifying_fvg([bull_fvg], direction=-1) is None
    assert find_qualifying_fvg([bear_fvg], direction=1) is None


def test_find_qualifying_fvg_newest_first():
    old_fvg = FVG(bar_index=3, top=102, bottom=100, is_bull=True, ext_price=None)
    new_fvg = FVG(bar_index=7, top=105, bottom=103, is_bull=True, ext_price=None)
    assert find_qualifying_fvg([old_fvg, new_fvg], direction=1) is new_fvg


def test_find_qualifying_fvg_require_ext():
    no_ext = FVG(bar_index=5, top=102, bottom=100, is_bull=True, ext_price=None)
    with_ext = FVG(bar_index=3, top=104, bottom=102, is_bull=True, ext_price=98)

    assert find_qualifying_fvg([no_ext], direction=1, require_ext=True) is None
    assert find_qualifying_fvg([no_ext, with_ext], direction=1, require_ext=True) is with_ext
    assert find_qualifying_fvg([no_ext], direction=1, require_ext=False) is no_ext
