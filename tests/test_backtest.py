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
from tester_framework.models import ExitReason, Side


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
    # 1% of 10000 over R = 1 point sizes 100 contracts; 2R banks 200.
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
    # High and low are both 1 point from the open: the engine assumes the adverse extreme comes first.
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
    # Entry 100, stop 99, 3R target 103 never reached. Bar 2 opens at 101.5 (1.5R), so the trail moves to
    # 101.0 before the low-first path dips to 100.8. Without the open update the stop stays at 99 until 102.5.
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 99.5, 100.0], [101.5, 102.5, 100.8, 102.0])
    signals = [{"time": data.index[0], "side": "long", "stop": 99.0}]
    _, trailing = _run(data, signals, test_asset_cfg, risk_reward_ratio=3, exit_mode="trailing")
    fixed_metrics, fixed = _run(data, signals, test_asset_cfg, risk_reward_ratio=3, exit_mode="fixed")
    assert [(t["exit_reason"], t["exit_i"], t["exit"], t["realized_r"]) for t in trailing] == [(ExitReason.STOP, 2, 101.0, 1.0)]
    assert fixed == []
    assert fixed_metrics["Unresolved"] == 1


def test_short_trailing_stop_follows_low_and_never_loosens(test_asset_cfg):
    # Short entry 100, stop 101. Bar 1 reaches 98.8 (1.2R), so the trail ceils to 99.3. Bar 2's low of 98.9
    # would put it at 99.45; it must stay at 99.3, where the rally to 99.5 exits.
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
    # Costs per side: half spread 0.1 + slippage 0.1 on price, commission 0.1 per contract.
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
    # The second trade sizes from net equity 10040, not gross 10100.
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
    # 100 contracts: gross 1 point each = 100; net 0.6 point each = 60, minus 0.1 x 100 x 2 commission = 40.
    assert trade["gross_pnl"] == 100.0
    assert trade["net_pnl"] == pytest.approx(40.0)
    assert trade["net_realized_r"] == pytest.approx(0.4)
    # Bar 1 closes flat with the trade open: net mark-to-market is -0.4 x 100 - 20 commission = 9940.
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
    # Each fill nets (target - 0.2) - 100.2 per contract, minus 0.2 commission: 33 x 0.4 + 33 x 1.4 + 34 x 2.4.
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


def test_unresolved_trade_discarded(base_data, hourly_index, test_asset_cfg):
    unresolved = base_data.copy()
    unresolved.loc[hourly_index[1], ["high", "low", "close"]] = [100.5, 99.5, 100.0]
    signals = pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])
    metrics, trades = run_backtest(
        unresolved,
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
    assert not trades
    assert metrics["Return"] == 0
    assert metrics["Unresolved"] == 1


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


def _gap_run(cfg, gap_bar, side, stop, risk_reward_ratio, exit_mode="fixed", next_bar=None):
    bars = [[100.0, 100.0, 100.0, 100.0], [100.0, 100.5, 99.5, 100.0], gap_bar]
    if next_bar:
        bars.append(next_bar)
    idx = pd.date_range("2025-01-01", periods=len(bars), freq="h")
    data = pd.DataFrame(bars, index=idx, columns=["open", "high", "low", "close"])
    _, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": side, "stop": stop}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=risk_reward_ratio, exit_mode=exit_mode,
        operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg,
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
    trade = _gap_run(test_asset_cfg, gap_bar, side, stop, risk_reward_ratio=3)
    assert [(f["exit_reason"], f["raw_exit"], f["gap_exit"]) for f in trade["exits"]] == [(reason, fill, True)]
    assert trade["exit"] == fill


def test_intrabar_stop_and_target_are_not_gap_exits(test_asset_cfg):
    target = _gap_run(test_asset_cfg, [100.0, 103.5, 99.5, 103.0], "long", 99.0, risk_reward_ratio=3)
    stop = _gap_run(test_asset_cfg, [100.0, 100.5, 98.0, 98.5], "long", 99.0, risk_reward_ratio=3)
    assert [(f["raw_exit"], f["gap_exit"]) for f in target["exits"]] == [(103.0, False)]
    assert [(f["raw_exit"], f["gap_exit"]) for f in stop["exits"]] == [(99.0, False)]


def test_gap_through_partial_targets_fills_each_crossed_target_at_open(test_asset_cfg):
    trade = _gap_run(
        test_asset_cfg, [102.5, 102.6, 102.4, 102.5], "long", 99.0, risk_reward_ratio=3, exit_mode="partial",
        next_bar=[102.5, 103.5, 102.4, 103.0],
    )
    assert [(f["target_r"], f["qty"], f["raw_exit"], f["gap_exit"]) for f in trade["exits"]] == [
        (1.0, 33.0, 102.5, True),
        (2.0, 33.0, 102.5, True),
        (3, 34.0, 103.0, False),
    ]
    assert trade["gross_pnl"] == 33 * 2.5 * 2 + 34 * 3.0


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
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg,
    )
    trade = trades[0]
    assert trade["realized_r"] == 0
    assert trade["mfe_r"] == 1
    assert trade["giveback_r"] == 1


def test_unresolved_partial_trade_discards_completed_fills(test_asset_cfg):
    idx = pd.date_range("2025-01-01", periods=3, freq="h")
    data = pd.DataFrame(
        {"open": [100, 100, 100.5], "high": [100, 101, 101], "low": [100, 99.5, 100], "close": [100, 100.5, 100.5], "volume": [0, 0, 0]},
        index=idx,
    )
    metrics, trades = run_backtest(
        data,
        pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=2, exit_mode="partial", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=test_asset_cfg,
    )
    assert trades == []
    assert metrics["Unresolved"] == 1
    assert metrics["Return"] == 0


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
    rejected_day, _ = run_backtest(**kwargs, days=(4,), months=(2,))
    rejected_month, _ = run_backtest(**kwargs, days=(5,), months=(1,))

    assert len(trades) == 1
    assert accepted["Discarded"] == 0
    assert rejected_day["Discarded"] == 1
    assert rejected_month["Discarded"] == 1


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
    # 1000 contracts on a 0.1 R: net (99.9 - 100.2) x 1000 - 0.1 x 1000 x 2 commission = -500, or -5R.
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
    assert math.isnan(_curve_metrics(np.array([100.0, 0.0, 100.0]), 252)["Sharpe Ratio"])


def test_curve_metrics_match_pandas_reference():
    curve = np.array([10_000.0, 10_100.0, 9_950.0, 10_200.0])
    series = pd.Series(curve)
    returns = series.pct_change(fill_method=None).dropna()
    drawdown = (series / series.cummax() - 1) * 100
    total_return = (curve[-1] / curve[0] - 1) * 100
    max_dd = abs(float(drawdown.min()))
    sharpe = math.sqrt(252) * float(returns.mean() / returns.std())

    assert _curve_metrics(curve, 252) == {
        "Return": round(total_return, 2),
        "Max DD": round(max_dd, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Return / DD": round(total_return / max_dd, 2),
    }


def test_make_metrics_skips_duplicate_net_calculation_without_costs(monkeypatch):
    calls = []
    monkeypatch.setattr(
        backtest,
        "_curve_metrics",
        lambda curve, _bars: calls.append(curve) or {"Return": 0, "Max DD": 0, "Sharpe Ratio": 0, "Return / DD": 0},
    )
    curve = np.array([10_000.0, 10_100.0])

    metrics = backtest.make_metrics("TEST", "1h", "test", curve, curve, False, 252)
    assert len(calls) == 1
    assert metrics["Gross"] == metrics["Net"]

    calls.clear()
    backtest.make_metrics("TEST", "1h", "test", curve, curve, True, 252)
    assert len(calls) == 2


def test_sharpe_uses_execution_timeframe(test_asset_cfg):
    cfg = replace(test_asset_cfg, bars_per_year={"1h": 100, "5m": 400})
    idx = pd.date_range("2025-01-01", periods=3, freq="h")
    data = pd.DataFrame(
        {"open": [100, 100, 100], "high": [100, 101.1, 100], "low": [100, 99.5, 100], "close": [100, 101, 100], "volume": [0, 0, 0]},
        index=idx,
    )
    kwargs = dict(
        data=data, signals=pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99}]),
        strategy="test", asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all",
        risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg,
    )
    hourly, _ = run_backtest(**kwargs, execution_timeframe="1h")
    five_minute, _ = run_backtest(**kwargs, execution_timeframe="5m")
    assert five_minute["Sharpe Ratio"] == pytest.approx(hourly["Sharpe Ratio"] * 2, abs=0.02)


def test_result_columns_include_long_for_all():
    assert "Strategy" in result_columns("all")
    assert "Session" in result_columns("all", include_session=True)
    assert "Long" in result_columns("all")
    assert "Long" not in result_columns("long_only")
    assert "Discarded" in result_columns("all")
    assert "Unresolved" in result_columns("all")


def _long_then_short():
    # Long from bar 1 open 100 hits 101 on bar 1; short from bar 3 open 101 hits 100 on bar 3.
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
    # 1% of 10000 over R = 1 point sizes 100 contracts, below a 101-contract minimum.
    data = _bars([100.0, 100.0, 100.0, 100.0], [100.0, 101.5, 99.5, 101.0])
    signals = [{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.0}]
    at_min, traded = _run(data, signals, replace(test_asset_cfg, min_qty=100.0))
    below, trades = _run(data, signals, replace(test_asset_cfg, min_qty=101.0))
    assert ([t["qty"] for t in traded], at_min["Discarded"]) == ([100.0], 0)
    assert (trades, below["Discarded"]) == ([], 1)


def test_signal_while_trade_is_open_is_discarded(test_asset_cfg):
    # The first trade enters bar 1 and exits on bar 2; the second signal would enter on bar 2.
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
        # Long: stop 99.1 floors to 99.0 (R = 1.0); target 100 + 1.1 = 101.1 ceils to 101.25.
        ("long", 99.1, [100.0, 101.5, 99.5, 101.0], 99.0, 101.25),
        # Short: stop 100.9 ceils to 101.0 (R = 1.0); target 100 - 1.1 = 98.9 floors to 98.75.
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
    # 1% of 10000 over R = 3 points x 5 per point is 6.67 contracts, floored to the 0.1 step: 6.6.
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


def test_close_entry_sharpe_has_no_return_on_entry_bars(test_asset_cfg):
    metrics, _ = _two_winning_close_entries(test_asset_cfg)

    # Curve 10000, 10000, 10100, 10100, 10201, 10201: bar returns 0, 1%, 0, 1%, 0.
    # mean 0.004, sample std sqrt(3e-5), so Sharpe = sqrt(1716) * 0.004 / sqrt(3e-5) = 30.25.
    assert metrics["Sharpe Ratio"] == 30.25


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

    # 100 contracts close bar 1 at 99.5 (equity 9950) before the 101 target fills on bar 2.
    assert (trades[0]["exit_i"], trades[0]["exit_reason"]) == (2, ExitReason.TARGET)
    assert metrics["Max DD"] == 0.5
    assert metrics["Return"] == 1


def mnq_like_cfg(test_asset_cfg):
    return replace(test_asset_cfg, point_value=2.0, tick_size=0.25, min_risk_ticks=4, bars_per_year={"1m": 347760})


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
    # 2020-03-16 MNQ: flat breakout bar, 1-tick stop, halted bars missing, reopen gapping through the 2R target.
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
    # A long stop floors to the tick: 99.1 -> 99.0 is 4 ticks (traded), 99.3 -> 99.25 is 3 ticks (discarded).
    data = min_risk_data()
    cfg = mnq_like_cfg(test_asset_cfg)
    at_min, _ = min_risk_run(data, pd.DataFrame([{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.1}]), cfg)
    below, trades = min_risk_run(data, pd.DataFrame([{"time": data.index[0], "side": "long", "entry": 100.0, "stop": 99.3}]), cfg)
    assert at_min["Discarded"] == 0
    assert below["Discarded"] == 1
    assert trades == []
