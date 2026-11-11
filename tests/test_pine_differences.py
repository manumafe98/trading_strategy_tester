"""Intentional differences from the Pine ORB references, documented in README "Differences From Pine"."""

from __future__ import annotations

import pandas as pd

from strategy.orb_fvg import generate_signals
from tester_framework.backtest import run_backtest
from tester_framework.models import ExitReason
from tester_framework.sessions import parse_sessions


FLAT = (100.0, 100.0, 100.0, 100.0)
NY_MORNING = parse_sessions("ny=09:30-12:00")[0]


def _run(data, signals, cfg, **overrides):
    kwargs = dict(
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=2, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg,
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
