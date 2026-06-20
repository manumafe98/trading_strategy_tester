from __future__ import annotations

import pandas as pd
import pytest

from tester_framework.sessions import filter_signals, parse_sessions


def test_parse_sessions_expands_all_and_keeps_custom_ranges():
    sessions = parse_sessions("all,ny=09:30-12:00,NY")
    assert [session["label"] for session in sessions] == ["asia", "london", "ny", "ny=09:30-12:00"]


def test_parse_sessions_rejects_none_with_other_values():
    with pytest.raises(ValueError, match="none must be used by itself"):
        parse_sessions("none,ny")


def test_parse_sessions_rejects_range_outside_preset():
    with pytest.raises(ValueError, match="must stay inside"):
        parse_sessions("london=07:00-10:00")


def test_filter_signals_uses_market_local_time_with_dst():
    london = parse_sessions("london=08:00-09:00")[0]
    signals = pd.DataFrame(
        [
            {"time": pd.Timestamp("2025-03-28 08:30"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-03-31 07:30"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-03-31 09:30"), "side": "long", "stop": 99},
        ]
    )

    filtered = filter_signals(signals, london)

    assert list(filtered["time"]) == [pd.Timestamp("2025-03-28 08:30"), pd.Timestamp("2025-03-31 07:30")]
