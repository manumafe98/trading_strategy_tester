from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import tester_framework.backtest as backtest
from tester_framework.backtest import (
    add_trade_counts,
    _curve_metrics,
    normalize_signals,
    partial_targets,
    result_columns,
    risk_reward_ratios,
    risk_for,
    run_backtest,
)
from tester_framework.models import ExitReason, Side, StopDistanceLimits
from tester_framework.sessions import parse_sessions
from tester_framework.split import walk_forward_efficiency


def test_risk_reward_ratios_rejects_non_numeric():
    with pytest.raises(ValueError):
        risk_reward_ratios("1RR")


def test_risk_reward_ratios_accepts_positive_numeric():
    assert risk_reward_ratios("1,2,3") == [1.0, 2.0, 3.0]


def test_risk_for_global_and_overrides():
    assets = ("MGC", "MNQ", "MES")
    assert risk_for("MNQ", "1", assets) == 1.0
    assert risk_for("MNQ", "MGC=1,MNQ=0.5", assets) == 0.5
    assert risk_for("MES", "MGC=1,MNQ=0.5", assets) == 1.0


@pytest.mark.parametrize("value", ["abc", "MGC=x", "0", "nan", "inf"])
def test_risk_for_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="--risk"):
        risk_for("MGC", value, ("MGC",))


def test_risk_for_rejects_override_for_asset_not_in_run():
    with pytest.raises(ValueError, match="--risk override for unknown asset: MCG"):
        risk_for("MGC", "MCG=2", ("MGC",))


def test_normalize_signals_rejects_invalid_side():
    with pytest.raises(ValueError):
        normalize_signals(pd.DataFrame([{"time": pd.Timestamp("2025-01-01"), "side": "buy", "stop": 99}]))


def _run(data, signals, cfg, **overrides):
    kwargs = dict(
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg,
    )
    kwargs.update(overrides)
    return run_backtest(data, pd.DataFrame(signals), **kwargs)


def _bars(*bars):
    idx = pd.date_range("2025-01-01", periods=len(bars), freq="h")
    return pd.DataFrame(bars, index=idx, columns=["open", "high", "low", "close"], dtype=float)


@pytest.mark.parametrize(
    ("side", "stop", "move_bar", "target"),
    [
        ("long", 99.0, [100.0, 102.5, 99.5, 102.0], 102.0),
        ("short", 101.0, [100.0, 100.5, 97.5, 98.0], 98.0),
    ],
)
def test_target_hit_exits_at_target_price(test_asset_cfg, side, stop, move_bar, target):
    data = _bars([100.0, 100.0, 100.0, 100.0], move_bar)
    metrics, trades = _run(data, [{"time": data.index[0], "side": side, "stop": stop}], test_asset_cfg, risk_reward_ratio=2)
    trade = trades[0]
    assert (trade["exit_reason"], trade["target"], trade["exit"], trade["qty"]) == (ExitReason.TARGET, target, target, 100.0)
    assert trade["gross_pnl"] == 200.0
    assert metrics["Return"] == 2.0


def test_stop_trade(base_data, hourly_index, test_asset_cfg):
    stop_data = base_data.copy()
    stop_data.loc[hourly_index[1], ["high", "low", "close"]] = [100.5, 98.5, 100.0]
    signals = pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])
    metrics, trades = run_backtest(
        stop_data,
        signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    assert trades[0]["exit_reason"] == ExitReason.STOP
    assert metrics["Return"] < 0


@pytest.mark.parametrize(("side", "stop"), [("long", 99.0), ("short", 101.0)])
def test_equal_distance_tie_reaches_stop_before_target(test_asset_cfg, side, stop):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 101.0, 99.0, 100.0])
    _, trades = _run(data, [{"time": data.index[0], "side": side, "stop": stop}], test_asset_cfg)
    assert (trades[0]["exit_reason"], trades[0]["exit"]) == (ExitReason.STOP, stop)


def test_close_entry_signal_fills_at_signal_price_on_its_own_bar(test_asset_cfg):
    data = _bars([100.0, 101.0, 100.0, 101.0], [101.0, 102.5, 100.5, 102.0])
    _, trades = _run(data, [{"time": data.index[0], "side": "long", "entry": 101.0, "stop": 100.0}], test_asset_cfg)
    trade = trades[0]
    assert (trade["entry_i"], trade["entry"], trade["exit_i"], trade["exit"]) == (0, 101.0, 1, 102.0)
    assert trade["gross_pnl"] == 100.0


def test_trailing_stop(base_data, hourly_index, test_asset_cfg):
    trail_data = base_data.copy()
    trail_data.loc[hourly_index[1], ["open", "high", "low", "close"]] = [100.0, 101.2, 99.5, 101.1]
    trail_data.loc[hourly_index[2], ["open", "high", "low", "close"]] = [101.1, 101.2, 100.6, 100.8]
    signals = pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])
    _, trades = run_backtest(
        trail_data,
        signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=2,
        exit_mode="trailing",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    assert trades[0]["exit_reason"] == ExitReason.STOP
    assert abs(trades[0]["exit"] - 100.7) < 1e-6
    assert abs(trades[0]["realized_r"] - 0.7) < 1e-6
    assert abs(trades[0]["mfe_r"] - 1.2) < 1e-6
    assert abs(trades[0]["giveback_r"] - 0.5) < 1e-6


def test_trailing_stop_updates_at_bar_open(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 99.5, 100.0], [101.5, 102.5, 100.8, 102.0])
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]
    _, trailing = _run(data, signals, test_asset_cfg, risk_reward_ratio=3, exit_mode="trailing")
    _, fixed = _run(data, signals, test_asset_cfg, risk_reward_ratio=3, exit_mode="fixed")
    assert [(t["exit_reason"], t["exit_i"], t["exit"], t["realized_r"]) for t in trailing] == [(ExitReason.STOP, 2, 101.0, 1.0)]
    assert [(t["exit_reason"], t["exit_i"], t["exit"], t["realized_r"]) for t in fixed] == [(ExitReason.END, 2, 102.0, 2.0)]


def test_short_trailing_stop_follows_low_and_never_loosens(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.2, 98.8, 99.0], [99.0, 99.5, 98.9, 99.4])
    _, trades = _run(data, [{"time": data.index[0], "side": "short", "stop": 101.0}], test_asset_cfg, risk_reward_ratio=3, exit_mode="trailing")
    trade = trades[0]
    assert (trade["exit_reason"], trade["exit_i"], trade["exit"]) == (ExitReason.STOP, 2, 99.3)
    assert trade["realized_r"] == pytest.approx(0.7)
    assert trade["mfe_r"] == pytest.approx(1.2)


def test_partial_targets_quantity_distribution():
    assert partial_targets(4, 2.5, 1, 1) == [(1.0, 2), (2.5, 2)]
    assert partial_targets(4, 3, 1, 1) == [(1.0, 1), (2.0, 1), (3, 2)]
    assert not partial_targets(2, 3, 1, 1)


def test_partial_exit_realized_r(test_asset_cfg):
    partial_idx = pd.date_range("2025-01-02", periods=4, freq="h")
    partial_data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 101.0, 102.0],
            "high": [100.0, 101.1, 102.1, 103.1],
            "low": [100.0, 99.5, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [0] * 4,
        },
        index=partial_idx,
    )
    partial_signal = pd.DataFrame([{"time": partial_idx[0], "side": "long", "entry": 100.0, "stop": 99.0}])
    _, partial_trades = run_backtest(
        partial_data,
        partial_signal,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=3,
        exit_mode="partial",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    partial_trade = partial_trades[0]
    assert [fill["qty"] for fill in partial_trade["exits"]] == [33.0, 33.0, 34.0]
    assert abs(partial_trade["realized_r"] - 2.01) < 1e-6
    assert partial_trade["gross_pnl"] == 201.0


def test_partial_stop_aggregation(test_asset_cfg):
    partial_idx = pd.date_range("2025-01-02", periods=4, freq="h")
    partial_data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 101.0, 102.0],
            "high": [100.0, 101.1, 102.1, 103.1],
            "low": [100.0, 99.5, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [0] * 4,
        },
        index=partial_idx,
    )
    partial_signal = pd.DataFrame([{"time": partial_idx[0], "side": "long", "entry": 100.0, "stop": 99.0}])
    partial_stop_data = partial_data.copy()
    partial_stop_data.loc[partial_idx[2], ["open", "high", "low", "close"]] = [100.0, 100.5, 98.5, 100.0]
    _, partial_stop_trades = run_backtest(
        partial_stop_data,
        partial_signal,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=3,
        exit_mode="partial",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    partial_stop = partial_stop_trades[0]
    assert abs(partial_stop["realized_r"] + 0.34) < 1e-6
    assert partial_stop["outcome"] == "loss"
    assert [fill["qty"] for fill in partial_stop["exits"]] == [33.0, 67.0]


def test_costs_keep_raw_execution_and_size_from_net_equity(cost_asset_cfg):
    data = _bars(
        [100.0, 100.0, 100.0, 100.0], [100.0, 101.1, 99.5, 100.0], [100.0, 100.0, 100.0, 100.0],
        [100.0, 100.0, 100.0, 100.0], [100.0, 101.1, 99.5, 100.0], [100.0, 100.0, 100.0, 100.0],
    )
    signals = [
        {"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.0},
        {"time": data.index[3], "side": "long", "entry": 100.0, "stop": 99.0},
    ]
    gross_metrics, gross_trades = _run(data, signals, cost_asset_cfg)
    net_metrics, net_trades = _run(data, signals, cost_asset_cfg, with_costs=True)

    assert [(t["raw_exit"], t["realized_r"], t["qty"]) for t in gross_trades] == [(101.0, 1.0, 100.0), (101.0, 1.0, 101.0)]
    assert [(t["raw_exit"], t["realized_r"], t["qty"]) for t in net_trades] == [(101.0, 1.0, 100.0), (101.0, 1.0, 100.0)]
    assert [t["gross_pnl"] for t in net_trades] == [100.0, 100.0]
    assert [t["net_pnl"] for t in net_trades] == pytest.approx([40.0, 40.0])
    assert gross_metrics["Return"] == 2.01
    assert (net_metrics["Gross"]["Return"], net_metrics["Net"]["Return"], net_metrics["Return"]) == (2.0, 0.8, 0.8)


@pytest.mark.parametrize(
    ("side", "stop", "move_bar", "net_entry", "net_exit"),
    [
        ("long", 99.0, [100.0, 101.5, 99.5, 101.0], 100.2, 100.8),
        ("short", 101.0, [100.0, 100.5, 98.5, 99.0], 99.8, 99.2),
    ],
)
def test_cost_amounts_are_half_spread_plus_slippage_per_side_and_commission_both_sides(
    cost_asset_cfg, side, stop, move_bar, net_entry, net_exit
):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 99.5, 100.0], move_bar)
    metrics, trades = _run(data, [{"time": data.index[0], "side": side, "entry": 100.0, "stop": stop}], cost_asset_cfg, with_costs=True)
    trade = trades[0]
    assert (trade["net_entry"], trade["net_exit"]) == pytest.approx((net_entry, net_exit))
    assert trade["gross_pnl"] == 100.0
    assert trade["net_pnl"] == pytest.approx(40.0)
    assert trade["net_realized_r"] == pytest.approx(0.4)
    assert (metrics["Gross"]["Return"], metrics["Gross"]["Max DD"]) == (1.0, 0.0)
    assert (metrics["Net"]["Return"], metrics["Net"]["Max DD"]) == (0.4, 0.6)


def test_partial_cost_accounting(test_asset_cfg, cost_asset_cfg):
    partial_idx = pd.date_range("2025-01-02", periods=4, freq="h")
    partial_data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 101.0, 102.0],
            "high": [100.0, 101.1, 102.1, 103.1],
            "low": [100.0, 99.5, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [0] * 4,
        },
        index=partial_idx,
    )
    partial_signal = pd.DataFrame([{"time": partial_idx[0], "side": "long", "entry": 100.0, "stop": 99.0}])
    _, gross_partial_trades = run_backtest(
        partial_data,
        partial_signal,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=3,
        exit_mode="partial",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    _, net_partial_trades = run_backtest(
        partial_data,
        partial_signal,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=3,
        exit_mode="partial",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=True,
        asset_cfg=cost_asset_cfg,
    )
    assert net_partial_trades[0]["realized_r"] == gross_partial_trades[0]["realized_r"]
    assert [fill["net_pnl"] for fill in net_partial_trades[0]["exits"]] == pytest.approx([13.2, 46.2, 81.6])
    assert net_partial_trades[0]["gross_pnl"] == 201.0
    assert net_partial_trades[0]["net_pnl"] == pytest.approx(141.0)


def test_empty_signals(base_data, test_asset_cfg):
    metrics, trades = run_backtest(
        base_data,
        pd.DataFrame(),
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    assert not trades
    assert metrics["Return"] == 0
    assert metrics["Max DD"] == 0
    assert math.isnan(metrics["Sharpe Ratio"])
    assert math.isnan(metrics["Return / DD"])


def test_trade_open_at_data_end_exits_at_last_close_with_costs(cost_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 100.0, 100.5])
    metrics, trades = _run(data, [{"time": data.index[0], "side": "long", "stop": 99.0}], cost_asset_cfg, with_costs=True)

    trade = trades[0]
    assert (trade["exit_reason"], trade["exit_time"], trade["exit"]) == (ExitReason.END, data.index[-1], 100.5)
    assert trade["net_exit"] == pytest.approx(100.3)
    assert trade["gross_pnl"] == pytest.approx(50.0)
    assert trade["net_pnl"] == pytest.approx(-10.0)
    assert (metrics["Gross"]["Return"], metrics["Net"]["Return"]) == (0.5, -0.1)


def test_close_entry_on_last_bar_is_discarded(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0])
    metrics, trades = _run(data, [{"time": data.index[-1], "side": "long", "stop": 99.0, "entry": 100.0}], test_asset_cfg)

    assert (metrics["Discarded"], trades) == (1, [])


def test_gap_stop_fills_at_open(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=3, freq="h")
    data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 97.0],
            "high": [100.0, 100.5, 98.0],
            "low": [100.0, 99.5, 96.0],
            "close": [100.0, 100.0, 97.0],
            "volume": [0, 0, 0],
        },
        index=idx,
    )
    _, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99.0}]),
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=3,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    assert trades[0]["exit"] == 97.0


def _gap_run(cfg, gap_bar, side, stop, risk_reward_ratio, exit_mode="fixed", next_bar=None, target_fill="tick_through"):
    bars = [[100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 99.5, 100.0], gap_bar]
    if next_bar:
        bars.append(next_bar)
    idx = pd.date_range("2025-01-01", periods=len(bars), freq="h")
    data = pd.DataFrame(bars, index=idx, columns=["open", "high", "low", "close"])
    _, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": side, "stop": stop}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=risk_reward_ratio, exit_mode=exit_mode,
        operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg, target_fill=target_fill,
    )
    return trades[0]


@pytest.mark.parametrize(
    ("side", "stop", "gap_bar", "reason", "fill"),
    [
        ("long", 99.0, [105.0, 106.0, 104.0, 105.0], ExitReason.TARGET, 105.0),
        ("short", 101.0, [95.0, 96.0, 94.0, 95.0], ExitReason.TARGET, 95.0),
        ("long", 99.0, [97.0, 98.0, 96.0, 97.0], ExitReason.STOP, 97.0),
        ("short", 101.0, [103.0, 104.0, 102.0, 103.0], ExitReason.STOP, 103.0),
    ],
)
def test_gap_through_stop_or_target_fills_at_open_as_gap_exit(test_asset_cfg, side, stop, gap_bar, reason, fill):
    trade = _gap_run(test_asset_cfg, gap_bar, side, stop, risk_reward_ratio=3, target_fill="touch")
    assert [(f["exit_reason"], f["raw_exit"], f["gap_exit"]) for f in trade["exits"]] == [(reason, fill, True)]
    assert trade["exit"] == fill


def test_intrabar_stop_and_target_are_not_gap_exits(test_asset_cfg):
    target = _gap_run(test_asset_cfg, [100.0, 103.5, 99.5, 103.0], "long", 99.0, risk_reward_ratio=3)
    stop = _gap_run(test_asset_cfg, [100.0, 100.5, 98.0, 98.5], "long", 99.0, risk_reward_ratio=3)
    assert [(f["raw_exit"], f["gap_exit"]) for f in target["exits"]] == [(103.0, False)]
    assert [(f["raw_exit"], f["gap_exit"]) for f in stop["exits"]] == [(99.0, False)]


@pytest.mark.parametrize(
    ("side", "stop", "gap_bar", "fill"),
    [
        ("long", 99.0, [105.0, 106.0, 104.0, 105.0], 103.0),
        ("short", 101.0, [95.0, 96.0, 94.0, 95.0], 97.0),
    ],
)
def test_tick_through_gap_beyond_target_fills_at_the_target_as_gap_exit(test_asset_cfg, side, stop, gap_bar, fill):
    trade = _gap_run(test_asset_cfg, gap_bar, side, stop, risk_reward_ratio=3)
    assert [(f["exit_reason"], f["raw_exit"], f["gap_exit"]) for f in trade["exits"]] == [(ExitReason.TARGET, fill, True)]


@pytest.mark.parametrize(
    ("target_fill", "gap_fills"),
    [("touch", (102.5, 102.5)), ("tick_through", (101.0, 102.0))],
)
def test_gap_through_partial_targets_fills_each_crossed_target(test_asset_cfg, target_fill, gap_fills):
    trade = _gap_run(
        test_asset_cfg, [102.5, 102.6, 102.4, 102.5], "long", 99.0, risk_reward_ratio=3, exit_mode="partial",
        next_bar=[102.5, 103.5, 102.4, 103.0], target_fill=target_fill,
    )
    assert [(f["target_r"], f["qty"], f["raw_exit"], f["gap_exit"]) for f in trade["exits"]] == [
        (1.0, 33.0, gap_fills[0], True),
        (2.0, 33.0, gap_fills[1], True),
        (3, 34.0, 103.0, False),
    ]


def test_partial_target_touched_exactly_fills_only_on_touch(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 101.0, 99.5, 100.5], [100.5, 102.1, 100.5, 102.0])
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, touched = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode="partial", target_fill="touch")
    _, ticked = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode="partial")

    assert [(f["target_r"], f["exit_i"]) for f in touched[0]["exits"]] == [(1.0, 1), (2, 2)]
    assert [(f["target_r"], f["exit_i"]) for f in ticked[0]["exits"]] == [(1.0, 2), (2, 2)]


def test_high_first_path_reaches_target_before_stop(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=2, freq="h")
    data = pd.DataFrame(
        {"open": [100, 100], "high": [100, 101.1], "low": [100, 98], "close": [100, 100], "volume": [0, 0]},
        index=idx,
    )
    _, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg,
    )
    assert trades[0]["exit_reason"] == ExitReason.TARGET


def test_short_high_first_path_reaches_stop_before_target(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=2, freq="h")
    data = pd.DataFrame(
        {"open": [100, 100], "high": [100, 101], "low": [100, 98.9], "close": [100, 100], "volume": [0, 0]},
        index=idx,
    )
    _, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": "short", "stop": 101}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg,
    )
    assert trades[0]["exit_reason"] == ExitReason.STOP


def test_partial_peak_and_giveback_are_position_weighted(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=3, freq="h")
    data = pd.DataFrame(
        {
            "open": [100, 100, 99],
            "high": [100, 101, 100],
            "low": [100, 99.5, 98.5],
            "close": [100, 100.5, 99],
            "volume": [0, 0, 0],
        },
        index=idx,
    )
    _, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=2, exit_mode="partial", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg, target_fill="touch",
    )
    trade = trades[0]
    assert trade["realized_r"] == 0
    assert trade["mfe_r"] == 1
    assert trade["giveback_r"] == 1


def test_partial_remainder_open_at_data_end_exits_at_last_close(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 101.0, 99.5, 100.5], [100.5, 101.0, 100.0, 100.5])
    _, trades = _run(
        data, [{"time": data.index[0], "side": "long", "stop": 99.0}], test_asset_cfg,
        risk_reward_ratio=2, exit_mode="partial", target_fill="touch",
    )

    assert [(f["exit_reason"], f["exit_i"], f["raw_exit"]) for f in trades[0]["exits"]] == [
        (ExitReason.TARGET, 1, 101.0), (ExitReason.END, 2, 100.5),
    ]
    assert trades[0]["exit_reason"] == ExitReason.END
    assert trades[0]["realized_r"] == pytest.approx(0.75)


def test_calendar_filters_use_actual_entry_timestamp_and_count_discarded(test_asset_cfg):
    index = pd.date_range("2025-01-31 23:00", periods=3, freq="h")
    data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 100.0],
            "low": [100.0, 99.5, 100.0],
            "close": [100.0, 101.0, 100.0],
            "volume": [0, 0, 0],
        },
        index=index,
    )
    signals = pd.DataFrame([{"time": index[0], "side": "long", "stop": 99}])
    kwargs = dict(
        data=data,
        signals=signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10_000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )

    accepted, trades = run_backtest(**kwargs, days=(5,), months=(2,))
    discarded_by_day, _ = run_backtest(**kwargs, days=(4,), months=(2,))
    discarded_by_month, _ = run_backtest(**kwargs, days=(5,), months=(1,))

    assert len(trades) == 1
    assert accepted["Discarded"] == 0
    assert discarded_by_day["Discarded"] == 1
    assert discarded_by_month["Discarded"] == 1


@pytest.mark.parametrize(("filters", "message"), [({"days": (7,)}, "--days"), ({"months": (0,)}, "--months")])
def test_backtest_rejects_invalid_calendar_filters(base_data, test_asset_cfg, filters, message):
    with pytest.raises(ValueError, match=message):
        run_backtest(
            base_data,
            pd.DataFrame(),
            strategy="test",
            asset="TEST",
            timeframe="1h",
            risk_reward_ratio=1,
            exit_mode="fixed",
            operation="all",
            risk_pct=1,
            capital=10_000,
            with_costs=False,
            asset_cfg=test_asset_cfg,
            **filters,
        )


def test_cost_outcomes_are_paired(cost_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=2, freq="h")
    data = pd.DataFrame(
        {"open": [100, 100], "high": [100, 100.2], "low": [100, 99.95], "close": [100, 100.1], "volume": [0, 0]},
        index=idx,
    )
    metrics, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99.9}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=True, asset_cfg=cost_asset_cfg,
    )
    assert trades[0]["net_pnl"] == pytest.approx(-500.0)
    assert trades[0]["net_realized_r"] == pytest.approx(-5.0)
    row = add_trade_counts(metrics, trades, "all", with_costs=True)
    assert row["W"] == (1, 0)
    assert row["L"] == (0, 1)


def test_first_bar_return_includes_starting_capital(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=2, freq="h")
    data = pd.DataFrame(
        {"open": [100, 100], "high": [101.1, 100], "low": [99.5, 100], "close": [101, 100], "volume": [0, 0]},
        index=idx,
    )
    metrics, _ = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0] - pd.Timedelta(hours=1), "side": "long", "stop": 99}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg,
    )
    assert metrics["Return"] == 1


def test_nonpositive_equity_has_no_sharpe():
    assert math.isnan(_curve_metrics(np.array([100.0, 0.0, 100.0, 100.0]), np.array([1, 2, 3]), 252)["Sharpe Ratio"])


def test_curve_metrics_worked_example():
    assert _curve_metrics(np.array([100.0, 105.0, 110.0, 99.0, 121.0]), np.array([2, 3, 4]), 4) == {
        "Return": 21.0,
        "Max DD": 10.0,
        "Sharpe Ratio": 0.91,
        "Return / DD": 2.1,
    }


def test_make_metrics_reports_net_curve_only_with_costs():
    gross = np.array([100.0, 110.0, 99.0, 121.0])
    net = np.array([100.0, 105.0])

    without_costs = backtest.make_metrics("TEST", "1h", "test", gross, net, False, np.array([1]), 4)
    with_costs = backtest.make_metrics("TEST", "1h", "test", gross, net, True, np.array([1]), 4)

    assert without_costs["Net"] == without_costs["Gross"]
    assert without_costs["Return"] == 21.0
    assert with_costs["Gross"]["Return"] == 21.0
    assert with_costs["Net"]["Return"] == with_costs["Return"] == 5.0


def test_result_columns_follow_operation_and_session():
    assert "Strategy" in result_columns("all")
    assert "Session" in result_columns("all", include_session=True)
    assert "Long" in result_columns("all")
    assert "Long" not in result_columns("long_only")
    assert "Discarded" in result_columns("all")
    assert "Unresolved" not in result_columns("all")


def test_result_columns_append_split_columns_only_in_split_mode():
    split_columns = [
        "OOS Trades", "OOS Return", "OOS Max DD", "OOS Sharpe", "OOS Return / DD", "IS Rank", "OOS Rank", "WFE", "Spanning", "Selected",
    ]
    assert result_columns("all", split=True) == result_columns("all") + split_columns
    assert not set(split_columns) & set(result_columns("all", include_session=True))


def _long_then_short():
    data = _bars(
        [100.0, 100.0, 100.0, 100.0], [100.0, 101.5, 99.8, 101.0], [101.0, 101.0, 101.0, 101.0], [101.0, 101.2, 99.5, 100.0],
    )
    signals = [
        {"time": data.index[0], "side": "long", "stop": 99.0},
        {"time": data.index[2], "side": "short", "stop": 102.0},
    ]
    return data, signals


@pytest.mark.parametrize(
    ("operation", "sides"),
    [("all", [Side.LONG, Side.SHORT]), ("long_only", [Side.LONG]), ("short_only", [Side.SHORT])],
)
def test_operation_filters_signal_sides(test_asset_cfg, operation, sides):
    data, signals = _long_then_short()
    metrics, trades = _run(data, signals, test_asset_cfg, operation=operation)
    assert [(t["side"], t["exit_reason"]) for t in trades] == [(side, ExitReason.TARGET) for side in sides]
    assert metrics["Discarded"] == 0


def test_add_trade_counts_splits_sides(test_asset_cfg):
    data, signals = _long_then_short()
    metrics, trades = _run(data, signals, test_asset_cfg)
    row = add_trade_counts(metrics, trades, "all")
    assert (row["Trades"], row["Long"], row["Short"]) == (2, 1, 1)


@pytest.mark.parametrize(("side", "stop"), [("long", 101.0), ("long", 100.0), ("short", 99.0), ("short", 100.0)])
def test_stop_on_wrong_side_of_entry_is_discarded(test_asset_cfg, side, stop):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 102.5, 97.5, 100.0])
    metrics, trades = _run(data, [{"time": data.index[0], "side": side, "entry": 100.0, "stop": stop}], test_asset_cfg)
    assert trades == []
    assert metrics["Discarded"] == 1


def test_quantity_below_minimum_is_discarded(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 101.5, 99.5, 101.0])
    signals = [{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.0}]
    at_min, traded = _run(data, signals, replace(test_asset_cfg, min_qty=100.0))
    below, trades = _run(data, signals, replace(test_asset_cfg, min_qty=101.0))
    assert ([t["qty"] for t in traded], at_min["Discarded"]) == ([100.0], 0)
    assert (trades, below["Discarded"]) == ([], 1)


def test_signal_while_trade_is_open_is_discarded(test_asset_cfg):
    data = _bars(
        [100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 99.5, 100.0], [100.0, 101.5, 99.5, 101.0], [101.0, 102.5, 100.5, 102.0],
    )
    signals = [
        {"time": data.index[0], "side": "long", "stop": 99.0},
        {"time": data.index[1], "side": "long", "stop": 99.0},
    ]
    metrics, trades = _run(data, signals, test_asset_cfg)
    assert [(t["entry_i"], t["exit_i"]) for t in trades] == [(1, 2)]
    assert metrics["Discarded"] == 1


def test_close_entry_time_not_on_a_bar_is_discarded(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 101.5, 99.5, 101.0], [101.0, 102.5, 100.5, 102.0])
    signals = [{"time": data.index[0] + pd.Timedelta(minutes=30), "side": "long", "entry": 100.0, "stop": 99.0}]
    metrics, trades = _run(data, signals, test_asset_cfg)
    assert trades == []
    assert metrics["Discarded"] == 1


@pytest.mark.parametrize(
    ("side", "stop", "move_bar", "rounded_stop", "rounded_target"),
    [
        ("long", 99.1, [100.0, 101.5, 99.5, 101.0], 99.0, 101.25),
        ("short", 100.9, [100.0, 100.5, 98.5, 99.0], 101.0, 98.75),
    ],
)
def test_off_tick_stop_and_target_round_away_from_entry(test_asset_cfg, side, stop, move_bar, rounded_stop, rounded_target):
    cfg = replace(test_asset_cfg, tick_size=0.25)
    data = _bars([100.0, 100.0, 100.0, 100.0], move_bar)
    _, trades = _run(data, [{"time": data.index[0], "side": side, "entry": 100.0, "stop": stop}], cfg, risk_reward_ratio=1.1)
    trade = trades[0]
    assert (trade["stop"], trade["initial_risk"], trade["target"]) == (rounded_stop, 1.0, rounded_target)
    assert (trade["exit_reason"], trade["exit"]) == (ExitReason.TARGET, rounded_target)


def test_risk_sizing_uses_point_value_and_floors_to_qty_step(test_asset_cfg):
    cfg = replace(test_asset_cfg, point_value=5.0, qty_step=0.1, min_qty=0.1)
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 103.5, 99.5, 103.0])
    metrics, trades = _run(data, [{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 97.0}], cfg)
    trade = trades[0]
    assert trade["qty"] == 6.6
    assert trade["gross_pnl"] == pytest.approx(99.0)
    assert metrics["Return"] == 0.99


def test_max_trades_caps_closed_trades(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=6, freq="h")
    data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 100.0, 102.0, 100.0, 102.0],
            "low": [100.0, 99.5, 100.0, 99.5, 100.0, 99.5],
            "close": [100.0, 101.0, 100.0, 101.0, 100.0, 101.0],
            "volume": [0, 0, 0, 0, 0, 0],
        },
        index=idx,
    )
    signals = pd.DataFrame(
        [
            {"time": idx[0], "side": "long", "stop": 99},
            {"time": idx[2], "side": "long", "stop": 99},
            {"time": idx[4], "side": "long", "stop": 99},
        ]
    )
    _, trades = run_backtest(
        data,
        signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
        max_trades=2,
    )
    assert len(trades) == 2


def test_max_trades_none_runs_all(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=6, freq="h")
    data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 100.0, 102.0, 100.0, 102.0],
            "low": [100.0, 99.5, 100.0, 99.5, 100.0, 99.5],
            "close": [100.0, 101.0, 100.0, 101.0, 100.0, 101.0],
            "volume": [0, 0, 0, 0, 0, 0],
        },
        index=idx,
    )
    signals = pd.DataFrame(
        [
            {"time": idx[0], "side": "long", "stop": 99},
            {"time": idx[2], "side": "long", "stop": 99},
            {"time": idx[4], "side": "long", "stop": 99},
        ]
    )
    _, trades = run_backtest(
        data,
        signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    assert len(trades) == 3


@pytest.mark.parametrize("max_trades", [0, -1])
def test_max_trades_rejects_nonpositive_values(base_data, test_asset_cfg, max_trades):
    with pytest.raises(ValueError, match="--max_trades"):
        run_backtest(
            base_data,
            pd.DataFrame(),
            strategy="test",
            asset="TEST",
            timeframe="1h",
            risk_reward_ratio=1,
            exit_mode="fixed",
            operation="all",
            risk_pct=1,
            capital=10000,
            with_costs=False,
            asset_cfg=test_asset_cfg,
            max_trades=max_trades,
        )


def _two_winning_close_entries(cfg):
    idx = pd.date_range("2025-01-01", periods=5, freq="h")
    data = pd.DataFrame(
        {
            "open": [100, 100, 101, 101, 102],
            "high": [100, 101.5, 101, 102.5, 102],
            "low": [100, 100, 101, 101, 102],
            "close": [100, 101, 101, 102, 102],
            "volume": [0] * 5,
        },
        index=idx,
        dtype=float,
    )
    signals = pd.DataFrame(
        [
            {"time": idx[0], "side": "long", "entry": 100.0, "stop": 99.0},
            {"time": idx[2], "side": "long", "entry": 101.0, "stop": 100.0},
        ]
    )
    return run_backtest(
        data, signals, strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed",
        operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg,
    )


def test_close_entry_winning_trades_have_no_drawdown(test_asset_cfg):
    metrics, trades = _two_winning_close_entries(test_asset_cfg)

    assert [(trade["entry_i"], trade["exit_i"], trade["exit_reason"]) for trade in trades] == [
        (0, 1, ExitReason.TARGET),
        (2, 3, ExitReason.TARGET),
    ]
    assert metrics["Max DD"] == 0
    assert metrics["Return"] == 2.01


def test_open_trade_drawdown_is_marked_to_market(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=3, freq="h")
    data = pd.DataFrame(
        {
            "open": [100, 100, 99.5],
            "high": [100, 100, 101.5],
            "low": [100, 99.5, 99.5],
            "close": [100, 99.5, 101],
            "volume": [0] * 3,
        },
        index=idx,
        dtype=float,
    )
    signals = pd.DataFrame([{"time": idx[0], "side": "long", "entry": 100.0, "stop": 99.0}])
    metrics, trades = run_backtest(
        data, signals, strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed",
        operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg,
    )

    assert (trades[0]["exit_i"], trades[0]["exit_reason"]) == (2, ExitReason.TARGET)
    assert metrics["Max DD"] == 0.5
    assert metrics["Return"] == 1


def mnq_like_cfg(test_asset_cfg):
    return replace(test_asset_cfg, point_value=2.0, tick_size=0.25, min_risk_ticks=4, bars_per_year={"1m": 347760, "1d": 252})


def min_risk_run(data, signals, cfg):
    return run_backtest(
        data, signals, strategy="test", asset="MNQ", timeframe="1m", risk_reward_ratio=2, exit_mode="fixed",
        operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg,
    )


def min_risk_data():
    idx = pd.date_range("2025-01-01", periods=2, freq="min")
    return pd.DataFrame(
        {"open": [100.0, 100.0], "high": [100.0, 102.5], "low": [100.0, 100.0], "close": [100.0, 102.0], "volume": [1, 1]},
        index=idx,
    )


def test_one_tick_stop_on_flat_breakout_bar_through_halt_gap_is_discarded(test_asset_cfg):
    idx = pd.DatetimeIndex(["2020-03-16 13:30", "2020-03-16 13:45", "2020-03-16 13:46"])
    data = pd.DataFrame(
        {
            "open": [7381.25, 7299.0, 7300.0],
            "high": [7381.25, 7310.0, 7305.0],
            "low": [7381.25, 7295.0, 7298.0],
            "close": [7381.25, 7300.0, 7301.0],
            "volume": [1, 1, 1],
        },
        index=idx,
    )
    signals = pd.DataFrame([{"time": idx[0], "side": "short", "entry": 7381.25, "stop": 7381.50}])
    metrics, trades = min_risk_run(data, signals, mnq_like_cfg(test_asset_cfg))
    assert trades == []
    assert metrics["Discarded"] == 1


def test_signal_exactly_at_min_risk_is_traded(test_asset_cfg):
    data = min_risk_data()
    signals = pd.DataFrame([{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.0}])
    metrics, trades = min_risk_run(data, signals, mnq_like_cfg(test_asset_cfg))
    assert metrics["Discarded"] == 0
    assert (trades[0]["initial_risk"], trades[0]["qty"], trades[0]["exit_reason"]) == (1.0, 50.0, ExitReason.TARGET)


def test_min_risk_uses_tick_rounded_stop(test_asset_cfg):
    data = min_risk_data()
    cfg = mnq_like_cfg(test_asset_cfg)
    at_min, _ = min_risk_run(data, pd.DataFrame([{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.1}]), cfg)
    below, trades = min_risk_run(data, pd.DataFrame([{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.3}]), cfg)
    assert at_min["Discarded"] == 0
    assert below["Discarded"] == 1
    assert trades == []


def _timed_bars(bars):
    idx = pd.DatetimeIndex([pd.Timestamp(time) for time in bars])
    return pd.DataFrame(list(bars.values()), index=idx, columns=["open", "high", "low", "close"], dtype=float)


NY_MORNING = parse_sessions("ny_bank=08:00-11:00")[0]
FLAT = (100.0, 100.0, 100.0, 100.0)


@pytest.mark.parametrize(
    ("side", "stop", "last_bar", "after_window"),
    [
        ("long", 99.0, (100.0, 100.8, 99.8, 100.5), (100.5, 102.5, 100.5, 102.0)),
        ("short", 101.0, (100.0, 100.2, 99.2, 99.5), (99.5, 99.5, 97.5, 98.0)),
    ],
)
def test_session_exit_closes_open_trade_at_last_in_window_bar_close(test_asset_cfg, side, stop, last_bar, after_window):
    data = _timed_bars({"2025-01-02 13:00": FLAT, "2025-01-02 14:00": FLAT, "2025-01-02 15:00": last_bar, "2025-01-02 16:00": after_window})
    signals = [{"time": data.index[0], "side": side, "stop": stop}]

    _, held = _run(data, signals, test_asset_cfg, risk_reward_ratio=2)
    _, exited = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, session_exit=NY_MORNING)

    assert held[0]["exit_reason"] == ExitReason.TARGET
    trade = exited[0]
    assert trade["exit_reason"] == ExitReason.SESSION
    assert (trade["exit_time"], trade["exit"], trade["realized_r"]) == (pd.Timestamp("2025-01-02 15:00"), last_bar[3], 0.5)
    assert trade["exits"][0]["gap_exit"] is False


@pytest.mark.parametrize(
    ("exit_mode", "exits"),
    [
        ("partial", [(ExitReason.TARGET, 50.0, 101.0), (ExitReason.SESSION, 50.0, 100.8)]),
        ("trailing", [(ExitReason.SESSION, 100.0, 100.8)]),
    ],
)
def test_session_exit_closes_remaining_position_for_managed_exit_modes(test_asset_cfg, exit_mode, exits):
    data = _timed_bars(
        {
            "2025-01-02 13:00": FLAT,
            "2025-01-02 14:00": (100.0, 101.2, 100.0, 101.0),
            "2025-01-02 15:00": (101.0, 101.0, 100.8, 100.8),
            "2025-01-02 16:00": (100.8, 103.0, 100.8, 103.0),
        }
    )
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, trades = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode=exit_mode, session_exit=NY_MORNING)

    assert [(fill["exit_reason"], fill["qty"], fill["exit"]) for fill in trades[0]["exits"]] == exits
    assert trades[0]["exit_reason"] == ExitReason.SESSION


def test_session_exit_uses_last_bar_present_when_window_end_bars_are_missing(test_asset_cfg):
    data = _timed_bars(
        {
            "2025-01-02 13:00": FLAT,
            "2025-01-02 14:00": (100.0, 100.4, 100.0, 100.3),
            "2025-01-03 13:00": (100.3, 103.0, 100.3, 103.0),
        }
    )
    _, trades = _run(data, [{"time": data.index[0], "side": "long", "stop": 99.0}], test_asset_cfg, session_exit=NY_MORNING)

    assert (trades[0]["exit_reason"], trades[0]["exit_time"], trades[0]["exit"]) == (
        ExitReason.SESSION, pd.Timestamp("2025-01-02 14:00"), 100.3,
    )


def test_session_exit_window_end_follows_dst(test_asset_cfg):
    data = _timed_bars(
        {
            "2025-07-02 12:00": FLAT,
            "2025-07-02 13:00": FLAT,
            "2025-07-02 14:00": (100.0, 100.6, 100.0, 100.6),
            "2025-07-02 15:00": (100.6, 100.6, 100.4, 100.4),
        }
    )
    _, trades = _run(data, [{"time": data.index[0], "side": "long", "stop": 99.0}], test_asset_cfg, session_exit=NY_MORNING)

    assert (trades[0]["exit_reason"], trades[0]["exit_time"], trades[0]["exit"]) == (
        ExitReason.SESSION, pd.Timestamp("2025-07-02 14:00"), 100.6,
    )


def test_session_exit_applies_costs_like_any_exit(cost_asset_cfg):
    data = _timed_bars(
        {"2025-01-02 13:00": FLAT, "2025-01-02 14:00": FLAT, "2025-01-02 15:00": (100.0, 100.5, 100.0, 100.5), "2025-01-02 16:00": FLAT}
    )
    _, trades = _run(
        data, [{"time": data.index[0], "side": "long", "stop": 99.0}], cost_asset_cfg, with_costs=True, session_exit=NY_MORNING,
    )

    trade = trades[0]
    assert trade["exit_reason"] == ExitReason.SESSION
    assert trade["net_exit"] == pytest.approx(100.3)
    assert trade["gross_pnl"] == pytest.approx(50.0)
    assert trade["net_pnl"] == pytest.approx(-10.0)


@pytest.mark.parametrize(
    ("signal", "reason"),
    [
        ({"time": pd.Timestamp("2025-01-02 15:00"), "side": "long", "stop": 99.0}, "entry outside window"),
        ({"time": pd.Timestamp("2025-01-02 15:00"), "side": "long", "stop": 99.0, "entry": 100.0}, "entry at window close"),
    ],
)
def test_session_exit_discards_trades_that_cannot_open_before_window_close(test_asset_cfg, signal, reason):
    data = _timed_bars({"2025-01-02 14:00": FLAT, "2025-01-02 15:00": FLAT, "2025-01-02 16:00": FLAT, "2025-01-02 17:00": FLAT})

    held, _ = _run(data, [signal], test_asset_cfg)
    metrics, trades = _run(data, [signal], test_asset_cfg, session_exit=NY_MORNING)

    assert held["Discarded"] == 0, reason
    assert (metrics["Discarded"], trades) == (1, []), reason


def test_session_exit_closes_trade_at_data_end_inside_window(test_asset_cfg):
    data = _timed_bars({"2025-01-02 13:00": FLAT, "2025-01-02 14:00": (100.0, 100.5, 100.0, 100.4)})
    _, trades = _run(data, [{"time": data.index[0], "side": "long", "stop": 99.0}], test_asset_cfg, session_exit=NY_MORNING)

    assert [(t["exit_reason"], t["exit_time"], t["exit"]) for t in trades] == [
        (ExitReason.END, pd.Timestamp("2025-01-02 14:00"), 100.4),
    ]


def _split_run(cfg, **overrides):
    data = _bars(
        [100.0, 100.0, 100.0, 100.0],
        [100.0, 102.0, 100.0, 101.0],
        [100.0, 100.0, 100.0, 100.0],
        [100.0, 100.0, 99.5, 99.5],
        [99.5, 101.5, 99.5, 101.0],
        [100.0, 100.0, 100.0, 100.0],
        [100.0, 100.0, 98.0, 98.0],
        [100.0, 100.0, 100.0, 100.0],
    )
    signals = [{"time": data.index[i], "side": "long", "stop": 99.0} for i in (0, 2, 5)]
    return _run(data, signals, cfg, **overrides)


def test_split_metrics_are_the_curve_metrics_of_each_slice_gross_and_net(cost_asset_cfg):
    metrics, trades = _split_run(cost_asset_cfg, with_costs=True, oos_cutoff=4)

    gross = [10000, 10000, 10100, 10100, 10050, 10200, 10200, 10100, 10100]
    net = [10000, 10000, 10040, 10040, 9930, 10080, 10080, 9920, 9920]
    assert len(trades) == 3
    def curve_metrics(curve):
        return {**_curve_metrics(np.array(curve, dtype=float), np.array([], dtype=int), 252), "Sharpe Ratio": None}

    for name in ("Gross", "Net", "OOS Gross", "OOS Net"):
        assert math.isnan(metrics[name]["Sharpe Ratio"])
    assert {**metrics["Gross"], "Sharpe Ratio": None} == curve_metrics(gross[:5])
    assert {**metrics["Net"], "Sharpe Ratio": None} == curve_metrics(net[:5])
    assert {**metrics["OOS Gross"], "Sharpe Ratio": None} == curve_metrics(gross[4:])
    assert {**metrics["OOS Net"], "Sharpe Ratio": None} == curve_metrics(net[4:])
    assert metrics["Return"] == metrics["Net"]["Return"] == -0.7
    assert metrics["OOS Return"] == metrics["OOS Net"]["Return"]


def test_split_metrics_carry_walk_forward_efficiency_gross_and_net(cost_asset_cfg):
    metrics, _ = _split_run(cost_asset_cfg, with_costs=True, oos_cutoff=4)

    gross = np.array([10000, 10000, 10100, 10100, 10050, 10200, 10200, 10100, 10100], dtype=float)
    gross_wfe, net_wfe = metrics["WFE"]
    assert gross_wfe == walk_forward_efficiency(gross, 4, 1716) > 0
    assert math.isnan(net_wfe)
    without_costs, _ = _split_run(cost_asset_cfg, oos_cutoff=4)
    assert isinstance(without_costs["WFE"], float) and without_costs["WFE"] > 0


def test_out_of_sample_curve_starts_from_equity_at_last_in_sample_close(test_asset_cfg):
    metrics, _ = _split_run(test_asset_cfg, oos_cutoff=4)

    assert metrics["OOS Return"] == round((10099 / 10049.5 - 1) * 100, 2) == 0.49
    assert metrics["OOS Max DD"] == round((1 - 10099 / 10201) * 100, 2)


def test_split_assigns_trades_by_entry_bar_and_counts_spanning_trades(test_asset_cfg):
    metrics, trades = _split_run(test_asset_cfg, oos_cutoff=4)

    assert [(trade["entry_i"], trade["exit_i"]) for trade in trades] == [(1, 1), (3, 4), (6, 6)]
    assert metrics["OOS Trades"] == 1
    assert metrics["Spanning"] == 1


def test_without_cutoff_metrics_have_no_split_values(test_asset_cfg):
    whole, trades = _split_run(test_asset_cfg)

    assert not any(key.startswith("OOS") or key == "Spanning" for key in whole)
    assert whole["Return"] == round((10099 / 10000 - 1) * 100, 2)
    assert len(trades) == 3


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"oos_cutoff": 0}, "in-sample period with no bars"),
        ({"oos_cutoff": 8}, "out-of-sample period with no bars"),
        ({"oos_cutoff": 4, "max_trades": 2}, "--max_trades cannot be combined with a split"),
    ],
)
def test_backtest_rejects_invalid_cutoffs(test_asset_cfg, overrides, message):
    with pytest.raises(ValueError, match=message):
        _split_run(test_asset_cfg, **overrides)


def _contract_bars(bars):
    idx = pd.DatetimeIndex([pd.Timestamp(time) for time in bars])
    data = pd.DataFrame(list(bars.values()), index=idx, columns=["open", "high", "low", "close", "contract"])
    return data.astype({column: float for column in ("open", "high", "low", "close")})


ROLL_BARS = {
    "2025-01-02 17:00": (100.0, 100.0, 100.0, 100.0, "FUTZ4"),
    "2025-01-02 18:00": (100.0, 100.5, 100.0, 100.5, "FUTZ4"),
    "2025-01-02 19:00": (100.4, 100.8, 100.2, 100.5, "FUTZ4"),
    "2025-01-02 22:00": (150.0, 150.0, 150.0, 150.0, "FUTH5"),
}


def test_roll_exit_closes_open_trade_at_last_bar_before_roll(cost_asset_cfg):
    data = _contract_bars(ROLL_BARS)
    _, trades = _run(data, [{"time": data.index[0], "side": "long", "stop": 99.0}], cost_asset_cfg, with_costs=True)

    (trade,) = trades
    assert trade["exit_reason"] == ExitReason.ROLL
    assert trade["exit_time"] == pd.Timestamp("2025-01-02 19:00")
    assert trade["exit"] == 100.5
    assert trade["gross_pnl"] == pytest.approx(50.0)
    assert trade["net_pnl"] == pytest.approx(-10.0)


def test_data_without_contract_column_has_no_roll_exit(test_asset_cfg):
    data = _contract_bars(ROLL_BARS).drop(columns="contract")
    _, trades = _run(data, [{"time": data.index[0], "side": "long", "stop": 99.0}], test_asset_cfg)

    (trade,) = trades
    assert (trade["exit_reason"], trade["exit"]) == (ExitReason.TARGET, 101.0)


@pytest.mark.parametrize(("fill", "discarded"), [("20:00", 1), ("19:59", 0)])
def test_signal_filling_in_last_60_minutes_before_roll_is_discarded(test_asset_cfg, fill, discarded):
    bars = {str(time): (100.0, 100.0, 100.0, 100.0, "FUTZ4") for time in pd.date_range("2025-01-02 19:57", "2025-01-02 20:59", freq="1min")}
    bars["2025-01-02 22:00"] = (150.0, 150.0, 150.0, 150.0, "FUTH5")
    data = _contract_bars(bars)
    signal_time = pd.Timestamp(f"2025-01-02 {fill}") - pd.Timedelta(minutes=1)

    metrics, trades = _run(data, [{"time": signal_time, "side": "long", "stop": 99.0}], test_asset_cfg)

    assert metrics["Discarded"] == discarded
    assert [trade["exit_reason"] for trade in trades] == [ExitReason.ROLL] * (1 - discarded)


@pytest.mark.parametrize(("signal_bar", "discarded"), [("18:00", 0), ("19:00", 1)])
def test_close_entry_roll_block_measures_from_the_fill_at_the_bar_close(test_asset_cfg, signal_bar, discarded):
    bars = {f"2025-01-02 {hour}:00": (100.0, 100.0, 100.0, 100.0, "FUTZ4") for hour in (17, 18, 19, 20)}
    bars["2025-01-02 22:00"] = (150.0, 150.0, 150.0, 150.0, "FUTH5")
    data = _contract_bars(bars)
    signal = {"time": pd.Timestamp(f"2025-01-02 {signal_bar}"), "side": "long", "entry": 100.0, "stop": 99.0}

    metrics, trades = _run(data, [signal], test_asset_cfg)

    assert metrics["Discarded"] == discarded
    assert [trade["exit_reason"] for trade in trades] == [ExitReason.ROLL] * (1 - discarded)


@pytest.mark.parametrize(("signal_time", "stop", "discarded"), [("12:00", 99.0, 1), ("22:00", 149.0, 0)])
def test_signal_before_roll_never_fills_on_or_after_roll_bar(test_asset_cfg, signal_time, stop, discarded):
    data = _contract_bars(
        {
            "2025-01-02 06:00": (100.0, 100.0, 100.0, 100.0, "FUTZ4"),
            "2025-01-02 12:00": (100.0, 100.0, 100.0, 100.0, "FUTZ4"),
            "2025-01-02 22:00": (150.0, 150.0, 150.0, 150.0, "FUTH5"),
            "2025-01-02 23:00": (150.0, 151.1, 150.0, 151.0, "FUTH5"),
        }
    )
    signal = {"time": pd.Timestamp(f"2025-01-02 {signal_time}"), "side": "long", "stop": stop}

    metrics, trades = _run(data, [signal], test_asset_cfg)

    assert metrics["Discarded"] == discarded
    assert [trade["exit_reason"] for trade in trades] == [ExitReason.TARGET] * (1 - discarded)


def test_trade_open_at_data_end_after_a_roll_exits_at_last_close(test_asset_cfg):
    data = _contract_bars(
        {
            **ROLL_BARS,
            "2025-01-02 23:00": (150.0, 150.5, 150.0, 150.5, "FUTH5"),
            "2025-01-03 00:00": (150.5, 150.8, 150.2, 150.6, "FUTH5"),
        }
    )
    signal = {"time": pd.Timestamp("2025-01-02 22:00"), "side": "long", "stop": 149.0}

    _, trades = _run(data, [signal], test_asset_cfg)

    assert [(t["exit_reason"], t["exit_time"], t["exit"]) for t in trades] == [
        (ExitReason.END, pd.Timestamp("2025-01-03 00:00"), 150.6),
    ]


@pytest.mark.parametrize(
    ("side", "stop", "touch_bar"),
    [
        ("long", 99.0, [100.0, 101.0, 99.5, 100.5]),
        ("short", 101.0, [100.0, 100.5, 99.0, 99.5]),
    ],
)
def test_bar_extreme_equal_to_target_fills_on_touch_but_not_tick_through(test_asset_cfg, side, stop, touch_bar):
    data = _bars([100.0, 100.0, 100.0, 100.0], touch_bar)
    signals = [{"time": data.index[0], "side": side, "stop": stop}]

    _, touched = _run(data, signals, test_asset_cfg, target_fill="touch")
    _, untouched = _run(data, signals, test_asset_cfg, target_fill="tick_through")

    assert (touched[0]["exit_reason"], touched[0]["exit"]) == (ExitReason.TARGET, touch_bar[1] if side == "long" else touch_bar[2])
    assert [t["exit_reason"] for t in untouched] == [ExitReason.END]


@pytest.mark.parametrize(
    ("side", "stop", "through_bar", "target"),
    [
        ("long", 99.0, [100.0, 101.1, 99.5, 100.5], 101.0),
        ("short", 101.0, [100.0, 100.5, 98.9, 99.5], 99.0),
    ],
)
def test_tick_through_target_fills_at_the_target_price(test_asset_cfg, side, stop, through_bar, target):
    data = _bars([100.0, 100.0, 100.0, 100.0], through_bar)

    _, trades = _run(data, [{"time": data.index[0], "side": side, "stop": stop}], test_asset_cfg)

    assert (trades[0]["exit_reason"], trades[0]["exit"]) == (ExitReason.TARGET, target)


@pytest.mark.parametrize(
    ("side", "stop", "both_bar"),
    [
        ("long", 99.0, [100.8, 101.5, 98.5, 100.0]),
        ("long", 99.0, [99.2, 101.5, 98.5, 100.0]),
        ("short", 101.0, [99.2, 101.5, 98.5, 100.0]),
        ("short", 101.0, [100.8, 101.5, 98.5, 100.0]),
    ],
)
def test_stop_first_exits_at_the_stop_when_stop_and_target_share_a_bar(test_asset_cfg, side, stop, both_bar):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0], both_bar)
    signals = [{"time": data.index[0], "side": side, "stop": stop}]

    _, trades = _run(data, signals, test_asset_cfg, intrabar="stop_first")

    assert [(f["exit_reason"], f["raw_exit"]) for f in trades[0]["exits"]] == [(ExitReason.STOP, stop)]


@pytest.mark.parametrize(
    ("exit_mode", "bar", "path_exits", "stop_first_exits"),
    [
        ("partial", [100.8, 101.5, 98.5, 100.0], [(ExitReason.TARGET, 101.0), (ExitReason.STOP, 99.0)], [(ExitReason.STOP, 99.0)]),
        ("trailing", [101.9, 102.5, 100.8, 101.0], [(ExitReason.TARGET, 102.0)], [(ExitReason.STOP, 101.4)]),
    ],
)
def test_managed_exit_modes_follow_the_intrabar_rule(test_asset_cfg, exit_mode, bar, path_exits, stop_first_exits):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0], bar)
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, path = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode=exit_mode)
    _, stop_first = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode=exit_mode, intrabar="stop_first")

    assert [(f["exit_reason"], f["raw_exit"]) for f in path[0]["exits"]] == path_exits
    assert [(f["exit_reason"], f["raw_exit"]) for f in stop_first[0]["exits"]] == stop_first_exits


def test_trailing_target_touched_exactly_fills_only_on_touch(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 102.0, 100.0, 101.8])
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, touched = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode="trailing", target_fill="touch")
    _, untouched = _run(data, signals, test_asset_cfg, risk_reward_ratio=2, exit_mode="trailing")

    assert (touched[0]["exit_reason"], touched[0]["exit"]) == (ExitReason.TARGET, 102.0)
    assert [t["exit_reason"] for t in untouched] == [ExitReason.END]


def test_stop_first_leaves_a_bar_that_cannot_reach_the_target_to_the_path(test_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0], [100.2, 101.6, 98.5, 99.0])
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]

    _, trades = _run(data, signals, test_asset_cfg, risk_reward_ratio=3, exit_mode="trailing", intrabar="stop_first")

    assert [(f["exit_reason"], f["raw_exit"]) for f in trades[0]["exits"]] == [(ExitReason.STOP, 101.1)]


@pytest.mark.parametrize(
    ("side", "stop", "flat", "through_bar", "target"),
    [
        ("long", 0.9, 1.0, [1.0, 1.2, 1.0, 1.1], 1.1),
        ("short", 0.5, 0.4, [0.4, 0.4, 0.2, 0.3], 0.3),
    ],
)
def test_tick_through_trigger_is_exactly_one_tick_beyond_the_target(test_asset_cfg, side, stop, flat, through_bar, target):
    data = _bars([flat] * 4, [flat] * 4, through_bar)

    _, trades = _run(data, [{"time": data.index[0], "side": side, "stop": stop}], test_asset_cfg)

    assert (trades[0]["target"], trades[0]["exit_reason"], trades[0]["exit_i"]) == (target, ExitReason.TARGET, 2)


def test_backtest_rejects_an_low_timeframe_without_a_bar_length(base_data, test_asset_cfg):
    cfg = replace(test_asset_cfg, bars_per_year={**test_asset_cfg.bars_per_year, "4h": 429})
    with pytest.raises(ValueError, match="Unknown low timeframe for TEST: 4h"):
        _run(base_data, [{"time": base_data.index[0], "side": "long", "stop": 99.0}], cfg, timeframe="4h")


def test_backtest_rejects_unknown_fill_rules(base_data, test_asset_cfg):
    signals = [{"time": base_data.index[0], "side": "long", "stop": 99.0}]
    with pytest.raises(ValueError, match="--target_fill"):
        _run(base_data, signals, test_asset_cfg, target_fill="near")
    with pytest.raises(ValueError, match="--intrabar"):
        _run(base_data, signals, test_asset_cfg, intrabar="low_first")


DAILY_SHARPE_BARS = {
    "2025-01-01 22:00": FLAT,
    "2025-01-01 23:00": FLAT,
    "2025-01-02 00:00": (100.0, 101.5, 100.0, 100.0),
    "2025-01-02 01:00": FLAT,
    "2025-01-03 00:00": (100.0, 102.5, 100.0, 100.0),
    "2025-01-03 01:00": FLAT,
}


def _daily_sharpe_run(cfg, data=None, **overrides):
    data = _timed_bars(DAILY_SHARPE_BARS) if data is None else data
    signals = [
        {"time": pd.Timestamp("2025-01-01 23:55"), "side": "long", "stop": 99.0},
        {"time": pd.Timestamp("2025-01-02 01:55"), "side": "long", "stop": 98.0},
    ]
    return _run(data, signals, cfg, **overrides)


def test_sharpe_is_daily_over_market_sessions_counting_sessions_without_trades(test_asset_cfg):
    metrics, trades = _daily_sharpe_run(test_asset_cfg)

    assert [trade["gross_pnl"] for trade in trades] == [100.0, 100.0]
    assert metrics["Sharpe Ratio"] == 18.33


def test_daily_sharpe_uses_the_net_curve_for_the_net_column(cost_asset_cfg):
    metrics, trades = _daily_sharpe_run(cost_asset_cfg, with_costs=True)

    first, second = (trade["net_pnl"] for trade in trades)
    assert (first, second) == pytest.approx((40.0, 70.0))
    assert metrics["Net"]["Sharpe Ratio"] == 16.59
    assert metrics["Gross"]["Sharpe Ratio"] != metrics["Net"]["Sharpe Ratio"]


def test_same_trades_on_1h_and_5m_data_give_the_same_sharpe(cost_asset_cfg):
    hourly = _timed_bars(DAILY_SHARPE_BARS)
    five_minute = pd.DataFrame(
        [row if step == 0 else [row[3]] * 4 for row in hourly.to_numpy() for step in range(12)],
        index=pd.DatetimeIndex([time + pd.Timedelta(minutes=5 * step) for time in hourly.index for step in range(12)]),
        columns=hourly.columns,
    )
    cfg = replace(cost_asset_cfg, bars_per_year={"1h": 1716, "5m": 20592, "1d": 252})

    hourly_metrics, hourly_trades = _daily_sharpe_run(cfg, hourly, with_costs=True)
    five_minute_metrics, five_minute_trades = _daily_sharpe_run(cfg, five_minute, with_costs=True, low_timeframe="5m")

    assert [t["net_pnl"] for t in five_minute_trades] == [t["net_pnl"] for t in hourly_trades]
    assert five_minute_metrics["Gross"]["Sharpe Ratio"] == hourly_metrics["Gross"]["Sharpe Ratio"] == 18.33
    assert five_minute_metrics["Net"]["Sharpe Ratio"] == hourly_metrics["Net"]["Sharpe Ratio"]


def test_split_daily_sharpe_uses_each_period_s_own_market_sessions(test_asset_cfg):
    later_days = {f"2025-01-0{day} {hour}": FLAT for day in (4, 5) for hour in ("00:00", "01:00")}
    data = _timed_bars(DAILY_SHARPE_BARS | later_days)

    metrics, _ = _daily_sharpe_run(test_asset_cfg, data, oos_cutoff=4)

    assert metrics["Sharpe Ratio"] == 11.22
    assert metrics["OOS Sharpe"] == 9.17


def _quarter_hour_bars(*bars):
    idx = pd.date_range("2025-01-01", periods=len(bars), freq="15min")
    return pd.DataFrame(bars, index=idx, columns=["open", "high", "low", "close"], dtype=float)


def _quarter_hour_cfg(cfg):
    return replace(cfg, bars_per_year=cfg.bars_per_year | {"15m": 1716 * 4})


@pytest.mark.parametrize(
    ("setup_time", "accepted"),
    [("2025-01-01 00:45", False), ("2025-01-01 01:00", True), (None, True)],
)
def test_setup_before_first_high_timeframe_bar_after_exit_is_discarded(test_asset_cfg, setup_time, accepted):
    flat = [100.0, 100.0, 100.0, 100.0]
    data = _quarter_hour_bars(flat, [100.0, 101.5, 100.0, 101.0], flat, flat, flat, flat, flat)
    signals = [
        {"time": data.index[0], "side": "long", "stop": 99.0, "setup_time": data.index[0]},
        {"time": data.index[4], "side": "long", "stop": 99.0, "setup_time": setup_time and pd.Timestamp(setup_time)},
    ]
    metrics, trades = _run(data, signals, _quarter_hour_cfg(test_asset_cfg), low_timeframe="15m")
    assert [t["entry_i"] for t in trades] == ([1, 5] if accepted else [1])
    assert metrics["Discarded"] == (0 if accepted else 1)


def test_setup_time_rule_uses_each_risk_reward_ratio_s_own_exit(test_asset_cfg):
    data = _quarter_hour_bars(
        [100.0, 100.0, 100.0, 100.0], [100.0, 101.5, 100.0, 101.0], [101.0, 101.0, 100.0, 101.0],
        [101.0, 101.0, 100.0, 101.0], [101.0, 103.5, 101.0, 103.0], [103.0, 103.0, 103.0, 103.0],
        [103.0, 103.0, 103.0, 103.0], [103.0, 103.0, 103.0, 103.0],
    )
    signals = [
        {"time": data.index[0], "side": "long", "stop": 99.0},
        {"time": data.index[5], "side": "long", "stop": 99.0, "setup_time": data.index[4]},
    ]
    cfg = _quarter_hour_cfg(test_asset_cfg)
    _, rr1 = _run(data, signals, cfg, low_timeframe="15m", risk_reward_ratio=1)
    _, rr3 = _run(data, signals, cfg, low_timeframe="15m", risk_reward_ratio=3)
    assert [(t["entry_i"], t["exit_i"]) for t in rr1] == [(1, 1), (6, 7)]
    assert [(t["entry_i"], t["exit_i"]) for t in rr3] == [(1, 4)]


@pytest.mark.parametrize(
    ("target_bar", "setup_time", "accepted"),
    [
        (1, "2025-01-01 22:00", False),
        (1, "2025-01-01 23:00", True),
        (23, "2025-01-02 01:00", False),
        (23, "2025-01-02 23:00", True),
    ],
)
def test_daily_setup_time_rule_uses_the_market_session_start(test_asset_cfg, target_bar, setup_time, accepted):
    flat = [100.0, 100.0, 100.0, 100.0]
    data = _bars(*[[100.0, 101.5, 100.0, 101.0] if i == target_bar else flat for i in range(50)])
    cfg = replace(test_asset_cfg, session_timezone="America/New_York", session_start="18:00")
    signals = [
        {"time": data.index[target_bar - 1], "side": "long", "stop": 99.0},
        {"time": data.index[47], "side": "long", "stop": 99.0, "setup_time": pd.Timestamp(setup_time)},
    ]

    _, trades = _run(data, signals, cfg, timeframe="1d", low_timeframe="1h")

    assert [t["entry_i"] for t in trades] == ([target_bar, 48] if accepted else [target_bar])


def test_setup_time_after_signal_time_is_an_error():
    signals = pd.DataFrame(
        [{"time": pd.Timestamp("2025-01-01 01:00"), "side": "long", "stop": 99, "setup_time": pd.Timestamp("2025-01-01 01:15")}]
    )
    with pytest.raises(ValueError, match="setup_time"):
        normalize_signals(signals)


@pytest.mark.parametrize(
    ("path", "side", "level", "remainder"),
    [
        pytest.param((100.0, 101.0, 99.0, 100.5), Side.LONG, 100.5, (100.5, 101.0, 99.0, 100.5), id="high-first-long"),
        pytest.param((100.0, 101.0, 99.0, 100.5), Side.SHORT, 99.5, (99.5, 99.0, 100.5), id="high-first-short"),
        pytest.param((100.0, 99.0, 101.0, 100.5), Side.LONG, 100.5, (100.5, 101.0, 100.5), id="low-first-long"),
        pytest.param((100.0, 99.0, 101.0, 100.5), Side.SHORT, 99.5, (99.5, 99.0, 101.0, 100.5), id="low-first-short"),
        pytest.param((100.0, 101.0, 99.0, 100.5), Side.LONG, 101.0, (101.0, 101.0, 99.0, 100.5), id="long-touching-the-high"),
        pytest.param((101.0, 101.2, 100.9, 101.0), Side.LONG, 100.5, (101.0, 101.2, 100.9, 101.0), id="long-gap"),
        pytest.param((99.0, 99.1, 98.8, 99.0), Side.SHORT, 99.5, (99.0, 99.1, 98.8, 99.0), id="short-gap"),
        pytest.param((100.0, 100.4, 99.0, 100.0), Side.LONG, 100.5, None, id="long-never-reached"),
        pytest.param((100.0, 101.0, 99.6, 100.0), Side.SHORT, 99.5, None, id="short-never-reached"),
    ],
)
def test_path_after_stop_entry_starts_at_the_first_point_reaching_the_level(path, side, level, remainder):
    assert backtest._path_after_stop_entry(path, level, side) == remainder


def _stop_entry(data, side, level, stop, bar=0):
    return {"time": data.index[bar], "side": side, "stop_entry": level, "stop": stop}


@pytest.mark.parametrize(
    ("side", "fill_bar", "level", "stop", "fill"),
    [
        pytest.param("long", [100.0, 101.0, 99.8, 100.8], 100.5, 99.5, 100.5, id="long-mid-bar"),
        pytest.param("short", [100.0, 100.2, 99.0, 99.2], 99.5, 100.5, 99.5, id="short-mid-bar"),
        pytest.param("long", [101.0, 101.2, 100.9, 101.0], 100.5, 99.5, 101.0, id="long-gap-fills-at-the-open"),
        pytest.param("short", [99.0, 99.1, 98.8, 99.0], 99.5, 100.5, 99.0, id="short-gap-fills-at-the-open"),
    ],
)
def test_stop_entry_fills_on_its_own_bar_at_the_level_or_a_gapped_open(test_asset_cfg, side, fill_bar, level, stop, fill):
    data = _bars(fill_bar, [fill_bar[3]] * 4)

    metrics, trades = _run(data, [_stop_entry(data, side, level, stop)], test_asset_cfg)

    (trade,) = trades
    assert (trade["entry_i"], trade["entry_time"], trade["entry"], trade["exit_reason"]) == (0, data.index[0], fill, ExitReason.END)
    assert trade["initial_risk"] == pytest.approx(abs(fill - stop))
    assert metrics["Discarded"] == 0


@pytest.mark.parametrize(
    ("side", "fill_bar", "level", "stop"),
    [
        pytest.param("long", [100.0, 100.4, 99.0, 100.0], 100.5, 99.5, id="long-high-below-the-level"),
        pytest.param("short", [100.0, 101.0, 99.6, 100.0], 99.5, 100.5, id="short-low-above-the-level"),
        pytest.param("long", [100.0, 101.0, 99.8, 100.8], 100.5, 100.5, id="long-stop-at-the-level"),
        pytest.param("short", [100.0, 100.2, 99.0, 99.2], 99.5, 99.5, id="short-stop-at-the-level"),
        pytest.param("long", [102.0, 102.5, 101.5, 102.0], 100.5, 100.7, id="long-stop-above-the-level-on-a-gap"),
        pytest.param("short", [98.0, 98.5, 97.5, 98.0], 99.5, 99.3, id="short-stop-below-the-level-on-a-gap"),
    ],
)
def test_stop_entry_that_never_reaches_its_level_or_has_a_wrong_side_stop_is_discarded(test_asset_cfg, side, fill_bar, level, stop):
    data = _bars(fill_bar, [fill_bar[3]] * 4)

    metrics, trades = _run(data, [_stop_entry(data, side, level, stop)], test_asset_cfg)

    assert (metrics["Discarded"], trades) == (1, [])


@pytest.mark.parametrize(
    ("side", "fill_bar", "level", "stop"),
    [
        pytest.param("long", [100.0, 100.8, 99.0, 99.5], 100.5, 99.8, id="long-high-first"),
        pytest.param("long", [100.0, 101.0, 99.7, 99.7], 100.5, 99.9, id="long-low-first"),
        pytest.param("short", [100.0, 100.2, 99.0, 100.2], 99.5, 100.2, id="short-high-first"),
        pytest.param("short", [100.0, 101.0, 99.8, 100.5], 99.9, 100.5, id="short-low-first"),
    ],
)
def test_stop_entry_stops_out_on_its_fill_bar_after_the_fill(test_asset_cfg, side, fill_bar, level, stop):
    data = _bars(fill_bar, [fill_bar[3]] * 4)

    _, trades = _run(data, [_stop_entry(data, side, level, stop)], test_asset_cfg)

    assert [(t["exit_i"], t["exit_reason"], t["exit"]) for t in trades] == [(0, ExitReason.STOP, stop)]


@pytest.mark.parametrize("intrabar", ["path", "stop_first"])
@pytest.mark.parametrize(
    ("side", "fill_bar", "level", "stop", "target"),
    [
        pytest.param("long", [100.0, 101.5, 99.7, 100.8], 100.5, 99.9, 101.1, id="long-low-before-the-fill"),
        pytest.param("short", [100.0, 100.3, 98.5, 99.2], 99.5, 100.1, 98.9, id="short-high-before-the-fill"),
    ],
)
def test_stop_entry_ignores_prices_before_its_fill_and_takes_a_same_bar_target(test_asset_cfg, intrabar, side, fill_bar, level, stop, target):
    data = _bars(fill_bar, [fill_bar[3]] * 4)

    _, trades = _run(data, [_stop_entry(data, side, level, stop)], test_asset_cfg, intrabar=intrabar)

    assert [(t["exit_i"], t["exit_reason"], t["exit"]) for t in trades] == [(0, ExitReason.TARGET, target)]


@pytest.mark.parametrize(
    ("side", "fill_bar", "level", "stop", "target"),
    [
        pytest.param("long", [100.0, 100.8, 98.5, 99.0], 100.3, 100.0, 100.6, id="long"),
        pytest.param("short", [100.0, 101.5, 99.2, 101.0], 99.7, 100.0, 99.4, id="short"),
    ],
)
def test_stop_first_exits_a_stop_entry_at_the_stop_when_its_remainder_reaches_both(test_asset_cfg, side, fill_bar, level, stop, target):
    data = _bars(fill_bar, [fill_bar[3]] * 4)
    signals = [_stop_entry(data, side, level, stop)]

    _, path = _run(data, signals, test_asset_cfg)
    _, stop_first = _run(data, signals, test_asset_cfg, intrabar="stop_first")

    assert [(t["exit_reason"], t["exit"]) for t in path] == [(ExitReason.TARGET, target)]
    assert [(t["exit_reason"], t["exit"]) for t in stop_first] == [(ExitReason.STOP, stop)]


def test_trailing_stop_entry_trails_from_the_fill_on_its_fill_bar(test_asset_cfg):
    data = _bars([100.0, 102.0, 99.7, 101.0], [101.0] * 4)

    _, trades = _run(data, [_stop_entry(data, "long", 100.5, 99.5)], test_asset_cfg, risk_reward_ratio=3, exit_mode="trailing")

    assert [(t["exit_i"], t["exit_reason"], t["exit"]) for t in trades] == [(0, ExitReason.STOP, 101.5)]


def test_partial_stop_entry_takes_its_first_target_and_stop_on_its_fill_bar(test_asset_cfg):
    data = _bars([100.0, 101.7, 99.3, 99.4], [99.4] * 4)

    _, trades = _run(data, [_stop_entry(data, "long", 100.5, 99.5)], test_asset_cfg, risk_reward_ratio=2, exit_mode="partial")

    assert [(f["exit_i"], f["exit_reason"], f["raw_exit"], f["qty"]) for f in trades[0]["exits"]] == [
        (0, ExitReason.TARGET, 101.5, 50.0),
        (0, ExitReason.STOP, 99.5, 50.0),
    ]


def test_signals_may_mix_close_entries_and_stop_entries_across_rows():
    signals = pd.DataFrame(
        [
            {"time": pd.Timestamp("2025-01-01 00:00"), "side": "long", "stop": 99.0, "entry": 100.0},
            {"time": pd.Timestamp("2025-01-01 01:00"), "side": "long", "stop": 99.0, "stop_entry": 100.5},
        ]
    )

    assert len(normalize_signals(signals)) == 2


def test_signal_with_both_entry_and_stop_entry_is_an_error():
    signals = pd.DataFrame(
        [{"time": pd.Timestamp("2025-01-01"), "side": "long", "stop": 99.0, "entry": 100.0, "stop_entry": 100.5}]
    )
    with pytest.raises(ValueError, match="both entry and stop_entry"):
        normalize_signals(signals)


def test_stop_entry_with_costs_sizes_from_the_fill_and_pays_costs_on_both_sides(cost_asset_cfg):
    data = _bars([100.0, 101.0, 99.8, 100.8], [100.8] * 4)

    _, trades = _run(data, [_stop_entry(data, "long", 100.5, 99.5)], cost_asset_cfg, with_costs=True)

    (trade,) = trades
    assert (trade["qty"], trade["net_entry"]) == (100.0, pytest.approx(100.7))
    assert trade["net_pnl"] == pytest.approx((100.6 - 100.7) * 100 - 0.1 * 100 * 2)


def test_stop_entry_on_the_last_bar_fills_and_takes_an_end_exit(test_asset_cfg):
    data = _bars([100.0] * 4, [100.0, 101.0, 99.8, 100.8])

    _, trades = _run(data, [_stop_entry(data, "long", 100.5, 99.5, bar=1)], test_asset_cfg)

    assert [(t["entry_i"], t["entry"], t["exit_i"], t["exit_reason"], t["exit"]) for t in trades] == [(1, 100.5, 1, ExitReason.END, 100.8)]


def test_stop_entry_time_not_on_a_bar_is_discarded(test_asset_cfg):
    data = _bars([100.0, 101.0, 99.8, 100.8], [100.0, 101.0, 99.8, 100.8], [100.8] * 4)
    signal = {"time": data.index[0] + pd.Timedelta(minutes=30), "side": "long", "stop_entry": 100.5, "stop": 99.5}

    metrics, trades = _run(data, [signal], test_asset_cfg)

    assert (metrics["Discarded"], trades) == (1, [])


def test_stop_entry_on_the_exit_bar_of_the_previous_trade_is_discarded(test_asset_cfg):
    data = _bars([100.0, 101.0, 99.8, 100.8], [100.8, 102.0, 100.8, 101.5], [101.5] * 4)
    signals = [_stop_entry(data, "long", 100.5, 99.5), _stop_entry(data, "long", 101.0, 100.0, bar=1)]

    metrics, trades = _run(data, signals, test_asset_cfg)

    assert [(t["entry_i"], t["exit_i"]) for t in trades] == [(0, 1)]
    assert metrics["Discarded"] == 1


@pytest.mark.parametrize(("setup_time", "accepted"), [("2025-01-01 00:45", False), ("2025-01-01 01:00", True)])
def test_stop_entry_with_a_setup_before_the_first_high_timeframe_bar_after_exit_is_discarded(test_asset_cfg, setup_time, accepted):
    flat = [100.0, 100.0, 100.0, 100.0]
    data = _quarter_hour_bars(flat, [100.0, 101.5, 100.0, 101.0], flat, flat, [100.0, 101.0, 99.8, 100.8], flat)
    signals = [
        {"time": data.index[0], "side": "long", "stop": 99.0},
        {**_stop_entry(data, "long", 100.5, 99.5, bar=4), "setup_time": pd.Timestamp(setup_time)},
    ]

    metrics, trades = _run(data, signals, _quarter_hour_cfg(test_asset_cfg), low_timeframe="15m")

    assert [t["entry_i"] for t in trades] == ([1, 4] if accepted else [1])
    assert metrics["Discarded"] == (0 if accepted else 1)


@pytest.mark.parametrize(
    ("signal_time", "discarded"),
    [("2025-01-02 15:00", 0), ("2025-01-02 16:00", 1)],
)
def test_session_exit_takes_a_stop_entry_whose_bar_starts_inside_the_window(test_asset_cfg, signal_time, discarded):
    bar = (100.0, 101.0, 99.8, 100.8)
    data = _timed_bars({"2025-01-02 14:00": FLAT, "2025-01-02 15:00": bar, "2025-01-02 16:00": bar, "2025-01-02 17:00": FLAT})
    signal = {"time": pd.Timestamp(signal_time), "side": "long", "stop_entry": 100.5, "stop": 99.5}

    metrics, trades = _run(data, [signal], test_asset_cfg, session_exit=NY_MORNING)

    assert metrics["Discarded"] == discarded
    assert [(t["entry_i"], t["exit_i"], t["exit_reason"]) for t in trades] == [(1, 1, ExitReason.SESSION)] * (1 - discarded)


@pytest.mark.parametrize(("signal_bar", "discarded"), [("19:00", 0), ("20:00", 1)])
def test_stop_entry_roll_block_measures_from_its_bar_start(test_asset_cfg, signal_bar, discarded):
    bars = {f"2025-01-02 {hour}:00": (100.0, 100.6, 100.0, 100.0, "FUTZ4") for hour in (17, 18, 19, 20)}
    bars["2025-01-02 22:00"] = (150.0, 150.0, 150.0, 150.0, "FUTH5")
    data = _contract_bars(bars)
    signal = {"time": pd.Timestamp(f"2025-01-02 {signal_bar}"), "side": "long", "stop_entry": 100.5, "stop": 99.5}

    metrics, trades = _run(data, [signal], test_asset_cfg)

    assert metrics["Discarded"] == discarded
    assert [trade["exit_reason"] for trade in trades] == [ExitReason.ROLL] * (1 - discarded)


def test_stop_entry_on_the_roll_bar_is_discarded(test_asset_cfg):
    data = _contract_bars(ROLL_BARS)
    signal = {"time": pd.Timestamp("2025-01-02 22:00"), "side": "long", "stop_entry": 150.0, "stop": 149.0}

    metrics, trades = _run(data, [signal], test_asset_cfg)

    assert (metrics["Discarded"], trades) == (1, [])


@pytest.mark.parametrize(
    ("price", "direction", "expected"),
    [
        (4488.8 + 2 * (4488.8 - 4479.5), 1, 4507.4),
        (4488.8 - 2 * (4488.8 - 4479.5), -1, 4470.2),
        (4507.41, 1, 4507.5),
        (4507.49, -1, 4507.4),
    ],
)
def test_tick_price_ignores_float_noise_but_rounds_off_grid_prices(price, direction, expected):
    assert backtest.tick_price(price, 0.1, direction) == expected


def _close_entry(stop, entry=100.0):
    return {"time": pd.Timestamp("2025-01-01 00:00"), "side": "long", "entry": entry, "stop": stop}


def _stop_distance_run(signal, limits, cfg, data=None):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0]) if data is None else data
    metrics, trades = _run(data, [signal], cfg, stop_distance=StopDistanceLimits.parse(limits))
    return metrics["Discarded"], len(trades)


@pytest.mark.parametrize(
    ("stop", "outcome"),
    [
        pytest.param(99.8, (0, 1), id="at-the-minimum"),
        pytest.param(99.9, (1, 0), id="one-tick-below-the-minimum"),
        pytest.param(99.5, (0, 1), id="at-the-maximum"),
        pytest.param(99.4, (1, 0), id="one-tick-past-the-maximum"),
    ],
)
def test_stop_distance_limits_take_r_at_either_bound_and_discard_one_tick_past_it(test_asset_cfg, stop, outcome):
    assert _stop_distance_run(_close_entry(stop), "0.2-0.5%", test_asset_cfg) == outcome


@pytest.mark.parametrize(
    ("fill_bar", "signal", "outcome"),
    [
        pytest.param([100.4, 100.4, 100.4, 100.4], {"stop": 99.9}, (0, 1), id="next-open-at-the-maximum"),
        pytest.param([100.4, 100.4, 100.4, 100.4], {"stop": 99.8}, (1, 0), id="next-open-one-tick-past"),
        pytest.param([100.0, 100.6, 100.0, 100.6], {"stop_entry": 100.5, "stop": 100.0}, (0, 1), id="stop-entry-at-the-maximum"),
        pytest.param([100.0, 100.6, 100.0, 100.6], {"stop_entry": 100.5, "stop": 99.9}, (1, 0), id="stop-entry-one-tick-past"),
        pytest.param([100.4, 100.4, 100.4, 100.4], {"stop_entry": 100.0, "stop": 99.9}, (0, 1), id="gapped-stop-entry-at-the-maximum"),
        pytest.param([100.4, 100.4, 100.4, 100.4], {"stop_entry": 100.0, "stop": 99.8}, (1, 0), id="gapped-stop-entry-one-tick-past"),
    ],
)
def test_stop_distance_limits_measure_r_from_the_fill(test_asset_cfg, fill_bar, signal, outcome):
    stop_entry = "stop_entry" in signal
    data = _bars(fill_bar, fill_bar, fill_bar) if stop_entry else _bars([100.0, 100.0, 100.0, 100.0], fill_bar, fill_bar)
    signal = {"time": data.index[0], "side": "long", **signal}

    assert _stop_distance_run(signal, "0-0.5%", test_asset_cfg, data) == outcome


@pytest.mark.parametrize(
    ("limits", "outcome"),
    [pytest.param("0-0.95%", (1, 0), id="rounded-past-the-maximum"), pytest.param("1%+", (0, 1), id="rounded-up-to-the-minimum")],
)
def test_stop_distance_limits_measure_the_stop_rounded_to_the_tick_grid(test_asset_cfg, limits, outcome):
    cfg = replace(test_asset_cfg, tick_size=0.25)

    assert _stop_distance_run(_close_entry(99.1), limits, cfg) == outcome


@pytest.mark.parametrize(
    ("pip_size", "stop", "outcome"),
    [
        pytest.param(0.01, 99.8, (0, 1), id="pip-size-inside"),
        pytest.param(0.01, 99.0, (1, 0), id="pip-size-past-the-maximum"),
        pytest.param(None, 99.8, (1, 0), id="tick-size-below-the-minimum"),
        pytest.param(None, 99.0, (0, 1), id="tick-size-inside"),
    ],
)
def test_stop_distance_limits_in_pips_use_pip_size_or_fall_back_to_tick_size(test_asset_cfg, pip_size, stop, outcome):
    cfg = replace(test_asset_cfg, pip_size=pip_size)

    assert _stop_distance_run(_close_entry(stop), "5-20pips", cfg) == outcome


@pytest.mark.parametrize(
    ("limits", "stop", "outcome"),
    [
        pytest.param("0.2%+", 95.0, (0, 1), id="minimum-only-takes-a-wide-stop"),
        pytest.param("0.2%+", 99.9, (1, 0), id="minimum-only-discards-a-tight-stop"),
        pytest.param("0-0.5%", 99.9, (0, 1), id="maximum-only-takes-a-tight-stop"),
        pytest.param("0-0.5%", 99.4, (1, 0), id="maximum-only-discards-a-wide-stop"),
    ],
)
def test_stop_distance_limits_with_only_a_minimum_or_only_a_maximum(test_asset_cfg, limits, stop, outcome):
    assert _stop_distance_run(_close_entry(stop), limits, test_asset_cfg) == outcome


def test_stop_distance_limits_measure_r_before_spread_and_slippage(cost_asset_cfg):
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.0, 100.0, 100.0])
    signals = [_close_entry(99.5)]

    metrics, trades = _run(data, signals, cost_asset_cfg, with_costs=True, stop_distance=StopDistanceLimits.parse("0-0.5%"))

    assert (metrics["Discarded"], len(trades)) == (0, 1)


def test_min_risk_ticks_still_discards_a_signal_inside_the_stop_distance_limits(test_asset_cfg):
    cfg = replace(test_asset_cfg, min_risk_ticks=10)

    assert _stop_distance_run(_close_entry(99.5), "0-1%", cfg) == (1, 0)
    assert _stop_distance_run(_close_entry(99.0), "0-1%", cfg) == (0, 1)
