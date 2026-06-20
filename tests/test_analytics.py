from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from tester_framework.analytics import analyze_trades, classify_outcome, strategy_metrics, trade_stats
from tester_framework.models import Side


def test_classify_outcome():
    assert classify_outcome(1e-10) == "breakeven"
    assert classify_outcome(1.0) == "win"
    assert classify_outcome(-1.0) == "loss"


def test_trade_stats_counts_and_expectancy():
    sample_trades = [
        {"side": Side.LONG, "realized_r": 1.0, "entry_time": pd.Timestamp("2025-01-01 23:30:00-02:00"), "holding_duration": pd.Timedelta(hours=1)},
        {"side": Side.SHORT, "realized_r": 0.0, "entry_time": pd.Timestamp("2025-01-02 04:00:00"), "holding_duration": pd.Timedelta(hours=2)},
        {"side": Side.SHORT, "realized_r": -1.0, "entry_time": pd.Timestamp("2025-01-03 04:00:00"), "holding_duration": pd.Timedelta(hours=3)},
        {"side": Side.LONG, "realized_r": -0.5, "entry_time": pd.Timestamp("2025-02-01 04:00:00"), "holding_duration": pd.Timedelta(hours=4)},
    ]
    stats = trade_stats(sample_trades)
    assert (stats["Wins"], stats["BE"], stats["Losses"], stats["Win Rate"]) == (1, 1, 2, 25.0)
    assert stats["Expectancy R"] == -0.125
    assert stats["Max Losing Streak"] == 2
    assert stats["Avg Duration"] == "2h 30m"
    assert stats["Median Duration"] == "2h 30m"


def test_analyze_trades_periods_and_outcomes():
    sample_trades = [
        {"side": Side.LONG, "realized_r": 1.0, "entry_time": pd.Timestamp("2025-01-01 23:30:00-02:00"), "holding_duration": pd.Timedelta(hours=1), "exit_reason": "target", "mfe_r": 1.0, "giveback_r": 0.0},
        {"side": Side.SHORT, "realized_r": 0.0, "entry_time": pd.Timestamp("2025-01-02 04:00:00"), "holding_duration": pd.Timedelta(hours=2), "exit_reason": "stop", "mfe_r": 0.0, "giveback_r": 0.0},
        {"side": Side.SHORT, "realized_r": -1.0, "entry_time": pd.Timestamp("2025-01-03 04:00:00"), "holding_duration": pd.Timedelta(hours=3), "exit_reason": "stop", "mfe_r": 0.0, "giveback_r": 0.0},
        {"side": Side.LONG, "realized_r": -0.5, "entry_time": pd.Timestamp("2025-02-01 04:00:00"), "holding_duration": pd.Timedelta(hours=4), "exit_reason": "stop", "mfe_r": 0.0, "giveback_r": 0.0},
    ]
    analytics = analyze_trades(sample_trades, "fixed")
    overall, long_stats, short_stats = analytics["outcomes"]
    assert (overall["Wins"], overall["BE"], overall["Losses"]) == (1, 1, 2)
    assert (long_stats["Wins"], long_stats["Losses"]) == (1, 1)
    assert (short_stats["BE"], short_stats["Losses"]) == (1, 1)
    assert [row["Period"] for row in analytics["weekday"]] == ["Thursday", "Friday", "Saturday"]
    assert analytics["weekday"][0]["Trades"] == 2
    assert [row["Period"] for row in analytics["month"]] == ["January", "February"]
    assert analytics["month"][0]["Trades"] == 3
    assert analyze_trades([], "trailing")["outcomes"][0]["Trades"] == 0


def test_analyze_trades_groups_entry_years_in_order():
    trades = [
        {"side": Side.LONG, "realized_r": 1.0, "entry_time": pd.Timestamp("2025-01-01"), "holding_duration": pd.Timedelta(hours=1)},
        {"side": Side.SHORT, "realized_r": -1.0, "entry_time": pd.Timestamp("2024-12-31 23:30:00-02:00"), "holding_duration": pd.Timedelta(hours=1)},
        {"side": Side.LONG, "realized_r": 0.0, "entry_time": pd.Timestamp("2024-01-01"), "holding_duration": pd.Timedelta(hours=1)},
    ]

    years = analyze_trades(trades, "fixed")["year"]

    assert [row["Period"] for row in years] == ["2024", "2025"]
    assert (years[0]["Trades"], years[0]["Wins"], years[0]["BE"], years[0]["Losses"], years[0]["Win Rate"]) == (1, 0, 1, 0, 0.0)
    assert (years[1]["Trades"], years[1]["Wins"], years[1]["BE"], years[1]["Losses"], years[1]["Win Rate"]) == (2, 1, 0, 1, 50.0)
    assert analyze_trades([], "fixed")["year"] == []


def test_strategy_metrics_hook():
    hook = SimpleNamespace(
        calculate_metrics=lambda data, signals, trades, asset, timeframe, params: {"Signals": len(signals), "RR": params["risk_reward_ratio"]}
    )
    assert strategy_metrics(hook, None, pd.DataFrame([{"time": pd.Timestamp("2025-01-01"), "side": "long", "stop": 99}]), [], "TEST", "1h", {"risk_reward_ratio": 2}) == {"Signals": 1, "RR": 2}


def test_strategy_metrics_rejects_non_dict():
    hook = SimpleNamespace(calculate_metrics=lambda *args: [])
    with pytest.raises(ValueError):
        strategy_metrics(hook, None, pd.DataFrame(), [], "TEST", "1h", {})
