from __future__ import annotations

import pandas as pd
import pytest

from tester_framework.sessions import filter_signals, parse_sessions


def test_parse_sessions_expands_regions_and_keeps_custom_ranges():
    sessions = parse_sessions("regions,ny=09:30-12:00")
    assert [session["label"] for session in sessions] == ["sydney", "asia", "london", "ny", "ny=09:30-12:00"]


def test_parse_sessions_runs_a_window_once_when_regions_repeats_it():
    sessions = parse_sessions("regions,NY")
    assert [session["label"] for session in sessions] == ["sydney", "asia", "london", "ny"]


def test_parse_sessions_rejects_all_and_names_regions():
    with pytest.raises(ValueError, match="all was renamed to regions"):
        parse_sessions("all")


def test_parse_sessions_rejects_custom_range_on_regions():
    with pytest.raises(ValueError, match="regions does not accept a custom time range"):
        parse_sessions("regions=09:30-12:00")


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


def test_parse_sessions_sydney_preset_and_subrange():
    full, subrange = parse_sessions("sydney,sydney=08:00-10:00")

    assert (full["timezone"], full["start"], full["end"], full["label"]) == ("Australia/Sydney", "07:00", "16:00", "sydney")
    assert (subrange["start"], subrange["end"], subrange["label"]) == ("08:00", "10:00", "sydney=08:00-10:00")


def test_parse_sessions_rejects_sydney_subrange_outside_preset():
    with pytest.raises(ValueError, match="sydney range must stay inside 07:00-16:00"):
        parse_sessions("sydney=06:30-10:00")


def test_parse_sessions_unknown_name_error_lists_sydney_and_regions():
    with pytest.raises(ValueError, match="supports only sydney, asia, london, ny, ny_bank, regions, or none"):
        parse_sessions("bogus")


def test_filter_signals_uses_sydney_local_time_across_australian_dst():
    sydney = parse_sessions("sydney")[0]
    signals = pd.DataFrame(
        [
            {"time": pd.Timestamp("2025-04-05 06:30", tz="Australia/Sydney"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-04-05 07:30", tz="Australia/Sydney"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-04-07 06:30", tz="Australia/Sydney"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-04-07 07:30", tz="Australia/Sydney"), "side": "long", "stop": 99},
        ]
    )

    filtered = filter_signals(signals, sydney)

    assert list(filtered["time"]) == [
        pd.Timestamp("2025-04-05 07:30", tz="Australia/Sydney"),
        pd.Timestamp("2025-04-07 07:30", tz="Australia/Sydney"),
    ]
