from __future__ import annotations

import pandas as pd
import pytest

from strategy.ema50 import generate_signals


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
