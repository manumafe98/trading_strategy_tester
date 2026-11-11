"""Breakout timing shared by the three ORB strategies, checked through each generate_signals."""

from __future__ import annotations

import pandas as pd
import pytest

import strategy.orb_candle as orb_candle
import strategy.orb_combined as orb_combined
import strategy.orb_fvg as orb_fvg
from tests.test_orb_combined import make_combined_day
from tester_framework.sessions import parse_sessions


SESSION = parse_sessions("ny=09:30-12:00")[0]
BREAKOUT_TIME = pd.Timestamp("2025-01-02 14:40")
# Base day: ORB 98.5-102.0, long breakout at 14:40 (close 103.0, low 102.5). That bar also completes
# a bull FVG (bottom 100.5, top 102.5, extension 99.5), the newest one when the breakout is confirmed.
STRATEGIES = [
    pytest.param(orb_candle, 102.25, id="orb_candle"),
    pytest.param(orb_fvg, 100.25, id="orb_fvg"),
    pytest.param(orb_combined, 99.25, id="orb_combined"),
]
FVG_STRATEGIES = STRATEGIES[1:]


def set_bar(df: pd.DataFrame, pos: int, open_: float, high: float, low: float, close: float) -> None:
    df.iloc[pos, df.columns.get_indexer(["open", "high", "low", "close"])] = [open_, high, low, close]


def signals_for(module, df, session=SESSION):
    return module.generate_signals(df, "MNQ", "5m", {"tick_size": 0.25, "session": session})


def assert_single_long(signals, stop):
    assert signals["time"].tolist() == [BREAKOUT_TIME]
    assert signals["side"].tolist() == ["long"]
    assert signals["stop"].tolist() == [stop]


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_close_equal_to_opening_range_is_not_a_breakout(module, stop):
    df = make_combined_day()
    set_bar(df, 8, 100.0, 100.5, 98.5, 98.5)  # close == ORB low
    set_bar(df, 9, 100.0, 102.0, 99.5, 102.0)  # close == ORB high

    assert_single_long(signals_for(module, df), stop)


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_first_breakout_of_the_day_wins(module, stop):
    df = make_combined_day()
    set_bar(df, 15, 97.0, 97.5, 96.5, 97.0)  # later short breakout, with its own bear FVG and extension

    assert_single_long(signals_for(module, df), stop)


@pytest.mark.parametrize(("module", "stop"), FVG_STRATEGIES)
def test_fvgs_after_the_breakout_bar_are_ignored(module, stop):
    df = make_combined_day()
    set_bar(df, 11, 103.5, 104.0, 103.0, 103.8)
    set_bar(df, 12, 104.8, 105.0, 104.5, 104.8)  # newer bull FVG (bottom 103.5, extension 103.0)
    df = df.iloc[:13]

    assert_single_long(signals_for(module, df), stop)


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_breakout_at_session_window_end_produces_no_signal(module, stop):
    df = make_combined_day()  # breakout bar starts at 09:40 New York

    assert signals_for(module, df, parse_sessions("ny=09:30-09:40")[0]).empty
    assert_single_long(signals_for(module, df, parse_sessions("ny=09:30-09:41")[0]), stop)


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_orb_strategies_do_not_mutate_input(module, stop):
    df = make_combined_day()
    before = df.copy()
    signals_for(module, df)
    pd.testing.assert_frame_equal(df, before)
