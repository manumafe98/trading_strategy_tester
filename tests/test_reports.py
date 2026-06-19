from __future__ import annotations

import math
import re

import pandas as pd

import tester_framework.reports as reports
from tester_framework.cli import RunConfig
from tester_framework.reports import format_metric, metric_class


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
    config = RunConfig("ema50", None, None, "60d", "yfinance", "all", "1", "all", "1", 10_000, False, None)

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
