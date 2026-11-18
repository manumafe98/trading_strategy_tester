"""Intentional differences from the Pine references, documented in README "Differences From Pine"."""

from __future__ import annotations

import pandas as pd
import pytest

from strategy import choch_sr
from strategy.orb_fvg import generate_signals
from strategy.utils.market_structure import market_structure
from tests.choch_sr_bars import long_setup, mirror
from tester_framework.backtest import run_backtest
from tester_framework.models import ExitReason
from tester_framework.sessions import parse_sessions


FLAT = (100.0, 100.0, 100.0, 100.0)
NY_MORNING = parse_sessions("ny=09:30-12:00")[0]


def _run(data, signals, cfg, **overrides):
    kwargs = dict(
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=2, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg, target_fill="touch", intrabar="path",
    )
    kwargs.update(overrides)
    return run_backtest(data, pd.DataFrame(signals), **kwargs)


def _timed_bars(bars):
    """OHLC bars keyed by UTC start time."""
    idx = pd.DatetimeIndex([pd.Timestamp(time) for time in bars])
    return pd.DataFrame(list(bars.values()), index=idx, columns=["open", "high", "low", "close"], dtype=float)


def _minute_bars(start, rows):
    """1m OHLC bars from a UTC start; volume is unused."""
    idx = pd.date_range(start, periods=len(rows), freq="1min")
    data = pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"], dtype=float)
    return data.assign(volume=0)


def _orb_fvg(data, timeframe="5m", session=NY_MORNING):
    return generate_signals(data, asset="MNQ", timeframe=timeframe, params={"tick_size": 0.25, "session": session})


def test_session_exit_closes_at_in_window_close_not_at_ny_day_change(test_asset_cfg):
    # Pine closes at the first bar of the next New York date (00:00 NY on 24h futures data).
    # Our closest equivalent exits at the close of the last bar starting before 17:00 NY.
    data = _timed_bars(
        {
            "2025-01-02 15:00": FLAT,  # 10:00 NY signal
            "2025-01-02 16:00": FLAT,
            "2025-01-02 21:00": (100.0, 100.6, 99.6, 100.5),  # 16:00 NY, last in-window bar
            "2025-01-02 23:00": (100.5, 101.0, 100.5, 101.0),  # 18:00 NY Globex reopen
            "2025-01-03 05:00": (101.0, 102.5, 101.0, 102.0),  # 00:00 NY, Pine's EOD bar
        }
    )
    session = parse_sessions("ny_bank=09:30-17:00")[0]
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, trades = _run(data, signals, test_asset_cfg, session_exit=session)

    trade = trades[0]
    assert (trade["exit_reason"], trade["exit_time"], trade["exit"]) == (
        ExitReason.SESSION, pd.Timestamp("2025-01-02 21:00"), 100.5,
    )


def test_default_target_fill_needs_a_tick_through_and_never_fills_a_gap_better(test_asset_cfg):
    # TradingView fills a limit target on a touch, and at the open when price gaps beyond it.
    # By default we need a trade one tick beyond the target and fill a gap at the target.
    touch_bar = (100.0, 102.0, 99.5, 101.5)  # high equals the 102 2R target
    gap_bar = (104.0, 104.5, 103.5, 104.0)
    data = _timed_bars({"2025-01-02 15:00": FLAT, "2025-01-02 16:00": touch_bar, "2025-01-02 17:00": gap_bar})
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, pine = _run(data, signals, test_asset_cfg)
    _, ours = _run(data, signals, test_asset_cfg, target_fill="tick_through")

    assert (pine[0]["exit_time"], pine[0]["exit"]) == (data.index[1], 102.0)
    assert (ours[0]["exit_time"], ours[0]["exit"], ours[0]["exits"][0]["gap_exit"]) == (data.index[2], 102.0, True)


def test_sizing_discards_below_min_qty_and_has_no_margin_cap(test_asset_cfg):
    # Pine trades max(1, floor(risk / R)) contracts capped by a 5% margin on equity.
    data = _timed_bars({"2025-01-02 15:00": FLAT, "2025-01-02 16:00": (100.0, 102.5, 99.5, 102.0)})
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    # 1% of 50 over R = 1 point is 0.5 contracts: Pine trades 1, we discard.
    small, small_trades = _run(data, signals, test_asset_cfg, capital=50)
    # 5% of 10000 over R = 0.1 point is 5000 contracts; Pine's margin cap is 10000 / (100 * 5%) = 2000.
    _, big_trades = _run(
        data, [{"time": data.index[0], "side": "long", "stop": 99.9}], test_asset_cfg, risk_pct=5, risk_reward_ratio=1,
    )

    assert (small_trades, small["Discarded"]) == ([], 1)
    assert big_trades[0]["qty"] == 5000.0


def _gap_day(start):
    """5m ORB day whose only bull FVG uses the bars at the first three positions after `start`."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * 14
    rows[0] = (99.0, 99.0, 98.5, 99.0)  # high 99.0
    rows[2] = (99.5, 100.5, 99.2, 100.0)  # low 99.2 > 99.0: bull FVG 99.0-99.2
    rows[12] = (100.0, 103.5, 100.0, 103.0)  # breakout close above the 100.5 ORB high, no gap of its own
    return _minute_bars(start, rows)


def test_fvg_never_uses_bars_before_the_session_window():
    # Pine checks high[2] < low on the 09:30 bar, so 09:28 can complete an FVG; we use in-window bars only.
    before_window = _gap_day("2025-01-02 14:28")  # gap bars 09:28 and 09:30 NY
    inside_window = _gap_day("2025-01-02 14:30")  # same shape at 09:30 and 09:32 NY

    assert _orb_fvg(before_window).empty
    assert _orb_fvg(inside_window)["fvg_bottom"].tolist() == [99.0]


def test_fvgs_are_not_capped_at_one_hundred():
    # One bear FVG, then 100 newer bull FVGs, then a short breakout. Pine keeps at most 100 FVGs
    # and evicts the oldest, so it would have no bear FVG left; we keep all of them.
    rows = [
        (75.0, 100.0, 50.0, 75.0),  # 1m ORB 50-100
        (99.2, 99.5, 99.0, 99.2),
        (99.0, 99.5, 98.5, 99.0),
        (97.8, 98.0, 97.5, 97.8),  # bear FVG: low two back 99.0 > high 98.0
        (98.0, 99.0, 60.0, 61.0),  # wide bars step down without forming a gap
        (61.0, 99.0, 60.0, 60.1),
    ]
    for step in range(102):  # rising stairs: a bull FVG on every bar from the third step
        low = 60.5 + 0.3 * step
        rows.append((low + 0.1, low + 0.2, low, low + 0.1))
    rows.append((90.0, 95.0, 48.0, 49.0))  # short breakout below 50 with no gap of its own
    data = _minute_bars("2025-01-02 14:30", rows)

    signals = _orb_fvg(data, timeframe="1m")

    assert signals[["side", "fvg_top", "stop"]].values.tolist() == [["short", 99.0, 99.25]]


def test_fvg_window_follows_the_session_window():
    # Pine detects FVGs only inside its own 09:30-12:00 input; ours follows --sessions.
    rows = [(100.0, 100.5, 99.5, 100.0)] * 160
    rows[150] = (99.5, 99.5, 99.5, 99.5)  # 12:00 NY, inside the ORB
    rows[152] = (100.0, 100.5, 99.8, 100.0)  # 12:02 NY: bull FVG 99.5-99.8
    rows[159] = (100.0, 103.5, 100.0, 103.0)  # 12:09 NY breakout
    data = _minute_bars("2025-01-02 14:30", rows)

    signals = _orb_fvg(data, session=parse_sessions("ny=09:30-13:00")[0])

    assert signals[["side", "fvg_bottom", "time"]].values.tolist() == [["long", 99.5, pd.Timestamp("2025-01-02 17:09")]]


def _choch_bar_breaking_both_swings(opened, high=100.56):
    df = long_setup()
    df.iloc[-1] = [opened, high, 100.10, 100.40, 1.0]
    return df


@pytest.mark.parametrize(
    ("opened", "high", "reflect", "sides"),
    [
        (100.45, 100.56, False, ["long"]),
        (100.20, 100.56, False, ["long"]),
        (100.60, 101.50, False, ["long"]),
        (100.45, 100.56, True, ["short"]),
        (100.20, 100.56, True, ["short"]),
        (100.60, 101.50, True, ["short"]),
    ],
)
def test_double_wick_break_fills_the_choch_that_rested_before_the_bar_opened(opened, high, reflect, sides):
    df = _choch_bar_breaking_both_swings(opened, high)
    params = {**choch_sr.PARAMS, "swing_length": 2, "tick_size": 0.01, "pip_size": 0.01, "entry_mode": "break"}

    signals = choch_sr.generate_signals(mirror(df) if reflect else df, asset="TEST", timeframe="5m", params=params)

    assert list(signals["side"]) == sides


@pytest.mark.parametrize("reflect", [False, True])
def test_double_wick_break_records_both_breaks_in_path_order(reflect):
    df = _choch_bar_breaking_both_swings(100.20)
    choch_bar = market_structure(mirror(df) if reflect else df, 2, "wick").iloc[-1]
    first, second = ("bullish", "bearish") if reflect else ("bearish", "bullish")

    assert (choch_bar[f"{first}_choch"], choch_bar[f"{first}_choch_at_open"]) == (False, False)
    assert (choch_bar[f"{second}_choch"], choch_bar[f"{second}_choch_at_open"]) == (True, True)


@pytest.mark.parametrize("reflect", [False, True])
@pytest.mark.parametrize(("opened", "high"), [(100.45, 100.56), (100.60, 101.50)])
def test_a_break_that_becomes_a_choch_only_inside_the_bar_is_not_a_choch_at_open(opened, high, reflect):
    df = _choch_bar_breaking_both_swings(opened, high)
    choch_bar = market_structure(mirror(df) if reflect else df, 2, "wick").iloc[-1]
    first, second = ("bearish", "bullish") if reflect else ("bullish", "bearish")

    assert (choch_bar[f"{first}_choch"], choch_bar[f"{first}_choch_at_open"]) == (True, True)
    assert (choch_bar[f"{second}_choch"], choch_bar[f"{second}_choch_at_open"]) == (True, False)


def _in_bar_choch_after_a_bullish_break():
    df = long_setup().iloc[:-1]
    rows = [
        [100.40, 100.60, 100.38, 100.55, 1.0],
        [100.55, 100.70, 100.60, 100.65, 1.0],
        [100.65, 100.66, 100.55, 100.60, 1.0],
        [100.60, 100.62, 100.52, 100.58, 1.0],
        [100.20, 100.75, 100.10, 100.40, 1.0],
    ]
    times = pd.date_range(df.index[-1] + pd.Timedelta(minutes=1), periods=len(rows), freq="1min")
    return pd.concat([df, pd.DataFrame(rows, columns=df.columns, index=times)])


@pytest.mark.parametrize(("reflect", "side"), [(False, "long"), (True, "short")])
def test_a_choch_made_by_an_earlier_break_inside_the_same_bar_never_fills(reflect, side):
    df = _in_bar_choch_after_a_bullish_break()
    params = {
        **choch_sr.PARAMS, "swing_length": 2, "tick_size": 0.01, "pip_size": 0.01, "entry_mode": "break", "stop_max": 1.0,
    }

    signals = choch_sr.generate_signals(mirror(df) if reflect else df, asset="TEST", timeframe="5m", params=params)

    assert list(signals["time"]) == [pd.Timestamp("2025-01-06 04:35")]
    assert list(signals["side"]) == [side]