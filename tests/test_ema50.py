from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from strategy.ema50 import generate_signals, plot_indicators


def make_cross(jump: float) -> pd.DataFrame:
    """60 flat bars at 100, then the close jumps by `jump` on bar 60 and stays there."""
    closes = [100.0] * 60 + [100.0 + jump] * 5
    index = pd.date_range("2025-01-01", periods=len(closes), freq="h")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [close + 0.5 for close in closes],
            "low": [close - 0.5 for close in closes],
            "close": closes,
            "volume": [0] * len(closes),
        },
        index=index,
    )


@pytest.mark.parametrize(("jump", "side", "stop"), [(10.0, "long", 99.5), (-10.0, "short", 100.5)])
def test_ema50_signals_on_the_bar_whose_close_crosses(jump, side, stop):
    df = make_cross(jump)
    cross_time = df.index[60]

    signals = generate_signals(df, "MGC", "1h", {"tick_size": 0.1})
    truncated = generate_signals(df.loc[:cross_time], "MGC", "1h", {"tick_size": 0.1})

    for result in (signals, truncated):
        assert result["time"].tolist() == [cross_time]
        assert result["side"].tolist() == [side]
        assert result["stop"].tolist() == [stop]


def test_ema50_does_not_mutate_input():
    df = make_cross(10.0)
    before = df.copy()
    generate_signals(df, "MGC", "1h", {"tick_size": 0.1})
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize(("start", "end"), [(10, 50), (2500, 2600)])
def test_ema50_plotted_ema_in_view_matches_full_data_ema(start, end):
    rng = np.random.default_rng(7)
    closes = 100.0 + rng.normal(0.0, 1.0, 3000).cumsum()
    data = pd.DataFrame({"close": closes}, index=pd.date_range("2025-01-01", periods=len(closes), freq="h"))
    view = data.iloc[start:end]
    fig = go.Figure()

    plot_indicators(fig, data, view, "MGC", "1h", {"tick_size": 0.1})

    expected = data["close"].ewm(span=50, adjust=False).mean().iloc[start:end]
    assert list(fig.data[0].x) == list(view.index)
    np.testing.assert_allclose(fig.data[0].y, expected.to_numpy(), rtol=1e-12, atol=0)
