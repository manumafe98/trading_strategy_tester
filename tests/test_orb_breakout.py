from __future__ import annotations

import pandas as pd
import pytest

import strategy.orb_candle as orb_candle
import strategy.orb_combined as orb_combined
import strategy.orb_fvg as orb_fvg
from tests.orb_days import make_combined_day
from tester_framework.sessions import parse_sessions


SESSION = parse_sessions("ny=09:30-12:00")[0]
NO_BREAK = parse_sessions("no_break")[0]
FULL = parse_sessions("full")[0]
BREAKOUT_TIME = pd.Timestamp("2025-01-02 14:40")
STRATEGIES = [
    pytest.param(orb_candle, 102.25, id="orb_candle"),
    pytest.param(orb_fvg, 100.25, id="orb_fvg"),
    pytest.param(orb_combined, 99.25, id="orb_combined"),
]
FVG_STRATEGIES = STRATEGIES[1:]
FVG_ONLY_STRATEGIES = [
    pytest.param(orb_fvg, 98.75, id="orb_fvg"),
    pytest.param(orb_combined, 98.25, id="orb_combined"),
]


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
    set_bar(df, 8, 100.0, 100.5, 98.5, 98.5)
    set_bar(df, 9, 100.0, 102.0, 99.5, 102.0)

    assert_single_long(signals_for(module, df), stop)


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_first_breakout_of_the_day_wins(module, stop):
    df = make_combined_day()
    set_bar(df, 15, 97.0, 97.5, 96.5, 97.0)

    assert_single_long(signals_for(module, df), stop)


@pytest.mark.parametrize(("module", "stop"), FVG_STRATEGIES)
def test_fvgs_after_the_breakout_bar_are_ignored(module, stop):
    df = make_combined_day()
    set_bar(df, 11, 103.5, 104.0, 103.0, 103.8)
    set_bar(df, 12, 104.8, 105.0, 104.5, 104.8)
    df = df.iloc[:13]

    assert_single_long(signals_for(module, df), stop)


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_breakout_at_session_window_end_produces_no_signal(module, stop):
    df = make_combined_day()

    assert signals_for(module, df, parse_sessions("ny=09:30-09:40")[0]).empty
    assert_single_long(signals_for(module, df, parse_sessions("ny=09:30-09:41")[0]), stop)


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_each_day_gets_its_own_opening_range(module, stop):
    day1 = make_combined_day()
    day2 = make_combined_day() + 10.0
    day2.index += pd.Timedelta(days=1)

    signals = signals_for(module, pd.concat([day1, day2]))

    assert signals["time"].tolist() == [BREAKOUT_TIME, BREAKOUT_TIME + pd.Timedelta(days=1)]
    assert signals["side"].tolist() == ["long", "long"]
    assert signals["stop"].tolist() == [stop, stop + 10.0]
    assert signals["orb_high"].tolist() == [102.0, 112.0]


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_orb_strategies_do_not_mutate_input(module, stop):
    df = make_combined_day()
    before = df.copy()
    signals_for(module, df)
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize("module", [orb_candle, orb_fvg, orb_combined], ids=["orb_candle", "orb_fvg", "orb_combined"])
def test_orb_strategies_reject_a_session_too_short_for_the_opening_range(module):
    with pytest.raises(ValueError, match="leave time after the 5m opening range"):
        signals_for(module, make_combined_day(), parse_sessions("ny=09:30-09:35")[0])


def new_york(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="America/New_York").tz_convert("UTC").tz_localize(None)


def fvg_breakout_day(start: str, breakout: str, breakout_low: float = 102.5) -> pd.DataFrame:
    end = new_york(breakout)
    pattern_start = end - pd.Timedelta(minutes=40)
    day = pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 0},
        index=pd.date_range(new_york(start), end, freq="5min"),
    )
    day.loc[new_york(start)] = [100.0, 102.0, 98.5, 100.0, 0]
    day.loc[pattern_start] = [100.0, 99.0, 98.0, 99.5, 0]
    day.loc[pattern_start + pd.Timedelta(minutes=5)] = [100.0, 100.5, 98.5, 100.0, 0]
    day.loc[pattern_start + pd.Timedelta(minutes=10)] = [100.0, 102.0, 101.0, 101.5, 0]
    day.loc[end] = [100.0, 103.5, breakout_low, 103.0, 0]
    return day


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_no_break_opening_range_is_the_first_bar_from_1815_new_york(module, stop):
    signals = signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-02 19:00"), NO_BREAK)

    assert signals["time"].tolist() == [new_york("2025-01-02 19:00")]
    assert signals["side"].tolist() == ["long"]
    assert signals["stop"].tolist() == [stop]
    assert signals["orb_high"].tolist() == [102.0]
    assert signals["orb_low"].tolist() == [98.5]
    assert signals["plot_start_time"].tolist() == [new_york("2025-01-02 18:15")]


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_breakout_after_midnight_belongs_to_the_window_day_that_took_its_range(module, stop):
    signals = signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-03 00:30"), NO_BREAK)

    assert signals["time"].tolist() == [new_york("2025-01-03 00:30")]
    assert signals["stop"].tolist() == [stop]
    assert signals["orb_high"].tolist() == [102.0]
    assert signals["plot_start_time"].tolist() == [new_york("2025-01-02 18:15")]


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_crossing_windows_on_consecutive_nights_keep_their_own_range(module, stop):
    first = fvg_breakout_day("2025-01-02 18:15", "2025-01-03 00:30")
    second = fvg_breakout_day("2025-01-03 18:15", "2025-01-04 00:30") + 10.0

    signals = signals_for(module, pd.concat([first, second]), NO_BREAK)

    assert signals["time"].tolist() == [new_york("2025-01-03 00:30"), new_york("2025-01-04 00:30")]
    assert signals["orb_high"].tolist() == [102.0, 112.0]
    assert signals["stop"].tolist() == [stop, stop + 10.0]


@pytest.mark.parametrize("module", [orb_candle, orb_fvg, orb_combined], ids=["orb_candle", "orb_fvg", "orb_combined"])
def test_no_break_ignores_a_breakout_in_the_daily_break(module):
    signals = signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-03 16:45"), NO_BREAK)

    assert signals.empty


@pytest.mark.parametrize(("module", "stop"), STRATEGIES)
def test_full_takes_a_breakout_in_the_daily_break(module, stop):
    signals = signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-03 16:45"), FULL)

    assert signals["time"].tolist() == [new_york("2025-01-03 16:45")]
    assert signals["stop"].tolist() == [stop]


@pytest.mark.parametrize(("module", "stop"), FVG_ONLY_STRATEGIES)
def test_fvg_across_midnight_is_found_on_no_break(module, stop):
    signals = signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-03 00:30", breakout_low=100.0), NO_BREAK)

    assert signals["time"].tolist() == [new_york("2025-01-03 00:30")]
    assert signals["stop"].tolist() == [stop]
    assert signals["fvg_form_time"].tolist() == [new_york("2025-01-02 23:50")]


@pytest.mark.parametrize(("module", "stop"), FVG_ONLY_STRATEGIES)
def test_fvg_late_in_no_break_is_found(module, stop):
    signals = signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-03 12:30", breakout_low=100.0), NO_BREAK)

    assert signals["time"].tolist() == [new_york("2025-01-03 12:30")]
    assert signals["stop"].tolist() == [stop]
    assert signals["fvg_form_time"].tolist() == [new_york("2025-01-03 11:50")]


@pytest.mark.parametrize("module", [orb_candle, orb_fvg, orb_combined], ids=["orb_candle", "orb_fvg", "orb_combined"])
@pytest.mark.parametrize("value", ["no_break=23:58-00:02", "no_break=23:55-00:00"])
def test_orb_strategies_reject_a_crossing_session_too_short_for_the_opening_range(module, value):
    with pytest.raises(ValueError, match="leave time after the 5m opening range"):
        signals_for(module, fvg_breakout_day("2025-01-02 18:15", "2025-01-03 00:30"), parse_sessions(value)[0])
