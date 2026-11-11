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


def test_parse_sessions_ny_bank_preset_and_subrange():
    full, subrange = parse_sessions("ny_bank,ny_bank=09:30-17:00")

    assert (full["timezone"], full["start"], full["end"], full["label"]) == ("America/New_York", "08:00", "17:00", "ny_bank")
    assert (subrange["start"], subrange["end"], subrange["label"]) == ("09:30", "17:00", "ny_bank=09:30-17:00")


def test_parse_sessions_rejects_ny_bank_subrange_outside_preset():
    with pytest.raises(ValueError, match="ny_bank range must stay inside 08:00-17:00"):
        parse_sessions("ny_bank=09:30-17:30")
