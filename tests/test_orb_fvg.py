from __future__ import annotations

import pandas as pd
import pytest

import plotly.graph_objects as go

from strategy.orb_fvg import generate_signals, plot_indicators
from tester_framework.sessions import parse_sessions


SESSION = parse_sessions("ny=09:30-12:00")[0]


def make_fvg_day(bull=True, with_fvg=True):
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
    if with_fvg:
        if bull:
            df.iloc[2, df.columns.get_loc("high")] = 99.0
            df.iloc[4, df.columns.get_loc("low")] = 101.0
            df.iloc[4, df.columns.get_loc("close")] = 101.5
            df.iloc[4, df.columns.get_loc("high")] = 102.0
            df.iloc[10, df.columns.get_loc("close")] = 103.0
            df.iloc[10, df.columns.get_loc("high")] = 103.5
            df.iloc[10, df.columns.get_loc("low")] = 102.5
        else:
            df.iloc[2, df.columns.get_loc("low")] = 101.0
            df.iloc[4, df.columns.get_loc("high")] = 99.0
            df.iloc[4, df.columns.get_loc("close")] = 98.5
            df.iloc[4, df.columns.get_loc("low")] = 98.0
            df.iloc[10, df.columns.get_loc("close")] = 97.0
            df.iloc[10, df.columns.get_loc("high")] = 97.5
            df.iloc[10, df.columns.get_loc("low")] = 96.5
    else:
        df.iloc[10, :] = [100.0, 100.75, 99.5, 100.6, 0]
    return df


def test_bull_fvg_signal():
    signals = generate_signals(make_fvg_day(bull=True), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["side"] == "long"
    assert row["entry"] == 103.0
    assert row["reason"] == "close_break_above_orb_fvg_cover"
    assert row["stop"] == 100.25
    assert row["fvg_bottom"] == 100.5
    assert "fvg_top" in row.index
    assert "fvg_bottom" in row.index
    assert "fvg_form_time" in row.index
    assert pd.notna(row["fvg_form_time"])


def test_bear_fvg_signal():
    signals = generate_signals(make_fvg_day(bull=False), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["side"] == "short"
    assert row["entry"] == 97.0
    assert row["reason"] == "close_break_below_orb_fvg_cover"
    assert row["stop"] == 99.75
    assert row["fvg_top"] == 99.5


def test_no_signal_without_fvg():
    signals = generate_signals(make_fvg_day(with_fvg=False), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_no_signal_outside_trade_session():
    df = make_fvg_day(bull=True)
    df.index = pd.date_range("2025-01-02 17:00", periods=20, freq="1min")
    signals = generate_signals(df, asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_stop_offset_uses_params_tick_size():
    signals = generate_signals(make_fvg_day(bull=True), asset="MGC", timeframe="5m", params={"tick_size": 0.1, "session": SESSION})
    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["stop"] == 100.4


def test_empty_data():
    signals = generate_signals(pd.DataFrame(), asset="MNQ", timeframe="5m", params={"tick_size": 0.25, "session": SESSION})
    assert signals.empty


def test_requires_positive_tick_size():
    with pytest.raises(ValueError, match="tick_size"):
        generate_signals(make_fvg_day(), asset="MNQ", timeframe="5m", params={"tick_size": 0, "session": SESSION})


@pytest.mark.parametrize(("low_timeframe", "bar_minutes"), [("1m", 1), ("5m", 5)])
def test_fvg_box_spans_22_low_timeframe_bars(low_timeframe, bar_minutes):
    form_time = pd.Timestamp("2025-01-02 14:32")
    trade = {
        "side": "long", "plot_start_time": pd.Timestamp("2025-01-02 14:30"), "signal_time": pd.Timestamp("2025-01-02 14:40"),
        "orb_high": 102.0, "orb_low": 98.5, "fvg_top": 101.0, "fvg_bottom": 99.0, "fvg_form_time": form_time,
        "low_timeframe": low_timeframe,
    }
    fig = go.Figure()

    plot_indicators(fig, None, None, "TEST", "5m", {"trade": trade})

    (box,) = fig.layout.shapes
    assert (box.x0, box.x1) == (form_time, form_time + pd.Timedelta(minutes=22 * bar_minutes))
