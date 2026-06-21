from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

import tester_framework.__main__ as cli
from tester_framework.__main__ import EXIT_MODES, exit_mode_variants, status_lines, worker_count
from tester_framework.cli import RunConfig


def test_exit_mode_variants():
    assert exit_mode_variants("fixed,trailing", 2) == ["fixed", "trailing"]
    assert exit_mode_variants("all", 1) == ["fixed"]
    assert exit_mode_variants("all", 2) == list(EXIT_MODES)
    assert exit_mode_variants("partial", 1.5) == []
    assert exit_mode_variants("trailing", 1) == []
    assert exit_mode_variants("fixed,fixed", 2) == ["fixed"]
    with pytest.raises(ValueError):
        exit_mode_variants("fixed,invalid", 2)


def test_worker_count():
    assert worker_count(1, 3) == 1
    assert worker_count(None, 1) == 1
    with pytest.raises(ValueError):
        worker_count(0, 1)


def test_status_lines():
    lines = status_lines(
        "Backtesting",
        "2 workers",
        1,
        3,
        started=0.0,
        workers=[("MGC 1h 2R fixed", 10.0), None],
        now=75.0,
    )
    assert lines == [
        "Backtesting | 1/3 complete | elapsed 1m 15s",
        "2 workers",
        "worker 1: MGC 1h 2R fixed | 1m 5s",
        "worker 2: idle waiting",
    ]


def test_main_maps_new_cli_flags(monkeypatch):
    configs = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tester_framework",
            "--strategies",
            "ema50,orb_candle",
            "--sessions",
            "ny=09:30-12:00",
            "--exit_mode",
            "trailing",
            "--max_trades",
            "3",
            "--trade_html",
            "2",
            "--days",
            "Monday,wed,MON",
            "--months",
            "January,sep,JAN",
        ],
    )
    monkeypatch.setattr(cli, "run", configs.append)
    cli.main()

    assert configs[0].strategies == ("ema50", "orb_candle")
    assert configs[0].sessions == "ny=09:30-12:00"
    assert configs[0].exit_mode == "trailing"
    assert configs[0].max_trades == 3
    assert configs[0].trade_html == 2
    assert configs[0].days == (0, 2)
    assert configs[0].months == (1, 9)


def test_calendar_filters_default_empty_and_reject_unknown_values():
    args = cli.parser().parse_args([])
    assert args.days == ()
    assert args.months == ()
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--days", "funday"])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--months", "smarch"])


def test_time_period_accepts_calendar_years_and_rejects_bad_ranges():
    assert cli.parser().parse_args(["--time_period", "2021"]).time_period == "2021"
    assert cli.parser().parse_args(["--time_period", "2020-2021"]).time_period == "2020-2021"
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--time_period", "2021-2020"])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--time_period", "2021-01-01"])


def test_trade_html_defaults_to_unlimited_and_accepts_zero():
    assert cli.parser().parse_args([]).trade_html is None
    assert cli.parser().parse_args(["--trade_html", "0"]).trade_html == 0


def test_trade_html_rejects_negative_count():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--trade_html", "-1"])


@pytest.mark.parametrize("flag", ["--strategy", "--exit-mode", "--no_trade_html"])
def test_old_cli_flags_are_rejected(flag):
    with pytest.raises(SystemExit):
        cli.parser().parse_args([flag, "ema50"])


def test_run_rejects_negative_trade_html(monkeypatch, test_asset_cfg):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="--trade_html"):
        cli.run(
            RunConfig(
                strategies=("test",),
                asset="TEST",
                timeframe="1h",
                sessions=None,
                time_period="1d",
                data_source="local",
                operation="all",
                risk_reward_ratio="1",
                exit_mode="fixed",
                risk="1",
                capital=10_000,
                with_costs=False,
                workers=1,
                trade_html=-1,
            )
        )


@pytest.mark.parametrize(("field", "value"), [("days", (7,)), ("months", (0,))])
def test_run_rejects_invalid_calendar_filters(monkeypatch, test_asset_cfg, field, value):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    config = RunConfig(
        strategies=("test",),
        asset="TEST",
        timeframe="1h",
        sessions=None,
        time_period="1d",
        data_source="local",
        operation="all",
        risk_reward_ratio="1",
        exit_mode="fixed",
        risk="1",
        capital=10_000,
        with_costs=False,
        workers=1,
        **{field: value},
    )

    with pytest.raises(ValueError, match=f"--{field}"):
        cli.run(config)


def test_run_reuses_data_for_strategies_with_same_execution_timeframe(
    monkeypatch, tmp_path, test_asset_cfg, base_data
):
    load_calls = []
    data_ids = []
    submitted = []

    def load_strategy(_name):
        def generate_signals(data, **_kwargs):
            data_ids.append(id(data))
            return pd.DataFrame()

        return SimpleNamespace(EXECUTION_TIMEFRAME="1h", generate_signals=generate_signals)

    def load_data(asset, _asset_cfg, timeframe, _period, _source):
        load_calls.append((asset, timeframe))
        return base_data

    class Future:
        def __init__(self, value):
            self.value = value

        def result(self):
            return self.value

    class Executor:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def submit(self, function, task):
            return Future(function(task))

        def shutdown(self, **_kwargs):
            pass

    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", load_strategy)
    monkeypatch.setattr(cli, "load_data", load_data)
    monkeypatch.setattr(cli, "cache_data", lambda *_args: ("values", "index", ()))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(
        cli,
        "run_variants",
        lambda task: submitted.append(task) or [{
            "Strategy": task["strategy"],
            "Asset": task["asset"],
            "TF": task["timeframe"],
            "_sort_return": 0,
        }],
    )
    monkeypatch.setattr(cli, "write_results_html", lambda *_args: tmp_path / "results.html")

    cli.run(
        RunConfig(
            strategies=("one", "two"),
            asset="TEST",
            timeframe="1h",
            sessions=None,
            time_period="1d",
            data_source="local",
            operation="all",
            risk_reward_ratio="1",
            exit_mode="fixed",
            risk="1",
            capital=10_000,
            with_costs=False,
            workers=1,
        )
    )

    assert load_calls == [("TEST", "1h")]
    assert data_ids == []
    assert len(submitted) == 2
    assert all(task["variants"] == ((1.0, "fixed"),) for task in submitted)


def test_run_waits_for_executor_before_temp_cleanup_on_failure(monkeypatch, tmp_path, test_asset_cfg, base_data):
    events = []
    strategy = SimpleNamespace(EXECUTION_TIMEFRAME="1h", generate_signals=lambda *_args, **_kwargs: pd.DataFrame())

    class TempDir:
        def __enter__(self):
            return str(tmp_path)

        def __exit__(self, *_args):
            events.append("temp cleanup")

    class Future:
        def result(self):
            raise RuntimeError("worker failed")

        def cancel(self):
            return False

    class Executor:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            events.append("executor waited")

        def submit(self, *_args):
            return Future()

        def shutdown(self, **_kwargs):
            pytest.fail("run must let the executor context perform the waiting shutdown")

    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: strategy)
    monkeypatch.setattr(cli, "load_data", lambda *_args: base_data)
    monkeypatch.setattr(cli, "cache_data", lambda *_args: ("values", "index", ()))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "TemporaryDirectory", lambda **_kwargs: TempDir())
    monkeypatch.setattr(cli, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))

    with pytest.raises(RuntimeError, match="worker failed"):
        cli.run(
            RunConfig(
                strategies=("test",),
                asset="TEST",
                timeframe="1h",
                sessions=None,
                time_period="1d",
                data_source="local",
                operation="all",
                risk_reward_ratio="1",
                exit_mode="fixed",
                risk="1",
                capital=10_000,
                with_costs=False,
                workers=1,
            )
        )

    assert events == ["executor waited", "temp cleanup"]


def test_run_requires_strategy_owned_sessions(monkeypatch, test_asset_cfg):
    strategy = SimpleNamespace(
        generate_signals=lambda *_args, **_kwargs: pd.DataFrame(),
        REQUIRED_FLAGS={"sessions": "ORB strategies require --sessions; none is not supported."},
    )
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: strategy)
    monkeypatch.setattr(cli, "load_data", lambda *_args, **_kwargs: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="ORB strategies require --sessions; none is not supported."):
        cli.run(
            RunConfig(
                strategies=("orb_candle",),
                asset="TEST",
                timeframe="1h",
                sessions=None,
                time_period="1d",
                data_source="local",
                operation="all",
                risk_reward_ratio="1",
                exit_mode="fixed",
                risk="1",
                capital=10_000,
                with_costs=False,
                workers=1,
            )
        )


def test_run_rejects_unknown_required_flag(monkeypatch, test_asset_cfg):
    strategy = SimpleNamespace(generate_signals=lambda *_args, **_kwargs: pd.DataFrame(), REQUIRED_FLAGS={"bogus": "x"})
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: strategy)

    with pytest.raises(ValueError, match="unknown required flag"):
        cli.run(
            RunConfig(
                strategies=("test",),
                asset="TEST",
                timeframe="1h",
                sessions=None,
                time_period="1d",
                data_source="local",
                operation="all",
                risk_reward_ratio="1",
                exit_mode="fixed",
                risk="1",
                capital=10_000,
                with_costs=False,
                workers=1,
            )
        )
