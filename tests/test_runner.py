from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import tester_framework.reports as reports
import tester_framework.runner as runner
from tester_framework.analytics import decode_analytics
from tester_framework.cli import RunConfig
from tester_framework.runner import cache_data
from tester_framework.sessions import parse_sessions


def _task(data, cache_dir, asset_cfg, **overrides):
    task = {
        "strategy": "ema50",
        "data_cache": cache_data(data, str(cache_dir), 0),
        "variants": ((1, "fixed"),),
        "asset": "TEST",
        "asset_cfg": asset_cfg,
        "timeframe": "1h",
        "low_timeframe": "1h",
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
        "session_exit": False,
        "oos_cutoff": None,
        "target_fill": "tick_through",
        "intrabar": "path",
    }
    task.update(overrides)
    return task


def _strategy(signals, **hooks):
    return lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: signals, **hooks)


def _target_bars(highs):
    # Flat 100 bars; a 102 high reaches the 1R target of a long entered at 100 with stop 99.
    count = len(highs)
    return pd.DataFrame(
        {"open": [100.0] * count, "high": highs, "low": [100.0] * count, "close": [100.0] * count, "volume": [0] * count},
        index=pd.date_range("2025-01-02 08:00", periods=count, freq="1h"),
    )


def test_zero_trade_html_skips_writer_and_keeps_empty_chart_cell(
    monkeypatch, tmp_path, base_data, hourly_index, test_asset_cfg
):
    monkeypatch.setattr(runner, "write_trade_html", lambda *_args: pytest.fail("trade HTML writer should not be called"))
    monkeypatch.setattr(runner, "load_strategy", _strategy(pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])))

    row = runner.run_variants(_task(base_data, tmp_path, test_asset_cfg, variants=((1, "trailing"),)))[0]

    trade = decode_analytics(row["_analytics"])["trades"][0]
    assert trade["chart_path"] is None
    assert "<td>-</td>" in reports._trade_table([trade])


def test_trade_html_limit_charts_first_trades_and_keeps_all_trades(monkeypatch, tmp_path, test_asset_cfg):
    trades_dir = tmp_path / "trades"
    monkeypatch.setattr(reports, "TRADES_DIR", trades_dir)
    data = _target_bars([100.0, 102.0, 100.0, 102.0, 100.0, 102.0, 100.0])
    signals = pd.DataFrame([{"time": data.index[i], "side": "long", "stop": 99.0} for i in (0, 2, 4)])
    monkeypatch.setattr(runner, "load_strategy", _strategy(signals))

    row = runner.run_variants(_task(data, tmp_path, test_asset_cfg, variants=((1, "trailing"),), trade_html=2))[0]

    trades = decode_analytics(row["_analytics"])["trades"]
    assert row["Trades"] == 3
    assert [trade["chart_path"] is not None for trade in trades] == [True, True, False]
    assert len(list(trades_dir.glob("*.html"))) == 2


def test_run_variants_generates_signals_once_per_batch(monkeypatch, tmp_path, base_data, test_asset_cfg):
    generated = []
    monkeypatch.setattr(
        runner,
        "load_strategy",
        lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: generated.append(True) or pd.DataFrame()),
    )

    rows = runner.run_variants(_task(base_data, tmp_path, test_asset_cfg, variants=((1, "fixed"), (2, "trailing"))))

    assert generated == [True]
    assert [(row["RR"], row["Exit Mode"]) for row in rows] == [("1", "fixed"), ("2", "trailing")]


def test_run_variants_drops_signals_at_or_after_session_window_end(monkeypatch, tmp_path, test_asset_cfg):
    london_open = parse_sessions("london=08:00-10:00")[0]
    # Both signals would reach their target: the 10:00 one only trades if the window end is not exclusive.
    data = _target_bars([100.0, 102.0, 100.0, 102.0, 100.0])
    signals = pd.DataFrame(
        [
            {"time": pd.Timestamp("2025-01-02 08:00"), "side": "long", "stop": 99},
            {"time": pd.Timestamp("2025-01-02 10:00"), "side": "long", "stop": 99},
        ]
    )
    monkeypatch.setattr(runner, "load_strategy", _strategy(signals))

    row = runner.run_variants(_task(data, tmp_path, test_asset_cfg, session=london_open))[0]

    assert row["Session"] == "london=08:00-10:00"
    assert row["Trades"] == 1


def test_extra_signal_columns_reach_plot_indicators_and_trade_records(
    monkeypatch, tmp_path, base_data, hourly_index, test_asset_cfg
):
    monkeypatch.setattr(reports, "TRADES_DIR", tmp_path / "trades")
    plotted = []
    signal = {"time": hourly_index[0], "side": "long", "stop": 99, "vwap": 100.25, "target": 150.0, "note": None}
    monkeypatch.setattr(
        runner,
        "load_strategy",
        _strategy(
            pd.DataFrame([signal]),
            plot_indicators=lambda fig, data, view, asset, timeframe, params: plotted.append(dict(params["trade"])),
        ),
    )

    row = runner.run_variants(_task(base_data, tmp_path, test_asset_cfg, variants=((1, "trailing"),), trade_html=None))[0]

    (trade,) = plotted
    assert trade["vwap"] == 100.25
    # Engine-owned keys win over same-named signal columns; NA columns are not copied.
    assert trade["target"] == 101.0
    assert "note" not in trade
    stored = decode_analytics(row["_analytics"])["trades"][0]
    assert stored["vwap"] == 100.25
    assert reports._trade_table([stored]).count('<tr class="trade-row">') == 1


def test_run_variants_passes_the_fill_rules_to_the_backtest(monkeypatch, tmp_path, test_asset_cfg):
    # A long enters at 100 with stop 99; the 1R target is 101.
    touch_bar = (100.0, 101.0, 100.0, 100.5)  # touches the target
    both_bar = (100.8, 101.5, 98.5, 100.0)  # the path reaches the target first, then the stop
    data = pd.DataFrame(
        [(100.0, 100.0, 100.0, 100.0, 0), (100.0, 100.0, 100.0, 100.0, 0), (*touch_bar, 0), (*both_bar, 0)],
        index=pd.date_range("2025-01-02 08:00", periods=4, freq="1h"),
        columns=["open", "high", "low", "close", "volume"],
    )
    monkeypatch.setattr(runner, "load_strategy", _strategy(pd.DataFrame([{"time": data.index[0], "side": "long", "stop": 99.0}])))
    task = _task(data, tmp_path, test_asset_cfg)

    def exit_of(**rules):
        (trade,) = decode_analytics(runner.run_variants({**task, **rules})[0]["_analytics"])["trades"]
        return trade["exit_reason"], trade["exit_time"]

    assert exit_of(target_fill="touch") == ("target", data.index[2])
    assert exit_of() == ("target", data.index[3])
    assert exit_of(intrabar="stop_first") == ("stop", data.index[3])


def test_run_variants_session_exit_closes_trades_at_the_variant_session_window_end(monkeypatch, tmp_path, test_asset_cfg):
    london_open = parse_sessions("london=08:00-10:00")[0]
    # Entered at the 09:00 open; the 10:00 bar would trade through the 2R target but starts at the window end.
    data = _target_bars([100.0, 100.0, 102.1])
    monkeypatch.setattr(runner, "load_strategy", _strategy(pd.DataFrame([{"time": data.index[0], "side": "long", "stop": 99.0}])))
    task = _task(data, tmp_path, test_asset_cfg, variants=((2, "trailing"),), session=london_open)

    held, exited = (runner.run_variants({**task, "session_exit": flag})[0] for flag in (False, True))

    assert decode_analytics(held["_analytics"])["trades"][0]["exit_reason"] == "target"
    analytics = decode_analytics(exited["_analytics"])
    (trade,) = analytics["trades"]
    assert (trade["exit_reason"], trade["exit_time"]) == ("session", pd.Timestamp("2025-01-02 09:00"))
    assert analytics["managed"]["Session Completions"] == 1
    assert "100 @ +0.00R session" in reports._trade_table([trade])


def test_split_row_counts_in_sample_trades_by_entry_and_pairs_oos_metrics(monkeypatch, tmp_path, cost_asset_cfg):
    # Long trades enter at bars 1, 3 and 5 and reach their 1R target on the same bar; the cutoff is bar 3.
    data = _target_bars([100.0, 102.0, 100.0, 102.0, 100.0, 102.0])
    signals = pd.DataFrame([{"time": data.index[i], "side": "long", "stop": 99.0} for i in (0, 2, 4)])
    monkeypatch.setattr(runner, "load_strategy", _strategy(signals))

    row = runner.run_variants(_task(data, tmp_path, cost_asset_cfg, with_costs=True, oos_cutoff=3))[0]

    assert (row["Trades"], row["OOS Trades"], row["Spanning"]) == (1, 2, 0)
    assert row["W"] == (1, 1)
    for column, source in (("OOS Return", "Return"), ("OOS Sharpe", "Sharpe Ratio")):
        assert row[column] == (row["OOS Gross"][source], row["OOS Net"][source])
    assert row["Return"] == (row["Gross"]["Return"], row["Net"]["Return"])
    # Variant pages keep whole-period analytics.
    assert decode_analytics(row["_analytics"])["outcomes"][0]["Trades"] == 3


@pytest.mark.parametrize("exit_mode", ["fixed", "trailing"])
@pytest.mark.parametrize("oos_cutoff", [3, None])
def test_variant_page_trade_rows_show_period_by_entry_bar_only_in_split_mode(
    monkeypatch, tmp_path, test_asset_cfg, oos_cutoff, exit_mode
):
    # Longs enter at bars 1 and 4; the first reaches its target on bar 3, the cutoff, so it spans the split.
    data = _target_bars([100.0, 100.0, 100.0, 102.0, 100.0, 102.0])
    signals = pd.DataFrame([{"time": data.index[i], "side": "long", "stop": 99.0} for i in (0, 3)])
    monkeypatch.setattr(runner, "load_strategy", _strategy(signals))
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path / "results")
    task = _task(data, tmp_path, test_asset_cfg, variants=((1, exit_mode),), oos_cutoff=oos_cutoff)
    table = pd.DataFrame(runner.run_variants(task))
    config = RunConfig(("ema50",), "TEST", "1h", None, "1d", "local", "all", "1", exit_mode, "1", 10_000, False, None)

    page = (reports.write_results_html(table, config).parent / "variants" / "000000.html").read_text(encoding="utf-8")

    trade_table = page[page.index(f"<h2>{exit_mode.title()} trades</h2>"):]
    rows = re.findall(r'<tr class="trade-row">(.*?)</tr>', trade_table, re.S)
    assert len(rows) == 2
    if oos_cutoff is None:
        assert "sample" not in page.lower()
    else:
        assert "<th>Sample</th>" in trade_table
        assert ["in-sample" in row for row in rows] == [True, False]
        assert ["out-of-sample" in row for row in rows] == [False, True]


def test_run_variants_surfaces_strategy_errors_from_mid_batch(monkeypatch, tmp_path, base_data, test_asset_cfg):
    calls = []

    def calculate_metrics(*_args):
        calls.append(True)
        if len(calls) == 2:
            raise ZeroDivisionError("strategy raised on its second variant")
        return {}

    monkeypatch.setattr(runner, "load_strategy", _strategy(pd.DataFrame(), calculate_metrics=calculate_metrics))

    with pytest.raises(ZeroDivisionError, match="strategy raised on its second variant"):
        runner.run_variants(_task(base_data, tmp_path, test_asset_cfg, variants=((1, "fixed"), (2, "trailing"))))

    assert calls == [True, True]


def test_failing_runner_test_reports_its_assertion_instead_of_crashing(pytester):
    repo_root = Path(__file__).resolve().parents[1]
    pytester.makeconftest(f"import sys\nsys.path.insert(0, {str(repo_root)!r})\n")
    pytester.makepyfile(
        test_cached_runner_failure="""
        from types import SimpleNamespace

        import pandas as pd

        import tester_framework.runner as runner
        from tester_framework.models import AssetConfig
        from tester_framework.runner import cache_data


        def test_inner_failure_keeps_cached_data_alive(tmp_path, monkeypatch):
            index = pd.date_range("2025-01-02 08:00", periods=4, freq="1h")
            data = pd.DataFrame(
                {
                    "open": [100.0, 100.0, 100.0, 100.0],
                    "high": [100.0, 102.0, 100.0, 100.0],
                    "low": [100.0, 99.5, 100.0, 100.0],
                    "close": [100.0, 101.0, 100.0, 100.0],
                    "volume": [0, 0, 0, 0],
                },
                index=index,
            )
            captured = []

            def generate_signals(frame, **_kwargs):
                captured.append(frame)
                return pd.DataFrame()

            monkeypatch.setattr(
                runner,
                "load_strategy",
                lambda _name: SimpleNamespace(generate_signals=generate_signals),
            )
            asset_cfg = AssetConfig(
                ticker="TEST",
                point_value=1.0,
                tick_size=0.1,
                qty_step=1.0,
                min_qty=1.0,
                min_risk_ticks=1,
                spread_points=0.0,
                slippage_points=0.0,
                commission_per_side=0.0,
                session_timezone="UTC",
                session_start="00:00",
                bars_per_year={"1h": 1716, "1d": 252},
            )
            task = {
                "strategy": "ema50",
                "data_cache": cache_data(data, str(tmp_path), 0),
                "variants": ((1, "fixed"),),
                "asset": "TEST",
                "asset_cfg": asset_cfg,
                "timeframe": "1h",
                "low_timeframe": "1h",
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
                "session_exit": False,
                "oos_cutoff": None,
                "target_fill": "tick_through",
                "intrabar": "path",
            }

            rows = runner.run_variants(task)

            # The failure deliberately keeps `captured` referenced; pytest reads
            # this frame's locals while reporting the assertion below.
            assert captured[0].shape == (99, 5), "deliberate inner failure: cached data must stay readable"
            assert rows
        """
    )

    result = pytester.runpytest_subprocess("-q", "-l", "-p", "no:cacheprovider", timeout=60)

    assert result.ret == pytest.ExitCode.TESTS_FAILED, f"inner pytest crashed instead of failing: {result.ret}"
    result.stdout.fnmatch_lines(
        [
            "*AssertionError: deliberate inner failure: cached data must stay readable*",
            "*1 failed*",
        ]
    )


def test_run_variants_closes_trades_at_roll_on_cached_contract_data(monkeypatch, tmp_path, test_asset_cfg):
    # FUTH5 trades 50 points above FUTZ4, so the 11:00 roll bar would otherwise gap through the target.
    data = _target_bars([100.0, 100.0, 100.0, 150.0])
    data.iloc[3, :4] = 150.0
    data["contract"] = ["FUTZ4", "FUTZ4", "FUTZ4", "FUTH5"]
    monkeypatch.setattr(runner, "load_strategy", _strategy(pd.DataFrame([{"time": data.index[0], "side": "long", "stop": 99.0}])))

    row = runner.run_variants(_task(data, tmp_path, test_asset_cfg))[0]

    (trade,) = decode_analytics(row["_analytics"])["trades"]
    assert (trade["exit_reason"], trade["exit_time"]) == ("roll", pd.Timestamp("2025-01-02 10:00"))


def test_strategy_hooks_receive_the_asset_pip_size(monkeypatch, tmp_path, base_data, test_asset_cfg):
    received = []

    def generate_signals(data, asset, timeframe, params):
        received.append(params["pip_size"])
        return pd.DataFrame()

    def calculate_metrics(data, signals, trades, asset, timeframe, params):
        received.append(params["pip_size"])
        return {}

    monkeypatch.setattr(
        runner,
        "load_strategy",
        lambda _name: SimpleNamespace(generate_signals=generate_signals, calculate_metrics=calculate_metrics),
    )

    runner.run_variants(_task(base_data, tmp_path, replace(test_asset_cfg, pip_size=0.5)))

    assert received == [0.5, 0.5]
