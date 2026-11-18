from __future__ import annotations

import dataclasses

import pandas as pd
import plotly.graph_objects as go
import pytest

from strategy import choch_sr
from strategy.choch_sr import generate_signals, plot_indicators
from strategy.utils.market_structure import market_structure
from tests.choch_sr_bars import (
    RECOVERY_CLOSES,
    TRIGGER_CLOSES,
    append,
    higher_low_setup,
    long_setup,
    mirror,
    repeated_setup,
    role_flip_setup,
    shifted_trigger,
    two_channel_setup,
)
from tester_framework.backtest import run_backtest
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
    assert row["channel_top"] == 100.2
    assert row["channel_bottom"] == 100.0
    assert row["channel_role"] == "support"
    assert row["touch_time"] == at("04:32")
    assert row["setup_time"] == at("04:34")  # the latest contact, the last bar of the touch's 5-minute bar
    assert row["choch_level"] == 100.52
    assert row["swing_time"] == at("04:30")
    assert row["stop_swing_time"] == at("04:32")
    assert row["entry_mode"] == "close"


def test_resistance_touch_then_bearish_choch_gives_one_short():
    signals = run(mirror(long_setup()))

    assert len(signals) == 1
    row = signals.iloc[0]
    assert row["time"] == at("04:35")
    assert row["side"] == "short"
    assert row["entry"] == 99.45
    assert row["stop"] == 99.88  # one tick above the HH at 99.87
    assert (row["channel_top"], row["channel_bottom"], row["channel_role"]) == (100.0, 99.8, "resistance")
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


@pytest.mark.parametrize(("side", "entry", "kept"), [("long", 100.55, True), ("long", 100.12, False), ("short", 103.0, True), ("short", 103.5, False)])
def test_a_stop_on_the_wrong_side_of_the_entry_gives_no_signal(side, entry, kept):
    # On the long setup's CHoCH bar the last LL is 100.13 and the last HH 103.12: stops at 100.12 and 103.13.
    # With no minimum distance, only the side check rejects a stop at or beyond the entry.
    df = long_setup()
    bar = len(df) - 1
    structure = market_structure(df, 2)
    touch = choch_sr.Touch(100.2, 100.0, choch_sr.SUPPORT, bar - 3)
    params = {**choch_sr.PARAMS, "swing_length": 2, "pip_size": 0.01, "stop_min": 0.0, "stop_max": 100.0}

    row = choch_sr.signal_row(structure, bar, side, touch, entry, 103.0, df.index, 0.01, params)

    assert (row is not None) == kept


@pytest.mark.parametrize(
    ("stop_source", "stop", "stop_swing_time", "stop_swing_price"),
    [("last_ll_hh", 100.12, "04:32", 100.13), ("last_swing", 100.19, "04:37", 100.20)],
)
def test_stop_source_picks_the_swing_behind_the_stop(stop_source, stop, stop_swing_time, stop_swing_price):
    (row,) = run(higher_low_setup(), stop_source=stop_source).to_dict("records")

    assert row["stop"] == stop
    assert row["stop_swing_time"] == at(stop_swing_time)
    assert row["stop_swing_price"] == stop_swing_price
    assert row["stop_source"] == stop_source


def test_last_swing_stop_short_is_one_tick_above_the_last_swing_high():
    (row,) = run(mirror(higher_low_setup()), stop_source="last_swing").to_dict("records")

    assert (row["side"], row["stop"], row["stop_swing_price"]) == ("short", 99.81, 99.80)


@pytest.mark.parametrize(("stop_max", "trades"), [(0.36, 1), (0.35, 0)])
def test_last_swing_stop_is_checked_against_the_stop_distance_limits(stop_max, trades):
    # The 100.19 stop is 0.36 below the 100.55 entry: 0.358%.
    assert len(run(higher_low_setup(), stop_source="last_swing", stop_max=stop_max)) == trades


def test_role_flip_drops_the_pending_touch():
    inside = run(role_flip_setup(100.05), stop_max=1.0)  # closes inside the channel: the touch stays pending
    flipped = run(role_flip_setup(99.95), stop_max=1.0)  # closes below it: the channel turns resistance

    assert len(inside) == 1
    assert inside.iloc[0]["time"] == at("04:41")
    assert inside.iloc[0]["touch_time"] == at("04:36")  # the 04:35 five-minute bar opens clear of the channel, so contact restarts
    assert inside.iloc[0]["setup_time"] == at("04:39")  # contact continued through the next 5-minute bar
    assert flipped.empty


def test_signals_keep_coming_after_an_earlier_signal():
    signals = run(repeated_setup())

    assert list(signals["time"]) == [at("04:35"), at("05:57")]
    second = signals.iloc[1]
    assert second["touch_time"] == at("05:54")  # the touch is still pending, but its current contact started at the retest
    assert second["setup_time"] == at("05:54")  # its latest contact is the retest
    assert second["plot_start_time"] == at("05:52")  # the retest's chart opens on its own swing, not at 04:32


def test_channels_are_built_once_per_data_and_channel_settings(monkeypatch):
    calls = []
    build = choch_sr.build_channels
    monkeypatch.setattr(choch_sr, "build_channels", lambda *args: calls.append(args[1:]) or build(*args))
    monkeypatch.setattr(choch_sr, "CHANNEL_CACHE", {})
    df = long_setup()

    first = run(df)
    assert run(df, swing_length=3, stop_max=1.0) is not None  # a sweep over settings channels ignore
    assert run(df).equals(first)
    run(df, channel_max_width=0.5)

    assert len(calls) == 2


def test_contact_on_the_choch_bar_is_not_the_setup_time():
    # A pending touch from the first setup, no contact during the recovery, then a CHoCH whose wick is its only contact.
    lifted = TRIGGER_CLOSES[:7] + [round(close + 0.5, 4) for close in TRIGGER_CLOSES[7:]]
    df = append(long_setup(), RECOVERY_CLOSES + lifted)
    df.iloc[-1, df.columns.get_loc("low")] = 100.15

    second = run(df).iloc[1]

    assert second["time"] == at("05:57")
    assert second["setup_time"] < second["time"]
    assert second["setup_time"] < at("04:45")  # the contact before the recovery, so a retest after an exit is not met


def test_choch_uses_the_matching_touch_with_the_latest_contact():
    signals = run(two_channel_setup())

    assert list(signals["time"]) == [at("08:15"), at("09:37")]
    second = signals.iloc[1]
    # The lower channel's touch from 08:12 is still pending, but the dip into the upper channel is fresher.
    assert (second["channel_top"], second["channel_bottom"]) == (101.7, 101.5)
    assert second["touch_time"] == at("09:34")
    assert second["setup_time"] == at("09:34")


def with_contracts(df, roll_time=None):
    """`df` with a `contract` column: one code throughout, or a second code from `roll_time`."""
    codes = [2 if roll_time is not None and time >= roll_time else 1 for time in df.index]
    return df.assign(contract=codes)


def test_contract_change_mid_setup_gives_no_signal_from_pre_roll_touches():
    rolled = run(with_contracts(long_setup(), roll_time=at("04:35")))  # the roll lands on the CHoCH bar

    assert rolled.empty


def test_data_with_one_contract_or_without_a_contract_column_never_resets():
    assert list(run(with_contracts(long_setup()))["time"]) == [at("04:35")]
    assert list(run(long_setup())["time"]) == [at("04:35")]


def test_contract_change_rebuilds_state_from_the_new_contract_bars():
    first = long_setup()
    second = long_setup()
    offset = first.index[-1] - first.index[0] + pd.Timedelta(minutes=1)
    second.index = second.index + offset
    df = pd.concat([first.assign(contract=1), second.assign(contract=2)])

    signals = run(df)

    assert list(signals["time"]) == [at("04:35"), at("04:35") + offset]
    second_row = signals.iloc[1]
    assert second_row["touch_time"] == at("04:32") + offset  # the first contract's touch did not carry over
    assert (second_row["channel_top"], second_row["channel_bottom"]) == (100.2, 100.0)


@pytest.mark.parametrize(("stop_source", "stop_swing_name"), [("last_ll_hh", "stop swing (LL)"), ("last_swing", "stop swing (swing)")])
def test_trade_chart_shows_the_channel_touch_choch_and_stop_swing(test_asset_cfg, stop_source, stop_swing_name):
    df = append(long_setup(), RECOVERY_CLOSES)  # the long exits at its target during the recovery
    signals = run(df, stop_source=stop_source)
    _, (trade,) = run_backtest(
        df, signals, strategy="choch_sr", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed",
        operation="all", risk_pct=1, capital=10_000, with_costs=False,
        asset_cfg=dataclasses.replace(test_asset_cfg, tick_size=0.01),
    )
    fig = go.Figure()

    plot_indicators(fig, df, df, "TEST", "5m", {"trade": trade, "tick_size": 0.01})

    shapes = {shape.name: shape for shape in fig.layout.shapes}
    channel = shapes["support channel"]
    assert (channel.x0, channel.x1, channel.y0, channel.y1) == (at("04:32"), trade["exit_time"], 100.0, 100.2)
    choch = shapes["CHoCH"]
    assert (choch.x0, choch.x1, choch.y0, choch.y1) == (at("04:30"), at("04:35"), 100.52, 100.52)
    assert choch.label.text == "CHoCH"
    markers = {marker.name: (list(marker.x), list(marker.y)) for marker in fig.data}
    assert markers["touch"] == ([at("04:32")], [100.2])
    assert markers[stop_swing_name] == ([at("04:32")], [100.13])
    assert trade["plot_start_time"] == at("04:30")  # the chart opens on the earliest bar it draws


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
        ({"channel_unit": "points"}, "channel_unit"),
        ({"stop_unit": "ticks"}, "stop_unit"),
        ({"channel_min_width": -0.1}, "channel_min_width"),
        ({"channel_max_width": 0.0}, "channel_max_width"),
        ({"channel_min_width": 0.5, "channel_max_width": 0.4}, "channel_min_width"),
        ({"stop_min": -1.0}, "stop_min"),
        ({"stop_max": 0.0}, "stop_max"),
        ({"stop_min": 0.6, "stop_max": 0.5}, "stop_min"),
        ({"stop_source": "last_hl"}, "stop_source"),
        ({"entry_mode": "touch"}, "entry_mode"),
    ],
)
def test_validate_params_rejects_bad_settings(overrides, message):
    with pytest.raises(ValueError, match=message):
        choch_sr.validate_params({**choch_sr.PARAMS, **overrides})


def test_defaults_are_the_pine_defaults():
    assert choch_sr.PARAMS == {
        "swing_length": 20,
        "channel_min_width": 0.1,
        "channel_max_width": 0.4,
        "channel_unit": "%",
        "stop_min": 0.1,
        "stop_max": 0.5,
        "stop_unit": "%",
        "stop_source": "last_ll_hh",
        "entry_mode": "close",
    }
    assert choch_sr.LOW_TIMEFRAME == "1m"
    choch_sr.validate_params(dict(choch_sr.PARAMS))


def test_strategy_loader_runs_it():
    strategy = load_strategy("choch_sr")
    params = {**strategy.PARAMS, "swing_length": 2, "tick_size": 0.01, "pip_size": 0.01, "session": None}

    assert len(strategy.generate_signals(long_setup(), "TEST", "5m", params)) == 1


def wick_only_break() -> pd.DataFrame:
    df = long_setup()
    df.iloc[-1] = [100.30, 100.56, 100.28, 100.50, 1.0]
    return df


def test_wick_only_break_gives_a_stop_entry_in_break_mode_only():
    assert run(wick_only_break()).empty

    (row,) = run(wick_only_break(), entry_mode="break").to_dict("records")

    assert row["time"] == at("04:35")
    assert row["side"] == "long"
    assert row["stop_entry"] == 100.53
    assert pd.isna(row["entry"])
    assert row["stop"] == 100.12
    assert row["setup_time"] == at("04:34")
    assert row["choch_level"] == 100.52
    assert row["entry_mode"] == "break"


def backtest(df, signals, asset_cfg):
    return run_backtest(
        df, signals, strategy="choch_sr", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed",
        operation="all", risk_pct=1, capital=10_000, with_costs=False,
        asset_cfg=dataclasses.replace(asset_cfg, tick_size=0.01),
    )


@pytest.mark.parametrize(
    ("setup", "fill"),
    [(wick_only_break, 100.53), (long_setup, 100.55)],
)
def test_break_entry_fills_at_the_level_or_at_an_open_beyond_it(test_asset_cfg, setup, fill):
    df = append(setup(), RECOVERY_CLOSES)

    _, (trade,) = backtest(df, run(df, entry_mode="break"), test_asset_cfg)

    assert trade["entry_time"] == at("04:35")
    assert trade["entry"] == fill
    assert trade["entry_mode"] == "break"


def test_break_entry_ignores_a_stop_swing_confirmed_on_the_choch_bar():
    df = long_setup().iloc[:-1].copy()
    df.iloc[-1] = [100.30, 100.56, 100.28, 100.30, 1.0]
    assert market_structure(df, 2, "wick")["last_ll_time"].iloc[-1] == at("04:34")

    assert run(df, entry_mode="break").empty