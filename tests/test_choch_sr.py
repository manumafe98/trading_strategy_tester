from __future__ import annotations

import pandas as pd
import pytest

from strategy import choch_sr
from strategy.choch_sr import generate_signals
from strategy.utils.market_structure import market_structure
from tests.choch_sr_bars import long_setup, mirror, repeated_setup, role_flip_setup, shifted_trigger
from tester_framework.sessions import parse_sessions
from tester_framework.strategy_loader import load_strategy


def at(clock: str) -> pd.Timestamp:
    return pd.Timestamp(f"2025-01-06 {clock}")


def run(df, **overrides):
    params = {**choch_sr.PARAMS, "swing_length": 2, "tick_size": 0.01, "pip_size": 0.01, "session": None, **overrides}
    return generate_signals(df, asset="TEST", timeframe="5m", params=params)


def test_support_touch_then_bullish_choch_gives_one_long():
    signals = run(long_setup())

    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["time"] == at("04:35")
    assert row["side"] == "long"
    assert row["entry"] == 100.55
    assert row["stop"] == 100.12  # one tick below the LL at 100.13
    assert row["zone_top"] == 100.2
    assert row["zone_bottom"] == 100.0
    assert row["zone_role"] == "support"
    assert row["touch_time"] == at("04:32")
    assert row["setup_time"] == at("04:34")  # the latest contact, the last bar of the touch's 5-minute bar
    assert row["choch_level"] == 100.52
    assert row["swing_time"] == at("04:30")
    assert row["stop_swing_time"] == at("04:32")


def test_resistance_touch_then_bearish_choch_gives_one_short():
    signals = run(mirror(long_setup()))

    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["time"] == at("04:35")
    assert row["side"] == "short"
    assert row["entry"] == 99.45
    assert row["stop"] == 99.88  # one tick above the HH at 99.87
    assert (row["zone_top"], row["zone_bottom"], row["zone_role"]) == (100.0, 99.8, "resistance")
    assert row["touch_time"] == at("04:32")
    assert row["setup_time"] == at("04:34")
    assert row["choch_level"] == 99.48


def test_choch_without_a_prior_touch_gives_none():
    df = shifted_trigger()
    assert market_structure(df, 2)["bullish_choch"].iloc[-1]

    assert run(df).empty


def test_touch_on_the_choch_bar_itself_gives_none():
    df = shifted_trigger(choch_low=100.15)  # the CHoCH bar's wick is its only contact with the channel

    assert run(df).empty


@pytest.mark.parametrize(
    ("overrides", "trades"),
    [
        ({}, 1),  # the stop is 0.43 below the 100.55 entry: 0.428%
        ({"stop_max": 0.42}, 0),
        ({"stop_min": 0.43}, 0),
        ({"stop_unit": "pips", "stop_min": 0.0, "stop_max": 43.0}, 1),  # 43 pips of 0.01
        ({"stop_unit": "pips", "stop_min": 0.0, "stop_max": 42.0}, 0),
        ({"stop_unit": "pips", "stop_min": 44.0, "stop_max": 50.0}, 0),
    ],
)
def test_setup_is_skipped_when_the_stop_distance_is_outside_the_limits(overrides, trades):
    assert len(run(long_setup(), **overrides)) == trades


def test_role_flip_drops_the_pending_touch():
    inside = run(role_flip_setup(100.05), stop_max=1.0)  # closes inside the channel: the touch stays pending
    flipped = run(role_flip_setup(99.95), stop_max=1.0)  # closes below it: the channel turns resistance

    assert len(inside) == 1
    assert inside.iloc[0]["time"] == at("04:41")
    assert inside.iloc[0]["touch_time"] == at("04:32")
    assert inside.iloc[0]["setup_time"] == at("04:39")  # contact continued through the next 5-minute bar
    assert flipped.empty


def test_signals_keep_coming_after_an_earlier_signal():
    signals = run(repeated_setup())

    assert list(signals["time"]) == [at("04:35"), at("05:57")]
    second = signals.iloc[1]
    assert second["touch_time"] == at("04:32")  # the first touch was never cleared
    assert second["setup_time"] == at("05:54")  # its latest contact is the retest


def test_session_window_does_not_gate_channels_or_touches():
    session = parse_sessions("ny=09:30-12:00")[0]  # excludes every bar of the sample

    pd.testing.assert_frame_equal(run(repeated_setup(), session=session), run(repeated_setup()))


def test_input_is_not_mutated():
    df = long_setup()
    before = df.copy()

    run(df)

    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"swing_length": 0}, "swing_length"),
        ({"zone_unit": "points"}, "zone_unit"),
        ({"stop_unit": "ticks"}, "stop_unit"),
        ({"zone_min_width": -0.1}, "zone_min_width"),
        ({"zone_max_width": 0.0}, "zone_max_width"),
        ({"zone_min_width": 0.5, "zone_max_width": 0.4}, "zone_min_width"),
        ({"stop_min": -1.0}, "stop_min"),
        ({"stop_max": 0.0}, "stop_max"),
        ({"stop_min": 0.6, "stop_max": 0.5}, "stop_min"),
    ],
)
def test_validate_params_rejects_bad_settings(overrides, message):
    with pytest.raises(ValueError, match=message):
        choch_sr.validate_params({**choch_sr.PARAMS, **overrides})


def test_defaults_are_the_pine_defaults():
    assert choch_sr.PARAMS == {
        "swing_length": 20,
        "zone_min_width": 0.1,
        "zone_max_width": 0.4,
        "zone_unit": "%",
        "stop_min": 0.1,
        "stop_max": 0.5,
        "stop_unit": "%",
    }
    assert choch_sr.LOW_TIMEFRAME == "1m"
    choch_sr.validate_params(dict(choch_sr.PARAMS))


def test_strategy_loader_runs_it():
    strategy = load_strategy("choch_sr")
    params = {**strategy.PARAMS, "swing_length": 2, "tick_size": 0.01, "pip_size": 0.01, "session": None}

    assert len(strategy.generate_signals(long_setup(), "TEST", "5m", params)) == 1
