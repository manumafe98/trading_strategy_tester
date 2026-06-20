from __future__ import annotations

import pandas as pd
import pytest

from strategy.orb_candle import generate_signals
from tester_framework.sessions import parse_sessions


SESSION = parse_sessions("ny=09:30-12:00")[0]


def make_day(close: float, high: float, low: float) -> pd.DataFrame:
    index = pd.date_range("2025-01-02 14:30", periods=6, freq="1min")
    return pd.DataFrame(
        {
            "open": [100.0] * 5 + [close],
            "high": [100.5, 101.0, 102.0, 101.0, 100.5, high],
            "low": [99.5, 99.0, 99.5, 99.25, 99.5, low],
            "close": [100.0, 100.5, 101.0, 100.5, 100.0, close],
            "volume": [0] * 6,
        },
        index=index,
    )


@pytest.mark.parametrize(
    ("close", "high", "low", "side", "stop"),
    [(103.0, 103.5, 102.5, "long", 102.4), (98.5, 99.5, 98.0, "short", 99.6)],
)
def test_orb_candle_breakout(close, high, low, side, stop):
    signals = generate_signals(
        make_day(close, high, low), asset="MGC", timeframe="5m", params={"tick_size": 0.1, "session": SESSION}
    )
    assert len(signals) == 1
    assert signals.iloc[0]["side"] == side
    assert signals.iloc[0]["stop"] == stop


def test_orb_candle_rejects_daily_opening_range():
    with pytest.raises(ValueError, match="intraday"):
        generate_signals(make_day(103, 103.5, 102.5), "MGC", "1d", {"tick_size": 0.1, "session": SESSION})


def test_orb_candle_requires_session():
    with pytest.raises(ValueError, match="require --sessions"):
        generate_signals(make_day(103, 103.5, 102.5), "MGC", "5m", {"tick_size": 0.1})


def test_orb_candle_rejects_session_without_breakout_time():
    short_session = parse_sessions("ny=09:30-09:35")[0]
    with pytest.raises(ValueError, match="leave time after"):
        generate_signals(make_day(103, 103.5, 102.5), "MGC", "5m", {"tick_size": 0.1, "session": short_session})
