from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import pytest

from strategy.orb_combined import generate_signals, plot_indicators
from tester_framework.backtest import run_backtest
from tester_framework.sessions import parse_sessions


SESSION = parse_sessions("ny=09:30-12:00")[0]


def make_combined_day(bull=True, with_ext=True):
    """Build a 1m trading day with an ORB, FVG with/without extension, and a breakout."""
    times = pd.date_range("2025-01-02 14:30", periods=20, freq="1min")
    df = pd.DataFrame(
        {
            "open": [100.0] * 20,
            "high": [100.5] * 20,
            "low": [99.5] * 20,
            "close": [100.0] * 20,
            "volume": [0] * 20,
        },
        index=times,
    )
    if bull:
        df.iloc[2, df.columns.get_loc("high")] = 99.0
        df.iloc[3, df.columns.get_loc("low")] = 98.5 if with_ext else 99.5
        df.iloc[4, df.columns.get_loc("low")] = 101.0
        df.iloc[4, df.columns.get_loc("close")] = 101.5
        df.iloc[4, df.columns.get_loc("high")] = 102.0
        if with_ext:
            df.iloc[10, df.columns.get_loc("close")] = 103.0
            df.iloc[10, df.columns.get_loc("high")] = 103.5
            df.iloc[10, df.columns.get_loc("low")] = 102.5
        else:
            df.iloc[10, df.columns.get_loc("close")] = 101.0
            df.iloc[10, df.columns.get_loc("high")] = 101.5
            df.iloc[10, df.columns.get_loc("low")] = 100.0
    else:
        df.iloc[2, df.columns.get_loc("low")] = 101.0
        df.iloc[3, df.columns.get_loc("high")] = 101.5 if with_ext else 100.5
        df.iloc[4, df.columns.get_loc("high")] = 99.0
        df.iloc[4, df.columns.get_loc("close")] = 98.5
        df.iloc[4, df.columns.get_loc("low")] = 98.0
        if with_ext:
            df.iloc[10, df.columns.get_loc("close")] = 97.0
            df.iloc[10, df.columns.get_loc("high")] = 97.5
            df.iloc[10, df.columns.get_loc("low")] = 96.5
        else:
            df.iloc[10, df.columns.get_loc("close")] = 99.0
            df.iloc[10, df.columns.get_loc("high")] = 100.0
            df.iloc[10, df.columns.get_loc("low")] = 98.5
    return df


def test_bull_combined_signal_with_ext():
    signals = generate_signals(make_combined_day(bull=True, with_ext=True), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["side"] == "long"
    assert row["entry"] == 103.0
    assert row["reason"] == "close_break_above_orb_fvg_ext_cover"
    assert row["stop"] == 99.25
    assert row["fvg_ext"] == 99.5
    assert pd.notna(row["fvg_ext"])
    assert "fvg_ext_time" in row.index
    assert pd.notna(row["fvg_ext_time"])


def test_bear_combined_signal_with_ext():
    signals = generate_signals(make_combined_day(bull=False, with_ext=True), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["side"] == "short"
    assert row["entry"] == 97.0
    assert row["reason"] == "close_break_below_orb_fvg_ext_cover"
    assert row["stop"] == 100.75
    assert row["fvg_ext"] == 100.5


def test_combined_accepts_bull_extension_above_gap():
    df = make_combined_day(bull=True, with_ext=True)
    df.iloc[3] = [103.0, 103.5, 102.5, 103.0, 0]
    df.iloc[8, df.columns.get_loc("high")] = 105.0
    df.iloc[10] = [105.0, 105.5, 104.5, 105.0, 0]
    signals = generate_signals(df, asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert len(signals) == 1
    assert signals.iloc[0]["fvg_ext"] == 102.5


def test_combined_accepts_bear_extension_below_gap():
    df = make_combined_day(bull=False, with_ext=True)
    df.iloc[3] = [97.0, 97.5, 96.5, 97.0, 0]
    df.iloc[8, df.columns.get_loc("low")] = 94.0
    df.iloc[10] = [95.0, 95.5, 94.5, 95.0, 0]
    signals = generate_signals(df, asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert len(signals) == 1
    assert signals.iloc[0]["fvg_ext"] == 97.5


def test_no_signal_without_extension():
    signals = generate_signals(make_combined_day(bull=True, with_ext=False), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_no_signal_bear_without_extension():
    signals = generate_signals(make_combined_day(bull=False, with_ext=False), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_no_signal_outside_trade_session():
    df = make_combined_day(bull=True, with_ext=True)
    df.index = pd.date_range("2025-01-02 17:00", periods=20, freq="1min")
    signals = generate_signals(df, asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_tick_size_uses_asset_config():
    signals = generate_signals(make_combined_day(bull=True, with_ext=True), asset="MGC", timeframe="5m", params={"tick_size": 0.1, "session": SESSION})
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["stop"] == 99.4


def test_empty_data():
    signals = generate_signals(pd.DataFrame(), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_requires_positive_tick_size():
    with pytest.raises(ValueError, match="tick_size"):
        generate_signals(make_combined_day(), asset="MNQ", timeframe="5m", params={"tick_size": 0, "session": SESSION})


def test_orb_and_fvg_columns_reach_the_trade_chart(test_asset_cfg):
    df = make_combined_day(bull=True, with_ext=True)
    df.iloc[12, df.columns.get_loc("high")] = 107.0
    signals = generate_signals(df, asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    # The fixture's FVG bars sit outside their open; widen ranges so the engine accepts them.
    prices = df[["open", "high", "low", "close"]]
    bars = df.assign(high=prices.max(axis=1), low=prices.min(axis=1))
    _, (trade,) = run_backtest(
        bars, signals, strategy="orb_combined", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed",
        operation="all", risk_pct=1, capital=10_000, with_costs=False, asset_cfg=test_asset_cfg,
    )
    fig = go.Figure()
    plot_indicators(fig, df, df, "TEST", "1m", {"trade": trade, "tick_size": 0.1})

    assert [(t.name, list(t.y)) for t in fig.data] == [("ORB high", [102.0, 102.0]), ("ORB low", [98.5, 98.5])]
    assert [(s.name, s.x0, s.y0, s.y1) for s in fig.layout.shapes] == [
        ("FVG", pd.Timestamp("2025-01-02 14:38"), 100.5, 102.5),
        ("FVG ext", pd.Timestamp("2025-01-02 14:39"), 99.5, 99.5),
    ]
