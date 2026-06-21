from __future__ import annotations

import math
import re
from types import SimpleNamespace

import pandas as pd
import pytest

import tester_framework.reports as reports
import tester_framework.runner as runner
from tester_framework.analytics import decode_analytics
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
        max_trades=2, trade_html=0, days=(2, 0), months=(9, 1),
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
        "Details",
    ]
    assert header_row.group(1).count('aria-sort="none"') == 4
    assert header_row.group(1).count('class="sort-button"') == 4
    assert "first 2 closed trades per variant" in html
    assert "trade charts off" in html
    assert "days Monday, Wednesday" in html
    assert "months January, September" in html

    capped_config = RunConfig(
        ("ema50",), None, None, None, "60d", "yfinance", "all", "1", "all", "1", 10_000, False, None,
        trade_html=3,
    )
    capped_html = reports.write_results_html(table, capped_config).read_text(encoding="utf-8")
    assert "first 3 trade charts per variant" in capped_html


def test_zero_trade_html_skips_writer_and_keeps_empty_chart_cell(
    monkeypatch, tmp_path, base_data, hourly_index, test_asset_cfg
):
    monkeypatch.setattr(
        runner,
        "write_trade_html",
        lambda *_args: pytest.fail("trade HTML writer should not be called"),
    )
    monkeypatch.setattr(
        runner,
        "load_strategy",
        lambda _name: SimpleNamespace(
            generate_signals=lambda *_args, **_kwargs: pd.DataFrame(
                [{"time": hourly_index[0], "side": "long", "stop": 99}]
            )
        ),
    )
    row = runner.run_variants(
        {
            "strategy": "ema50",
            "data_cache": cache_data(base_data, str(tmp_path), 0),
            "variants": ((1, "trailing"),),
            "asset": "TEST",
            "asset_cfg": test_asset_cfg,
            "timeframe": "1h",
            "execution_timeframe": "1h",
            "operation": "all",
            "risk_pct": 1,
            "capital": 10_000,
            "with_costs": False,
            "time_period": "1d",
            "data_source": "local",
            "max_trades": None,
            "trade_html": 0,
            "session": None,
            "days": (),
            "months": (),
        }
    )[0]

    trade = decode_analytics(row["_analytics"])["managed"]["trades"][0]
    assert trade["chart_path"] is None
    assert "<td>-</td>" in reports._trade_table([trade])


def test_trade_html_limit_is_per_variant_and_keeps_all_trades(
    monkeypatch, tmp_path, base_data, test_asset_cfg
):
    trades = [{}, {}, {}]
    charted = []
    collected = []
    monkeypatch.setattr(runner.gc, "collect", lambda: collected.append(True))
    monkeypatch.setattr(
        runner,
        "load_strategy",
        lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: pd.DataFrame()),
    )
    monkeypatch.setattr(runner, "run_backtest", lambda *_args, **_kwargs: ({"Gross": {"Return": 0}}, trades))
    monkeypatch.setattr(runner, "strategy_metrics", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "analyze_trades", lambda items, _mode: {"trades": items})
    monkeypatch.setattr(
        runner,
        "add_trade_counts",
        lambda *_args, **_kwargs: {"Strategy": "ema50", "Asset": "TEST", "TF": "1h"},
    )
    monkeypatch.setattr(
        runner,
        "write_trade_html",
        lambda _data, trade, _strategy: charted.append(trade) or tmp_path / f"{len(charted)}.html",
    )

    row = runner.run_variants(
        {
            "strategy": "ema50",
            "data_cache": cache_data(base_data, str(tmp_path), 0),
            "variants": ((1, "fixed"),),
            "asset": "TEST",
            "asset_cfg": test_asset_cfg,
            "timeframe": "1h",
            "execution_timeframe": "1h",
            "operation": "all",
            "risk_pct": 1,
            "capital": 10_000,
            "with_costs": False,
            "time_period": "1d",
            "data_source": "local",
            "max_trades": None,
            "trade_html": 2,
            "session": None,
            "days": (),
            "months": (),
        }
    )[0]

    assert len(charted) == 2
    assert [trade["chart_path"] is not None for trade in trades] == [True, True, False]
    assert collected == [True]
    assert decode_analytics(row["_analytics"])["trades"] == trades
    assert row["_risk_pct"] == 1


def test_run_variants_generates_signals_once_per_batch(monkeypatch, tmp_path, base_data, test_asset_cfg):
    generated = []
    backtests = []
    monkeypatch.setattr(
        runner,
        "load_strategy",
        lambda _name: SimpleNamespace(
            generate_signals=lambda *_args, **_kwargs: generated.append(True) or pd.DataFrame()
        ),
    )
    monkeypatch.setattr(
        runner,
        "run_backtest",
        lambda *_args, **kwargs: backtests.append((kwargs["risk_reward_ratio"], kwargs["exit_mode"]))
        or ({"Gross": {"Return": 0}}, []),
    )

    rows = runner.run_variants(
        {
            "strategy": "ema50",
            "data_cache": cache_data(base_data, str(tmp_path), 0),
            "variants": ((1, "fixed"), (2, "trailing")),
            "asset": "TEST",
            "asset_cfg": test_asset_cfg,
            "timeframe": "1h",
            "execution_timeframe": "1h",
            "operation": "all",
            "risk_pct": 1,
            "capital": 10_000,
            "with_costs": False,
            "time_period": "1d",
            "data_source": "local",
            "max_trades": None,
            "trade_html": 0,
            "session": None,
            "days": (),
            "months": (),
        }
    )

    assert generated == [True]
    assert backtests == [(1, "fixed"), (2, "trailing")]
    assert len(rows) == 2


def test_write_results_html_creates_filterable_static_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path)
    analytics = {
        "outcomes": [
            {
                "Group": "All", "Trades": 1, "Wins": 1, "BE": 0, "Losses": 0, "Win Rate": 100.0,
                "Avg Win R": 1.0, "Avg Loss R": 0.0, "Expectancy R": 1.0, "Max Losing Streak": 0,
                "Avg Duration": "5m", "Median Duration": "5m",
            }
        ],
        "weekday": [{"Period": "Monday", "Trades": 1, "Wins": 1, "BE": 0, "Losses": 0, "Win Rate": 100.0}],
        "month": [{"Period": "January", "Trades": 1, "Wins": 1, "BE": 0, "Losses": 0, "Win Rate": 100.0}],
        "year": [{"Period": "2025", "Trades": 1, "Wins": 1, "BE": 0, "Losses": 0, "Win Rate": 100.0}],
    }
    table = pd.DataFrame(
        [
            {"Strategy": "ema50", "Asset": "MES", "TF": "5m", "Session": "london", "RR": "1", "Exit Mode": "fixed", "Return": 1.0, "Max DD": 2.0, "Sharpe Ratio": 0.5, "Return / DD": 0.5, "_risk_pct": 0.5, "_analytics": analytics, "_strategy_metrics": {}},
            {"Strategy": "orb_candle", "Asset": "MNQ", "TF": "10m", "Session": "ny", "RR": "2", "Exit Mode": "trailing", "Return": 2.0, "Max DD": 1.0, "Sharpe Ratio": 1.5, "Return / DD": 2.0, "_risk_pct": 1.0, "_analytics": {**analytics, "managed": {"Mode": "trailing", "Target Completions": 0, "Stop Completions": 0, "Avg Realized R": 0, "Avg MFE R": 0, "Avg Giveback R": 0, "trades": []}}, "_strategy_metrics": {}},
        ]
    )
    config = RunConfig(
        ("ema50", "orb_candle"), "MES,MNQ", "5m,10m", "london,ny", "60d", "local", "all", "1,2", "fixed,trailing", "MES=0.5,MNQ=1", 10_000, False, None
    )

    path = reports.write_results_html(table, config)
    html = path.read_text(encoding="utf-8")
    fixed = (path.parent / "variants" / "000000.html").read_text(encoding="utf-8")
    trailing = (path.parent / "variants" / "000001.html").read_text(encoding="utf-8")

    assert path.name == "index.html"
    for key in ("strategy", "asset", "timeframe", "session", "rr", "exit-mode", "risk"):
        assert f'data-filter-menu="{key}"' in html
    assert 'data-variant-id="0" data-original-order="0"' in html
    assert 'data-risk="0.5%"' in html
    assert html.count('class="details-link"') == 2
    assert 'href="variants/000000.html" target="_blank" rel="noopener"' in html
    assert 'href="variants/000001.html" target="_blank" rel="noopener"' in html
    assert "variant-payload" not in html
    assert "variant-body" not in html
    assert 'class="trade-row"' not in html
    assert "Entry weekday (UTC)" not in html
    assert 'id="filter-match-count" aria-live="polite">2 of 2 variants' in html
    assert "Entry weekday (UTC)" in fixed
    assert "Managed trades" not in fixed
    assert "Trailing summary" in trailing
    assert "Trailing trades" in trailing
    assert "No trades" in trailing
    assert 'href="../index.html"' in fixed


def test_variant_trade_history_is_paginated_without_data_loss(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path)
    start = pd.Timestamp("2025-01-01 00:00")
    trades = []
    for index in range(2_001):
        entry = start + pd.Timedelta(minutes=index)
        trades.append(
            {
                "chart_path": tmp_path / "chart&1.html" if index == 0 else None,
                "entry_time": entry,
                "exit_time": entry + pd.Timedelta(minutes=5),
                "holding_duration": "0 days 00:05:00",
                "side": "<long>" if index == 0 else "long",
                "outcome": "target_hit",
                "exits": [{"target_r": 2.0, "realized_r": 2.0, "qty": 1.0}],
                "risk_reward_ratio": 2.0,
                "realized_r": 2.0,
                "mfe_r": 2.5,
                "giveback_r": 0.5,
            }
        )
    analytics = {
        "outcomes": [
            {
                "Group": "All", "Trades": len(trades), "Wins": len(trades), "BE": 0, "Losses": 0,
                "Win Rate": 100.0, "Avg Win R": 2.0, "Avg Loss R": 0.0, "Expectancy R": 2.0,
                "Max Losing Streak": 0, "Avg Duration": "5m", "Median Duration": "5m",
            }
        ],
        "weekday": [],
        "month": [],
        "year": [],
        "managed": {
            "Mode": "partial",
            "Target Completions": len(trades),
            "Stop Completions": 0,
            "Avg Realized R": 2.0,
            "Avg MFE R": 2.5,
            "Avg Giveback R": 0.5,
            "trades": trades,
        },
    }
    table = pd.DataFrame(
        [
            {
                "Strategy": "ema50", "Asset": "M&ES", "TF": "5m", "RR": "2", "Exit Mode": "partial",
                "Return": 1.0, "Max DD": 2.0, "Sharpe Ratio": 0.5, "Return / DD": 0.5,
                "_analytics": analytics, "_strategy_metrics": {"<metric>": "<value>"},
            }
        ]
    )
    config = RunConfig(
        ("ema50",), "M&ES", "5m", None, "60d", "local", "all", "2", "partial", "1", 10_000,
        False, None,
    )

    path = reports.write_results_html(table, config)
    index_html = path.read_text(encoding="utf-8")
    page_paths = [path.parent / "variants" / name for name in ("000000.html", "000000-p2.html", "000000-p3.html")]
    pages = [page.read_text(encoding="utf-8") for page in page_paths]
    row_counts = [page.count('class="trade-row"') for page in pages]
    entry_times = re.findall(
        r'<tr class="trade-row"><td>.*?</td><td>(.*?)</td>',
        "".join(pages),
        re.S,
    )

    assert row_counts == [1_000, 1_000, 1]
    assert len(entry_times) == len(set(entry_times)) == 2_001
    assert entry_times[0] == "2025-01-01 00:00"
    assert entry_times[-1] == "2025-01-02 09:20"
    assert 'rel="next" href="000000-p2.html"' in pages[0]
    assert 'rel="prev" href="000000.html"' in pages[1]
    assert 'rel="next" href="000000-p3.html"' in pages[1]
    assert 'rel="prev" href="000000-p2.html"' in pages[2]
    assert 'rel="next"' not in pages[2]
    assert "Page 3 of 3 | trades 2001-2001 of 2001" in pages[2]
    assert 'href="../../../trades/chart&amp;1.html"' in pages[0]
    assert "&lt;long&gt;" in pages[0]
    assert "&lt;metric&gt;" in pages[0]
    assert "&lt;value&gt;" in pages[0]
    assert "Entry weekday (UTC)" in pages[0]
    assert "Entry weekday (UTC)" not in pages[1]
    assert "M&amp;ES" in index_html
    assert "variant-payload" not in index_html
    assert 'class="trade-row"' not in index_html


def test_write_results_html_replaces_same_named_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(
        reports,
        "datetime",
        SimpleNamespace(now=lambda: pd.Timestamp("2025-01-02 03:04:05")),
    )
    table = pd.DataFrame(columns=["Asset", "TF", "RR", "Exit Mode", *reports.FINANCIAL_COLUMNS])
    config = RunConfig(
        ("ema50",), None, None, None, "60d", "local", "all", "1", "fixed", "1", 10_000,
        False, None,
    )

    first = reports.write_results_html(table, config)
    stale = first.parent / "variants" / "stale.html"
    stale.write_text("stale", encoding="utf-8")
    second = reports.write_results_html(table, config)

    assert second == first
    assert not stale.exists()


def test_filter_options_use_domain_order():
    table = pd.DataFrame(
        {
            "Strategy": ["orb_fvg", "ema50", "orb_candle", "ema50", "ema50"],
            "Asset": ["MNQ", "MES", "MGC", "MES", "MES"],
            "TF": ["15m", "10m", "30m", "5m", "1h"],
            "Session": ["ny", "london", "asia", "ny", "ny"],
            "RR": ["5", "4", "3", "1", "2"],
            "Exit Mode": ["partial", "trailing", "fixed", "fixed", "fixed"],
            "_risk_pct": [2, 1, 0.5, 1, 1],
        }
    )

    html = reports._render_filters(table)

    def options(key):
        menu = re.search(rf'data-filter-menu="{key}".*?</details>', html, re.S)
        assert menu is not None
        return re.findall(r'<input[^>]+value="([^"]+)"', menu.group(0))

    assert options("strategy") == ["ema50", "orb_candle", "orb_fvg"]
    assert options("asset") == ["MES", "MGC", "MNQ"]
    assert options("timeframe") == ["5m", "10m", "15m", "30m", "1h"]
    assert options("session") == ["asia", "london", "ny"]
    assert options("rr") == ["1", "2", "3", "4", "5"]
    assert options("exit-mode") == ["fixed", "trailing", "partial"]
    assert options("risk") == ["0.5%", "1%", "2%"]


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

    path = reports.write_results_html(table, config, columns=["Strategy", "Asset", "TF", "Session", "RR", "Exit Mode", "Return", "Max DD", "Sharpe Ratio", "Return / DD"])
    html = path.read_text(encoding="utf-8")
    details = (path.parent / "variants" / "000000.html").read_text(encoding="utf-8")

    assert "Session" in html
    assert "ny=09:30-12:00" in html
    assert "Entry year (UTC)" not in html
    assert "Entry year (UTC)" in details
    assert '<td class="c" data-sort-value="1.25">' in html
    assert 'data-sort-value=""' in html
    assert "+9.00% / +1.25%" in html
    assert 'data-original-order="0"' in html
    assert 'id="filter-bar"' not in html
    assert "Number.isNaN(left.value)" in html
    assert "body.replaceChildren" in html


def test_run_variants_filters_signals_by_session(monkeypatch, tmp_path, test_asset_cfg):
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

    monkeypatch.setattr(
        runner,
        "load_strategy",
        lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: signals),
    )
    row = runner.run_variants(
        {
            "strategy": "ema50",
            "data_cache": cache_data(data, str(tmp_path), 0),
            "variants": ((1, "fixed"),),
            "asset": "TEST",
            "asset_cfg": test_asset_cfg,
            "timeframe": "1h",
            "execution_timeframe": "1h",
            "operation": "all",
            "risk_pct": 1,
            "capital": 10_000,
            "with_costs": False,
            "time_period": "1d",
            "data_source": "local",
            "max_trades": None,
            "trade_html": 0,
            "session": london_open,
            "days": (),
            "months": (),
        }
    )[0]

    assert row["Session"] == "london=08:00-10:00"
    assert row["Trades"] == 1
