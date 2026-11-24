from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import tester_framework.__main__ as cli
import tester_framework.reports as reports
import tester_framework.runner as runner
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


def test_variant_label_names_both_timeframes():
    task = {"strategy": "orb_candle", "asset": "MGC", "timeframe": "15m", "low_timeframe": "5m", "session": None, "params_label": "", "variants": ((1, "fixed"),), "stop_distances": (None,)}

    assert cli.variant_label(task) == "orb_candle MGC 15m 5m 1 variant"


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


def test_main_maps_high_timeframe_to_config(monkeypatch):
    configs = []
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "ema50", "--high_timeframe", "15m,1h"])
    monkeypatch.setattr(cli, "run", configs.append)
    cli.main()

    assert configs[0].high_timeframe == "15m,1h"


def test_main_maps_low_timeframe_to_config(monkeypatch):
    configs = []
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "orb_candle", "--low_timeframe", "1m,5m"])
    monkeypatch.setattr(cli, "run", configs.append)
    cli.main()

    assert configs[0].low_timeframe == "1m,5m"
    assert cli.parser().parse_args([]).low_timeframe is None


@pytest.mark.parametrize(
    ("strategy", "overrides", "message"),
    [
        (SimpleNamespace(), {"low_timeframe": "1h"}, "test does not declare LOW_TIMEFRAME"),
        (SimpleNamespace(LOW_TIMEFRAME="5m"), {"low_timeframe": "7m"}, "Unknown low timeframe 7m"),
        (
            SimpleNamespace(LOW_TIMEFRAME="5m"),
            {"low_timeframe": "1d"},
            "--low_timeframe: low timeframe 1d must not exceed high timeframe 1h",
        ),
        (
            SimpleNamespace(LOW_TIMEFRAME="5m"),
            {"high_timeframe": "5m", "low_timeframe": "10m"},
            "--low_timeframe: low timeframe 10m must not exceed high timeframe 5m",
        ),
        (
            SimpleNamespace(LOW_TIMEFRAME="5m"),
            {"high_timeframe": "5m", "low_timeframe": "2m"},
            "--low_timeframe: low timeframe 2m must divide high timeframe 5m evenly",
        ),
        (
            SimpleNamespace(LOW_TIMEFRAME="5m"),
            {"high_timeframe": "2m"},
            "test LOW_TIMEFRAME: low timeframe 5m must not exceed high timeframe 2m",
        ),
        (
            SimpleNamespace(LOW_TIMEFRAME="2m"),
            {"high_timeframe": "5m"},
            "test LOW_TIMEFRAME: low timeframe 2m must divide high timeframe 5m evenly",
        ),
        (SimpleNamespace(LOW_TIMEFRAME="5m"), {"low_timeframe": "15m"}, "Missing bars_per_year for TEST 15m"),
    ],
)
def test_run_rejects_invalid_low_timeframes_before_loading_data(monkeypatch, test_asset_cfg, strategy, overrides, message):
    test_asset_cfg = replace(test_asset_cfg, bars_per_year={**test_asset_cfg.bars_per_year, "2m": 1, "5m": 1, "10m": 1})
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=None, **vars(strategy)))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match=message):
        cli.run(_config(**overrides))


@pytest.mark.parametrize("overrides", [{"low_timeframe": "1h"}, {}])
def test_low_timeframe_equal_to_the_high_timeframe_is_a_single_timeframe_run(monkeypatch, test_asset_cfg, overrides):
    loaded = []
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(LOW_TIMEFRAME="1h", generate_signals=None))

    def load_data(_asset, _cfg, timeframe, *_args):
        loaded.append(timeframe)
        raise RuntimeError("stop after validation")

    monkeypatch.setattr(cli, "load_data", load_data)

    with pytest.raises(RuntimeError, match="stop after validation"):
        cli.run(_config(**overrides))

    assert loaded == ["1h"]


def test_timeframe_flag_is_rejected_with_a_message_naming_high_timeframe(capsys):
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--timeframe", "1h"])

    assert "--high_timeframe" in capsys.readouterr().err


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
                high_timeframe="1h",
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
        high_timeframe="1h",
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


def test_run_loads_low_timeframe_once_for_all_high_timeframes(
    monkeypatch, tmp_path, test_asset_cfg, base_data
):
    load_calls = []
    submitted = []
    test_asset_cfg = replace(test_asset_cfg, bars_per_year={"1h": 1716, "15m": 6864, "5m": 20592})

    def load_strategy(_name):
        return SimpleNamespace(LOW_TIMEFRAME="5m", generate_signals=lambda *_args, **_kwargs: pd.DataFrame())

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
            "High TF": task["timeframe"],
            "_sort_return": 0,
        }],
    )
    monkeypatch.setattr(cli, "write_results_html", lambda *_args: tmp_path / "results.html")

    cli.run(
        RunConfig(
            strategies=("one", "two"),
            asset="TEST",
            high_timeframe="1h,15m",
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

    assert load_calls == [("TEST", "5m")]
    assert [(task["strategy"], task["timeframe"], task["low_timeframe"]) for task in submitted] == [
        ("one", "1h", "5m"),
        ("one", "15m", "5m"),
        ("two", "1h", "5m"),
        ("two", "15m", "5m"),
    ]
    assert len({task["data_cache"] for task in submitted}) == 1
    assert all(task["variants"] == ((1.0, "fixed"),) for task in submitted)


def test_each_listed_low_timeframe_gets_its_own_variants_on_its_own_bars(monkeypatch, tmp_path, test_asset_cfg, base_data):
    submitted = []
    test_asset_cfg = replace(test_asset_cfg, bars_per_year={"1h": 1716, "1m": 411840, "5m": 82368})
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(LOW_TIMEFRAME="1m", generate_signals=None))
    monkeypatch.setattr(cli, "load_data", lambda _asset, _cfg, timeframe, *_args: base_data.assign(bars=timeframe))
    monkeypatch.setattr(cli, "cache_data", lambda data, _dir, cache_id: (data["bars"].iloc[0], cache_id))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(
        cli,
        "run_variants",
        lambda task: submitted.append(task) or [{"Strategy": task["strategy"], "Asset": task["asset"], "_sort_return": 0}],
    )
    monkeypatch.setattr(cli, "write_results_html", lambda *_args: tmp_path / "results.html")

    cli.run(_config(low_timeframe="1m,5m,1m", exit_mode="fixed,trailing", risk_reward_ratio="2"))

    assert [(task["timeframe"], task["low_timeframe"], task["data_cache"][0]) for task in submitted] == [
        ("1h", "1m", "1m"),
        ("1h", "5m", "5m"),
    ]
    assert all(len(task["variants"]) == 2 for task in submitted)


def test_run_waits_for_executor_before_temp_cleanup_on_failure(monkeypatch, tmp_path, test_asset_cfg, base_data):
    events = []
    strategy = SimpleNamespace(LOW_TIMEFRAME="1h", generate_signals=lambda *_args, **_kwargs: pd.DataFrame())

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
                high_timeframe="1h",
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


def test_run_removes_the_temporary_cache_folder_after_a_batch(monkeypatch, tmp_path, test_asset_cfg, base_data):
    folders = []
    cache_files = []
    cache_present_during_batch = []
    real_temporary_directory = cli.TemporaryDirectory
    real_cache_data = cli.cache_data

    class RecordingTempDir:
        def __init__(self, **kwargs):
            self._inner = real_temporary_directory(**kwargs)

        def __enter__(self):
            path = self._inner.__enter__()
            folders.append(Path(path))
            return path

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    def recording_cache_data(data, cache_dir, cache_id):
        cached = real_cache_data(data, cache_dir, cache_id)
        cache_files.append(Path(cached[0]))
        return cached

    def generate_signals(_frame, **_kwargs):
        cache_present_during_batch.append(cache_files[0].exists())
        return pd.DataFrame()

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

    strategy = SimpleNamespace(generate_signals=generate_signals)
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: strategy)
    monkeypatch.setattr(runner, "load_strategy", lambda _name: strategy)
    monkeypatch.setattr(cli, "load_data", lambda *_args: base_data)
    monkeypatch.setattr(cli, "cache_data", recording_cache_data)
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "TemporaryDirectory", RecordingTempDir)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "write_results_html", lambda *_args: tmp_path / "results.html")

    cli.run(_config())

    assert len(folders) == 1
    assert [path.parent for path in cache_files] == [folders[0]]
    assert cache_present_during_batch == [True]
    assert not folders[0].exists()


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
                high_timeframe="1h",
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
                high_timeframe="1h",
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


def _config(**overrides) -> RunConfig:
    values = dict(
        strategies=("test",),
        asset="TEST",
        high_timeframe="1h",
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
    values.update(overrides)
    return RunConfig(**values)


def test_run_rejects_risk_override_for_asset_not_in_run(monkeypatch, test_asset_cfg):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="--risk override for unknown asset: MCG"):
        cli.run(_config(risk="MCG=2"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"asset": "NOPE"}, "Unknown asset NOPE"),
        ({"high_timeframe": "7h"}, "Unknown timeframe 7h"),
        ({"capital": 0}, "--capital"),
        ({"capital": -1}, "--capital"),
        ({"capital": math.nan}, "--capital"),
        ({"max_trades": 0}, "--max_trades"),
        ({"target_fill": "near"}, "--target_fill"),
        ({"intrabar": "low_first"}, "--intrabar"),
        ({"exit_mode": "partial", "risk_reward_ratio": "1"}, "No exit modes are compatible"),
    ],
)
def test_run_rejects_invalid_config_before_loading_data(monkeypatch, test_asset_cfg, overrides, message):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match=message):
        cli.run(_config(**overrides))


def test_run_rejects_invalid_operation_before_clearing_outputs(monkeypatch, test_asset_cfg):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="--operation"):
        cli.run(_config(operation="longs"))


def test_run_keeps_previous_results_when_first_data_load_fails(monkeypatch, tmp_path, test_asset_cfg):
    results_dir = tmp_path / "results"
    previous = results_dir / "previous_bundle" / "index.html"
    previous.parent.mkdir(parents=True)
    previous.write_text("previous run")
    monkeypatch.setattr(reports, "ROOT", tmp_path)
    monkeypatch.setattr(reports, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(reports, "TRADES_DIR", tmp_path / "trades")
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))

    def failing_load(*_args):
        raise RuntimeError("download failed")

    monkeypatch.setattr(cli, "load_data", failing_load)

    with pytest.raises(RuntimeError, match="download failed"):
        cli.run(_config())

    assert previous.read_text() == "previous run"


def test_run_clears_outputs_once_after_first_data_load(monkeypatch, tmp_path, test_asset_cfg, base_data):
    events = []

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

    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg, "OTHER": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "load_data", lambda asset, *_args: events.append(f"load {asset}") or base_data)
    monkeypatch.setattr(cli, "cache_data", lambda *_args: ("values", "index", ()))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: events.append("reset"))
    monkeypatch.setattr(cli, "ProcessPoolExecutor", Executor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "run_variants", lambda _task: [])
    monkeypatch.setattr(cli, "write_results_html", lambda *_args: tmp_path / "results.html")

    cli.run(_config(asset="TEST,OTHER"))

    assert events == ["load TEST", "reset", "load OTHER"]


@pytest.mark.parametrize("sessions", [None, "none"])
def test_run_rejects_session_exit_without_session_window(monkeypatch, test_asset_cfg, sessions):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="--session_exit requires --sessions"):
        cli.run(_config(sessions=sessions, session_exit=True))


def test_session_exit_flag_defaults_off_and_maps_to_config(monkeypatch):
    assert cli.parser().parse_args([]).session_exit is False
    configs = []
    argv = ["tester_framework", "--strategies", "orb_candle", "--sessions", "ny_bank", "--session_exit"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "run", configs.append)
    cli.main()

    assert configs[0].session_exit is True


def test_sessions_help_lists_no_break():
    help_text = " ".join(cli.parser().format_help().split())

    assert "full, no_break, regions" in help_text


def test_fill_rule_flags_default_to_tick_through_and_path_and_map_to_config(monkeypatch):
    args = cli.parser().parse_args([])
    assert (args.target_fill, args.intrabar) == ("tick_through", "path")
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--target_fill", "near"])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--intrabar", "low_first"])
    configs = []
    argv = ["tester_framework", "--strategies", "ema50", "--target_fill", "touch", "--intrabar", "stop_first"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "run", configs.append)
    cli.main()

    assert (configs[0].target_fill, configs[0].intrabar) == ("touch", "stop_first")


def test_split_flags_default_off_and_map_to_config(monkeypatch):
    args = cli.parser().parse_args([])
    assert (args.oos_start, args.oos_fraction) == (None, None)
    configs = []
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "ema50", "--oos_fraction", "0.25"])
    monkeypatch.setattr(cli, "run", configs.append)
    cli.main()
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "ema50", "--oos_start", "2021-07-01"])
    cli.main()

    assert (configs[0].oos_start, configs[0].oos_fraction) == (None, 0.25)
    assert (configs[1].oos_start, configs[1].oos_fraction) == ("2021-07-01", None)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"oos_start": "2025-01-02", "oos_fraction": 0.5}, "Use --oos_start or --oos_fraction, not both"),
        ({"oos_fraction": 0.0}, "--oos_fraction must be between 0 and 1"),
        ({"oos_fraction": 1.0}, "--oos_fraction must be between 0 and 1"),
        ({"oos_fraction": -0.5}, "--oos_fraction must be between 0 and 1"),
        ({"oos_start": "2025-13-01"}, "--oos_start must be a YYYY-MM-DD date"),
        ({"oos_fraction": 0.5, "max_trades": 3}, "--max_trades cannot be combined with a split"),
    ],
)
def test_run_rejects_invalid_split_flags_before_loading_data(monkeypatch, test_asset_cfg, overrides, message):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match=message):
        cli.run(_config(**overrides))


def test_main_reports_conflicting_split_flags_clearly(monkeypatch):
    argv = ["tester_framework", "--strategies", "ema50", "--oos_start", "2021-07-01", "--oos_fraction", "0.3"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="Use --oos_start or --oos_fraction, not both"):
        cli.main()


class _Done:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value


class _InlineExecutor:
    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def submit(self, function, task):
        return _Done(function(task))


def _two_day_data():
    index = pd.date_range("2025-01-01 00:00", periods=48, freq="h")
    return pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0}, index=index)


def _split_run(monkeypatch, tmp_path, asset_cfg, data, **overrides):
    submitted = []
    written = []
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: pd.DataFrame()))
    monkeypatch.setattr(runner, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: pd.DataFrame()))
    monkeypatch.setattr(cli, "load_data", lambda *_args: data)
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "run_variants", lambda task: submitted.append(task) or runner.run_variants(task))
    monkeypatch.setattr(
        cli, "write_results_html", lambda *args: written.append((args,)) or tmp_path / "results.html"
    )
    cli.run(_config(**overrides))
    return submitted, written


def test_split_run_sends_cutoff_to_workers_and_shows_split_columns_and_ranges(monkeypatch, tmp_path, capsys, test_asset_cfg):
    submitted, written = _split_run(monkeypatch, tmp_path, test_asset_cfg, _two_day_data(), oos_start="2025-01-02")

    assert [task["oos_cutoff"] for task in submitted] == [24]
    (args,) = written[0]
    assert args[2][-10:] == [
        "OOS Trades", "OOS Return", "OOS Max DD", "OOS Sharpe", "OOS Return / DD", "IS Rank", "OOS Rank", "WFE", "Spanning", "Selected"
    ]
    (split,) = args[3]
    assert (split.asset, split.low_timeframe, split.cutoff, split.bar_count) == ("TEST", "1h", 24, 48)
    out = capsys.readouterr().out
    assert "TEST 1h in-sample 2025-01-01 00:00 to 2025-01-01 23:00 UTC" in out
    assert "TEST 1h out-of-sample 2025-01-02 00:00 to 2025-01-02 23:00 UTC" in out
    assert "OOS Return" in out and "Spanning" in out


def test_run_without_split_flags_sends_no_cutoff_and_shows_no_split_columns(monkeypatch, tmp_path, capsys, test_asset_cfg):
    submitted, written = _split_run(monkeypatch, tmp_path, test_asset_cfg, _two_day_data())

    assert [task["oos_cutoff"] for task in submitted] == [None]
    (args,) = written[0]
    assert "OOS Return" not in args[2]
    assert args[3] == []
    out = capsys.readouterr().out
    assert "OOS" not in out and "in-sample" not in out


@pytest.mark.parametrize(
    ("overrides", "header"),
    [
        ({}, None),
        ({"target_fill": "touch"}, "target fill touch"),
        ({"intrabar": "stop_first"}, "intrabar stop first"),
        ({"target_fill": "touch", "intrabar": "stop_first"}, "target fill touch | intrabar stop first"),
    ],
)
def test_run_prints_non_default_fill_rules_in_the_console_header(monkeypatch, tmp_path, capsys, test_asset_cfg, overrides, header):
    _split_run(monkeypatch, tmp_path, test_asset_cfg, _two_day_data(), **overrides)

    first_line = capsys.readouterr().out.splitlines()[0]
    if header is None:
        assert "target fill" not in first_line and "intrabar" not in first_line
    else:
        assert first_line == header


def test_run_rejects_a_cutoff_that_empties_a_period_with_the_asset_named(monkeypatch, tmp_path, test_asset_cfg):
    with pytest.raises(ValueError, match="TEST 1h: Split cutoff leaves the out-of-sample period with no bars"):
        _split_run(monkeypatch, tmp_path, test_asset_cfg, _two_day_data(), oos_start="2025-01-03")


def test_selection_flags_default_off_and_map_to_config(monkeypatch):
    args = cli.parser().parse_args(["--strategies", "ema50"])
    assert (args.select_by, args.select_top) == (None, None)
    configs = []
    monkeypatch.setattr(cli, "run", configs.append)
    argv = ["tester_framework", "--strategies", "ema50", "--oos_start", "2021-07-01", "--select_by", "sharpe", "--select_top", "3"]
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert (configs[0].select_by, configs[0].select_top) == ("sharpe", 3)
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["--strategies", "ema50", "--select_by", "profit"])


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"select_by": "sharpe"}, "--select_by and --select_top need --oos_start or --oos_fraction"),
        ({"select_top": 3}, "--select_by and --select_top need --oos_start or --oos_fraction"),
        ({"oos_fraction": 0.5, "select_top": 0}, "--select_top must be a positive integer"),
        ({"oos_fraction": 0.5, "select_top": -2}, "--select_top must be a positive integer"),
    ],
)
def test_run_rejects_selection_flags_without_a_split_or_with_non_positive_top(monkeypatch, test_asset_cfg, overrides, message):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match=message):
        cli.run(_config(**overrides))


def _ranked_rows(monkeypatch, returns):
    def fake_run_variants(task):
        rows = []
        for (rr, mode), (ret, ret_dd, oos) in zip(task["variants"], returns):
            rows.append({
                "Strategy": task["strategy"], "Asset": task["asset"], "High TF": task["timeframe"], "RR": f"{rr:g}", "Exit Mode": mode,
                "Return": ret, "Max DD": 1.0, "Sharpe Ratio": ret, "Return / DD": ret_dd, "_sort_return": ret,
                "OOS Trades": 1, "OOS Return": oos, "OOS Max DD": 1.0, "OOS Sharpe": oos, "OOS Return / DD": oos, "WFE": 50.0,
            })
        return rows

    monkeypatch.setattr(cli, "run_variants", fake_run_variants)


def test_split_run_ranks_selects_and_sorts_variants_by_in_sample_rank(monkeypatch, tmp_path, capsys, test_asset_cfg):
    written = []
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(cli, "load_data", lambda *_args: _two_day_data())
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "write_results_html", lambda *args: written.append(args) or tmp_path / "results.html")
    _ranked_rows(monkeypatch, [(3.0, 1.0, 2.0), (1.0, 3.0, 1.0), (2.0, 2.0, 3.0)])

    cli.run(_config(risk_reward_ratio="1,2,3", oos_start="2025-01-02", select_top=2))

    table, _config_arg, columns, _splits = written[0]
    assert columns[-5:] == ["IS Rank", "OOS Rank", "WFE", "Spanning", "Selected"]
    assert list(table["RR"]) == ["2", "3", "1"]
    assert list(table["IS Rank"]) == [1, 2, 3]
    assert list(table["OOS Rank"]) == [3, 1, 2]
    assert list(table["Selected"]) == [True, True, False]
    out = capsys.readouterr().out
    assert "IS Rank" in out and "WFE" in out and "50.00%" in out
    assert "test TEST: Tried 3, OOS profitable 3/3 (100.0%), Equal-weight OOS +2.00%" in out


def test_split_run_ranks_within_each_strategy_and_asset_with_nan_last_and_marks_the_top_n(
    monkeypatch, tmp_path, capsys, test_asset_cfg
):
    values = {
        ("a", "TEST"): [(1.0, 2.0), (math.nan, 9.0), (3.0, math.nan)],
        ("a", "OTHER"): [(5.0, 1.0), (4.0, 3.0), (6.0, 2.0)],
        ("b", "TEST"): [(0.5, 0.0), (2.0, -1.0), (math.nan, 1.0)],
    }

    def fake_run_variants(task):
        return [
            {
                "Strategy": task["strategy"], "Asset": task["asset"], "High TF": task["timeframe"], "RR": f"{rr:g}", "Exit Mode": mode,
                "Return": 1.0, "Max DD": 1.0, "Sharpe Ratio": 1.0, "Return / DD": ret_dd, "_sort_return": 1.0,
                "OOS Trades": 1, "OOS Return": 1.0, "OOS Max DD": 1.0, "OOS Sharpe": 1.0, "OOS Return / DD": oos, "WFE": 10.0 * rr,
            }
            for (rr, mode), (ret_dd, oos) in zip(task["variants"], values.get((task["strategy"], task["asset"]), []))
        ]

    written = []
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg, "OTHER": replace(test_asset_cfg, ticker="OTHER")})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(cli, "load_data", lambda *_args: _two_day_data())
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "write_results_html", lambda *args: written.append(args) or tmp_path / "results.html")
    monkeypatch.setattr(cli, "run_variants", fake_run_variants)

    cli.run(_config(strategies=("a", "b"), asset="TEST,OTHER", risk_reward_ratio="1,2,3", oos_fraction=0.5, select_top=2))

    table, config, columns, _splits = written[0]
    assert (config.select_by, config.select_top) == ("return_dd", 2)
    assert list(zip(table["Strategy"], table["Asset"], table["RR"], table["IS Rank"], table["OOS Rank"], table["Selected"])) == [
        ("a", "TEST", "3", 1, 3, True),
        ("a", "TEST", "1", 2, 2, True),
        ("a", "TEST", "2", 3, 1, False),
        ("a", "OTHER", "3", 1, 2, True),
        ("a", "OTHER", "1", 2, 3, True),
        ("a", "OTHER", "2", 3, 1, False),
        ("b", "TEST", "2", 1, 3, True),
        ("b", "TEST", "1", 2, 2, True),
        ("b", "TEST", "3", 3, 1, False),
    ]
    assert list(table["WFE"]) == [30.0, 10.0, 20.0, 30.0, 10.0, 20.0, 20.0, 10.0, 30.0]
    assert "WFE" in columns
    out = capsys.readouterr().out
    assert "30.00%" in out


def test_run_without_split_keeps_return_sort_and_adds_no_rank_columns(monkeypatch, tmp_path, test_asset_cfg):
    written = []
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(cli, "load_data", lambda *_args: _two_day_data())
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "write_results_html", lambda *args: written.append(args) or tmp_path / "results.html")
    _ranked_rows(monkeypatch, [(3.0, 1.0, 2.0), (1.0, 3.0, 1.0), (2.0, 2.0, 3.0)])

    cli.run(_config(risk_reward_ratio="1,2,3"))

    table, _config_arg, columns, _splits = written[0]
    assert list(table["RR"]) == ["1", "3", "2"]
    assert "IS Rank" not in table and "IS Rank" not in columns and "WFE" not in columns


def test_stop_distance_flag_defaults_off_and_maps_to_config(monkeypatch):
    assert cli.parser().parse_args([]).stop_distance is None
    configs = []
    monkeypatch.setattr(cli, "run", configs.append)
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "ema50", "--stop_distance", "0.1-0.5%,5-20pips"])
    cli.main()

    assert configs[0].stop_distance == "0.1-0.5%,5-20pips"


def _stop_distance_run(monkeypatch, tmp_path, asset_cfg, asset_names=("TEST",), **overrides):
    generated, submitted, written = [], [], []
    data = _two_day_data()
    signals = pd.DataFrame([{"time": data.index[0], "side": "long", "stop": 99.5}])
    strategy = SimpleNamespace(generate_signals=lambda *_args, **_kwargs: generated.append(True) or signals)
    monkeypatch.setattr(cli, "load_assets", lambda: {name: replace(asset_cfg, ticker=name) for name in asset_names})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: strategy)
    monkeypatch.setattr(runner, "load_strategy", lambda _name: strategy)
    monkeypatch.setattr(cli, "load_data", lambda *_args: data)
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "run_variants", lambda task: submitted.append(task) or runner.run_variants(task))
    monkeypatch.setattr(cli, "write_results_html", lambda *args: written.append(args) or tmp_path / "results.html")
    cli.run(_config(trade_html=0, **overrides))
    return generated, submitted, written[0]


def test_each_stop_distance_range_is_its_own_variant_sharing_one_signal_generation(monkeypatch, tmp_path, capsys, test_asset_cfg):
    generated, submitted, (table, _config_arg, columns, _splits) = _stop_distance_run(
        monkeypatch, tmp_path, test_asset_cfg, risk_reward_ratio="1,2", stop_distance="0.1-0.5%,0.6%+"
    )

    assert generated == [True]
    assert [len(task["variants"]) * len(task["stop_distances"]) for task in submitted] == [4]
    assert columns.index("Stop Distance") == columns.index("Exit Mode") + 1
    assert sorted(zip(table["RR"], table["Stop Distance"], table["Trades"], table["Discarded"])) == [
        ("1", "0.1-0.5%", 1, 0),
        ("1", "0.6%+", 0, 1),
        ("2", "0.1-0.5%", 1, 0),
        ("2", "0.6%+", 0, 1),
    ]
    captured = capsys.readouterr()
    assert "4 variants" in captured.err
    assert "Stop Distance" in captured.out


def test_run_without_stop_distance_has_no_limits_and_no_stop_distance_column(monkeypatch, tmp_path, capsys, test_asset_cfg):
    _generated, submitted, (table, _config_arg, columns, _splits) = _stop_distance_run(monkeypatch, tmp_path, test_asset_cfg)

    assert [task["stop_distances"] for task in submitted] == [(None,)]
    assert "Stop Distance" not in columns
    assert list(table["Trades"]) == [1]
    assert "Stop Distance" not in capsys.readouterr().out


def test_stop_distance_values_listed_twice_run_once(monkeypatch, tmp_path, test_asset_cfg):
    _generated, submitted, _written = _stop_distance_run(monkeypatch, tmp_path, test_asset_cfg, stop_distance="0.1-0.5%,0.10-0.50%")

    assert [tuple(map(str, task["stop_distances"])) for task in submitted] == [("0.1-0.5%",)]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("0.1-0.5", id="missing-unit"),
        pytest.param("0.1-0.5pts", id="unknown-unit"),
        pytest.param("0.1%", id="no-maximum-and-no-plus"),
        pytest.param("a-0.5%", id="not-a-number"),
        pytest.param("0.1-0.5-0.6%", id="three-bounds"),
        pytest.param("-0.1-0.5%", id="negative-minimum"),
        pytest.param("-0.1%+", id="negative-minimum-only"),
        pytest.param("0.5-0.1%", id="minimum-above-maximum"),
        pytest.param("nan-0.5%", id="not-finite"),
        pytest.param("0.1-0.5%,5pips", id="one-bad-range-in-a-list"),
    ],
)
def test_run_rejects_a_bad_stop_distance_before_loading_data(monkeypatch, test_asset_cfg, value):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="--stop_distance"):
        cli.run(_config(stop_distance=value))


def test_stop_distance_ranges_count_against_max_variants(monkeypatch, test_asset_cfg):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="6 variants exceed --max_variants 5"):
        cli.run(_config(risk_reward_ratio="1,2", stop_distance="0.1-0.5%,0.2-0.6%,5-20pips", max_variants=5))


def test_split_run_ranks_stop_distance_variants_with_the_other_variants_of_their_strategy_and_asset(
    monkeypatch, tmp_path, test_asset_cfg
):
    _generated, _submitted, (table, _config_arg, _columns, _splits) = _stop_distance_run(
        monkeypatch, tmp_path, test_asset_cfg, stop_distance="0.1-0.5%,0.6%+", oos_start="2025-01-02"
    )

    assert sorted(table["Stop Distance"]) == ["0.1-0.5%", "0.6%+"]
    assert sorted(table["IS Rank"]) == [1, 2]


def test_variant_label_counts_stop_distance_variants():
    task = {
        "strategy": "orb_candle", "asset": "MGC", "timeframe": "15m", "low_timeframe": "5m", "session": None, "params_label": "",
        "variants": ((1, "fixed"), (2, "fixed")), "stop_distances": (None, None, None),
    }

    assert cli.variant_label(task) == "orb_candle MGC 15m 5m 6 variants"


def test_each_asset_runs_its_tagged_stop_distances_or_else_the_untagged_ones(monkeypatch, tmp_path, capsys, test_asset_cfg):
    _generated, submitted, (table, _config_arg, _columns, _splits) = _stop_distance_run(
        monkeypatch, tmp_path, test_asset_cfg, asset_names=("MGC", "EURUSD", "MNQ"), asset="MGC,EURUSD,MNQ",
        stop_distance="MGC=0.1-0.5%,mgc=0.2-0.6%,EURUSD=5-20pips,0.1-0.4%",
    )

    assert {task["asset"]: tuple(map(str, task["stop_distances"])) for task in submitted} == {
        "MGC": ("0.1-0.5%", "0.2-0.6%"),
        "EURUSD": ("5-20pips",),
        "MNQ": ("0.1-0.4%",),
    }
    assert sorted(zip(table["Asset"], table["Stop Distance"])) == [
        ("EURUSD", "5-20pips"), ("MGC", "0.1-0.5%"), ("MGC", "0.2-0.6%"), ("MNQ", "0.1-0.4%"),
    ]
    assert "4 variants" in capsys.readouterr().err


def test_an_asset_without_a_tagged_or_untagged_stop_distance_runs_without_limits(monkeypatch, tmp_path, test_asset_cfg):
    _generated, submitted, (table, _config_arg, columns, _splits) = _stop_distance_run(
        monkeypatch, tmp_path, test_asset_cfg, asset_names=("MGC", "EURUSD"), asset="MGC,EURUSD", stop_distance="MGC=0.6%+"
    )

    assert {task["asset"]: task["stop_distances"] for task in submitted}["EURUSD"] == (None,)
    assert "Stop Distance" in columns
    assert sorted(zip(table["Asset"], table["Stop Distance"], table["Trades"])) == [("EURUSD", "", 1), ("MGC", "0.6%+", 0)]


def test_run_rejects_a_stop_distance_tag_for_an_asset_not_in_the_run_before_loading_data(monkeypatch, test_asset_cfg):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": test_asset_cfg, "MGC": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: pytest.fail("outputs must not be cleared"))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="--stop_distance tag for unknown asset: MGC"):
        cli.run(_config(stop_distance="0.1-0.5%,mgc=0.1-0.5%"))


def test_a_stop_distance_listed_twice_for_an_asset_runs_once(monkeypatch, tmp_path, test_asset_cfg):
    _generated, submitted, _written = _stop_distance_run(
        monkeypatch, tmp_path, test_asset_cfg, stop_distance="TEST=0.1-0.5%,test=0.10-0.50%,0.2%+,0.2%+"
    )

    assert [tuple(map(str, task["stop_distances"])) for task in submitted] == [("0.1-0.5%",)]


def test_each_asset_counts_its_own_stop_distances_against_max_variants(monkeypatch, test_asset_cfg):
    monkeypatch.setattr(cli, "load_assets", lambda: {"MGC": test_asset_cfg, "EURUSD": test_asset_cfg, "MNQ": test_asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args: None))
    monkeypatch.setattr(cli, "load_data", lambda *_args: pytest.fail("load_data should not be called"))

    with pytest.raises(ValueError, match="8 variants exceed --max_variants 7"):
        cli.run(_config(
            asset="MGC,EURUSD,MNQ", risk_reward_ratio="1,2", stop_distance="MGC=0.1-0.5%,MGC=0.2-0.6%,EURUSD=5-20pips,0.1-0.4%",
            max_variants=7,
        ))
