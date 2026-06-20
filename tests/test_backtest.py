from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

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
    assert risk_for("MNQ", "1") == 1.0
    assert risk_for("MNQ", "MGC=1,MNQ=0.5") == 0.5
    assert risk_for("MES", "MGC=1,MNQ=0.5") == 1.0


@pytest.mark.parametrize("value", ["abc", "MGC=x", "0", "nan", "inf"])
def test_risk_for_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="--risk"):
        risk_for("MGC", value)


def test_normalize_signals_rejects_invalid_side():
    with pytest.raises(ValueError):
        normalize_signals(pd.DataFrame([{"time": pd.Timestamp("2025-01-01"), "side": "buy", "stop": 99}]))


def test_long_target_trade(base_data, hourly_index, test_asset_cfg):
    signals = pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])
    metrics, trades = run_backtest(
        base_data,
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
    assert trades[0]["exit_reason"] == ExitReason.TARGET
    assert metrics["Return"] > 0


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


def test_same_bar_stop_priority(base_data, hourly_index, test_asset_cfg):
    both_data = base_data.copy()
    both_data.loc[hourly_index[1], ["high", "low"]] = [102, 98]
    signals = pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])
    _, trades = run_backtest(
        both_data,
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


def test_close_entry_signal(base_data, hourly_index, test_asset_cfg):
    close_entry_data = base_data.copy()
    close_entry_data.loc[hourly_index[1], ["open", "high", "low", "close"]] = [101.0, 102.0, 100.5, 101.5]
    close_entry = pd.DataFrame([{"time": hourly_index[0], "side": "long", "entry": 101.0, "stop": 100.0}])
    _, trades = run_backtest(
        close_entry_data,
        close_entry,
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
    assert trades[0]["entry_i"] == 0
    assert trades[0]["entry"] == 101.0
    assert trades[0]["exit_reason"] == ExitReason.TARGET


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


def test_trailing_target_completion(base_data, hourly_index, test_asset_cfg):
    _, trades = run_backtest(
        base_data,
        pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}]),
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="trailing",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=test_asset_cfg,
    )
    assert trades[0]["exit_reason"] == ExitReason.TARGET


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


def test_cost_accounting_preserves_raw_execution(base_data, hourly_index, cost_asset_cfg):
    cost_idx = pd.date_range("2025-02-01", periods=6, freq="h")
    cost_data = pd.DataFrame(
        {
            "open": [100.0] * 6,
            "high": [100.0, 101.1, 100.0, 100.0, 101.1, 100.0],
            "low": [100.0, 99.5, 100.0, 100.0, 99.5, 100.0],
            "close": [100.0] * 6,
            "volume": [0] * 6,
        },
        index=cost_idx,
    )
    cost_signals = pd.DataFrame(
        [
            {"time": cost_idx[0], "side": "long", "entry": 100.0, "stop": 99.0},
            {"time": cost_idx[3], "side": "long", "entry": 100.0, "stop": 99.0},
        ]
    )
    gross_metrics, gross_trades = run_backtest(
        cost_data,
        cost_signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=False,
        asset_cfg=cost_asset_cfg,
    )
    net_metrics, net_trades = run_backtest(
        cost_data,
        cost_signals,
        strategy="test",
        asset="TEST",
        timeframe="1h",
        risk_reward_ratio=1,
        exit_mode="fixed",
        operation="all",
        risk_pct=1,
        capital=10000,
        with_costs=True,
        asset_cfg=cost_asset_cfg,
    )
    assert gross_trades[0]["target"] == net_trades[0]["target"] == 101.0
    assert gross_trades[0]["raw_exit"] == net_trades[0]["raw_exit"]
    assert gross_trades[0]["realized_r"] == net_trades[0]["realized_r"] == 1.0
    assert net_trades[0]["outcome"] == "win"
    assert gross_trades[0]["qty"] == net_trades[0]["qty"]
    assert net_trades[1]["qty"] < gross_trades[1]["qty"]
    assert net_metrics["Gross"]["Return"] > net_metrics["Net"]["Return"]
    assert gross_metrics["Return"] > net_metrics["Return"]


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
    assert net_partial_trades[0]["gross_pnl"] > net_partial_trades[0]["net_pnl"]


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


def test_add_trade_counts_splits_sides(base_data, hourly_index, test_asset_cfg):
    signals = pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])
    metrics, trades = run_backtest(
        base_data,
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
    row = add_trade_counts(metrics, trades, "all")
    assert row["Long"] == 1
    assert row["Short"] == 0


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
