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
from tester_framework.sessions import parse_sessions


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
        ("ema50",), None, None, None, "60d", "yfinance", "all", "1", "all", "1", 10_000, False, None,
        max_trades=2, trade_html=False,
    )

    html = reports.write_results_html(table, config).read_text(encoding="utf-8")
    header_row = re.search(r"<thead><tr>(.*?)</tr></thead>", html, re.S)

    assert header_row is not None
    headers = [re.sub(r"<[^>]+>", "", value) for value in re.findall(r"<th[^>]*>(.*?)</th>", header_row.group(1))]
    assert headers == [
        "Asset",
        "TF",
        "RR",
        "Exit Mode",
        "Return",
        "Max DD",
        "Sharpe",
        "Ret / DD",
    ]
    assert header_row.group(1).count('aria-sort="none"') == 4
    assert header_row.group(1).count('class="sort-button"') == 4
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
            "session": None,
        }
    )

    trade = row["_analytics"]["managed"]["trades"][0]
    assert trade["chart_path"] is None
    assert "<td>-</td>" in reports._managed_section(row["_analytics"])


def test_write_results_html_includes_session_column_when_present(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path)
    table = pd.DataFrame(
        [
            {
                "Strategy": "orb_candle",
                "Asset": "MNQ",
                "TF": "5m",
                "Session": "ny=09:30-12:00",
                "RR": "2",
                "Exit Mode": "fixed",
                "Return": (9.0, 1.25),
                "Max DD": (8.0, 2.5),
                "Sharpe Ratio": (7.0, math.nan),
                "Return / DD": (6.0, 4.5),
                "_analytics": {"outcomes": [{"Group": "All", "Trades": 0, "Wins": 0, "BE": 0, "Losses": 0, "Win Rate": 0.0, "Avg Win R": 0.0, "Avg Loss R": 0.0, "Expectancy R": 0.0, "Max Losing Streak": 0, "Avg Duration": "0 days 00:00:00", "Median Duration": "0 days 00:00:00"}], "weekday": [], "month": [], "year": [{"Period": "2025", "Trades": 1, "Wins": 1, "BE": 0, "Losses": 0, "Win Rate": 100.0}]},
                "_strategy_metrics": {},
            }
        ]
    )
    config = RunConfig(
        ("orb_candle",), "MNQ", "5m", "ny=09:30-12:00", "5d", "local", "all", "2", "fixed", "1", 10_000, True, None
    )

    html = reports.write_results_html(table, config, columns=["Strategy", "Asset", "TF", "Session", "RR", "Exit Mode", "Return", "Max DD", "Sharpe Ratio", "Return / DD"]).read_text(encoding="utf-8")

    assert "Session" in html
    assert "ny=09:30-12:00" in html
    assert "Entry year (UTC)" in html
    assert '<td class="c" data-sort-value="1.25">' in html
    assert 'data-sort-value=""' in html
    assert "+9.00% / +1.25%" in html
    assert 'data-original-order="0"' in html
    assert "Number.isNaN(leftValue)" in html


def test_run_variant_filters_signals_by_session(tmp_path, test_asset_cfg):
    london_open = parse_sessions("london=08:00-10:00")[0]
    data = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 100.0, 100.0, 100.0],
            "low": [100.0, 99.5, 100.0, 100.0, 100.0],
            "close": [100.0, 101.0, 100.0, 100.0, 100.0],
            "volume": [0, 0, 0, 0, 0],
        },
        index=pd.date_range("2025-01-02 08:00", periods=5, freq="1h"),
    )
    signals = pd.DataFrame(
        [
            {"time": pd.Timestamp("2025-01-02 08:00"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-01-02 11:00"), "side": "long", "stop": 99},
        ]
    )

    row = runner.run_variant(
        {
            "strategy": "ema50",
            "data_cache": cache_data(data, str(tmp_path), 0),
            "signals": signals,
            "asset": "TEST",
            "asset_cfg": test_asset_cfg,
            "timeframe": "1h",
            "execution_timeframe": "1h",
            "risk_reward_ratio": 1,
            "exit_mode": "fixed",
            "operation": "all",
            "risk_pct": 1,
            "capital": 10_000,
            "with_costs": False,
            "time_period": "1d",
            "data_source": "local",
            "max_trades": None,
            "trade_html": False,
            "session": london_open,
        }
    )

    assert row["Session"] == "london=08:00-10:00"
    assert row["Trades"] == 1
