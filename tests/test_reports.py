from __future__ import annotations

import math
import re

import pandas as pd
import pytest

import tester_framework.reports as reports
import tester_framework.runner as runner
from tester_framework.cli import RunConfig
from tester_framework.reports import format_metric, metric_class
from tester_framework.runner import cache_data


def test_format_metric_return_positive():
    assert format_metric("Return", 1.234) == "+1.23%"


def test_format_metric_return_negative():
    assert format_metric("Return", -1.234) == "-1.23%"


def test_format_metric_max_dd():
    assert format_metric("Max DD", 5.5) == "5.50%"


def test_format_metric_pair():
    assert format_metric("Return", (1.0, 2.0)) == "+1.00% / +2.00%"


def test_format_metric_undefined_and_count_pair():
    assert format_metric("Sharpe Ratio", math.nan) == "N/A"
    assert format_metric("W", (2, 1)) == "2 / 1"


def test_metric_class_return():
    assert metric_class("Return", 1.0) == "good"
    assert metric_class("Return", -1.0) == "bad"
    assert metric_class("Return", 0.0) == "neutral"


def test_metric_class_pair_uses_last():
    assert metric_class("Return", (1.0, -1.0)) == "bad"


def test_write_results_html_keeps_asset_breakdown_as_quick_view(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path)
    table = pd.DataFrame(
        columns=[
            "Asset",
            "TF",
            "RR",
            "Exit Mode",
            "Trades",
            "Discarded",
            "Unresolved",
            "Long",
            "Short",
            "W",
            "BE",
            "L",
            "Win Rate",
            "Expectancy R",
            "Avg Duration",
            "Return",
            "Max DD",
            "Sharpe Ratio",
            "Return / DD",
        ]
    )
    config = RunConfig(
        ("ema50",), None, None, "60d", "yfinance", "all", "1", "all", "1", 10_000, False, None,
        max_trades=2, trade_html=False,
    )

    html = reports.write_results_html(table, config).read_text(encoding="utf-8")
    header_row = re.search(r"<thead><tr>(.*?)</tr></thead>", html, re.S)

    assert header_row is not None
    assert re.findall(r"<th>(.*?)</th>", header_row.group(1)) == [
        "Asset",
        "TF",
        "RR",
        "Exit Mode",
        "Return",
        "Max DD",
        "Sharpe",
        "Ret / DD",
    ]
    assert "first 2 closed trades per variant" in html
    assert "trade charts off" in html


def test_no_trade_html_skips_writer_and_keeps_empty_chart_cell(
    monkeypatch, tmp_path, base_data, hourly_index, test_asset_cfg
):
    monkeypatch.setattr(
        runner,
        "write_trade_html",
        lambda *_args: pytest.fail("trade HTML writer should not be called"),
    )
    row = runner.run_variant(
        {
            "strategy": "ema50",
            "data_cache": cache_data(base_data, str(tmp_path), 0),
            "signals": pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}]),
            "asset": "TEST",
            "asset_cfg": test_asset_cfg,
            "timeframe": "1h",
            "execution_timeframe": "1h",
            "risk_reward_ratio": 1,
            "exit_mode": "trailing",
            "operation": "all",
            "risk_pct": 1,
            "capital": 10_000,
            "with_costs": False,
            "time_period": "1d",
            "data_source": "local",
            "max_trades": None,
            "trade_html": False,
        }
    )

    trade = row["_analytics"]["managed"]["trades"][0]
    assert trade["chart_path"] is None
    assert "<td>-</td>" in reports._managed_section(row["_analytics"])
