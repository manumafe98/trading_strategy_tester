from __future__ import annotations

import re
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

import tester_framework.__main__ as cli
import tester_framework.reports as reports
import tester_framework.runner as runner
import tester_framework.strategy_loader as strategy_loader
from tester_framework.analytics import decode_analytics
from tester_framework.strategy_params import RESERVED_PARAMS
from tests.test_cli import _InlineExecutor, _config, _two_day_data
from tests.test_runner import _task


def _recording_strategy(params, param_types=None, **hooks):
    seen = []

    def generate_signals(df, asset, timeframe, params):
        seen.append(dict(params))
        return pd.DataFrame()

    strategy = SimpleNamespace(generate_signals=generate_signals, PARAMS=params, PARAM_TYPES=param_types or {}, **hooks)
    return strategy, seen


def _run(monkeypatch, tmp_path, asset_cfg, strategies, tables=None, **overrides):
    """Runs cli.run inline; `strategies` maps strategy name to its module stand-in."""
    submitted = []
    tables = [] if tables is None else tables
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", strategies.__getitem__)
    monkeypatch.setattr(runner, "load_strategy", strategies.__getitem__)
    monkeypatch.setattr(cli, "load_data", lambda *_args: _two_day_data())
    monkeypatch.setattr(cli, "reset_output_dirs", lambda: None)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(cli, "wait", lambda futures, **_kwargs: (set(futures), set()))
    monkeypatch.setattr(cli, "run_variants", lambda task: submitted.append(task) or runner.run_variants(task))
    monkeypatch.setattr(cli, "write_results_html", lambda table, *_args: tables.append(table) or tmp_path / "results.html")
    cli.run(_config(strategies=tuple(strategies), **overrides))
    return submitted


def test_overrides_arrive_typed_like_their_defaults_with_other_defaults_filled_in(monkeypatch, tmp_path, test_asset_cfg):
    strategy, seen = _recording_strategy(
        {"swing_length": 20, "width": 0.4, "retest": True, "mode": "close", "limit": None, "note": None},
        {"limit": int},
    )

    _run(
        monkeypatch,
        tmp_path,
        test_asset_cfg,
        {"test": strategy},
        strategy_params="swing_length=10; width=1.5;retest=false;mode=break;limit=30",
    )

    (params,) = seen
    assert params == {
        "swing_length": 10,
        "width": 1.5,
        "retest": False,
        "mode": "break",
        "limit": 30,
        "note": None,
        "tick_size": test_asset_cfg.tick_size,
        "pip_size": test_asset_cfg.pip_size,
        "session": None,
    }
    assert type(params["swing_length"]) is int


def _fail_before_data(monkeypatch, asset_cfg, strategies, text):
    monkeypatch.setattr(cli, "load_assets", lambda: {"TEST": asset_cfg})
    monkeypatch.setattr(cli, "load_strategy", strategies.__getitem__)
    monkeypatch.setattr(cli, "load_data", lambda *_args, **_kwargs: pytest.fail("load_data should not be called"))
    cli.run(_config(strategies=tuple(strategies), strategy_params=text))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("swing=10", "--strategy_params swing is not declared in test PARAMS"),
        ("swing_length=ten", "--strategy_params swing_length=ten: expected int"),
        ("swing_length=1.5", "--strategy_params swing_length=1.5: expected int"),
        ("swing_length=", "--strategy_params swing_length=: expected int"),
        ("swing_length=none", "--strategy_params swing_length=none: expected int"),
        ("width=nan", "--strategy_params width=nan: expected float"),
        ("retest=yes", "--strategy_params retest=yes: expected bool"),
        ("mode=none", "--strategy_params mode=none: expected str"),
        ("limit=soon", "--strategy_params limit=soon: expected none or int"),
        ("swing_length=10;swing_length=20", "--strategy_params names swing_length more than once"),
        ("swing_length", "--strategy_params items must look like name=value; got: swing_length"),
        ("swing_length=10,20", "--strategy_params takes one value per parameter; got: swing_length=10,20"),
    ],
)
def test_bad_overrides_fail_with_a_clear_message_before_data_loads(monkeypatch, test_asset_cfg, text, message):
    strategy, _ = _recording_strategy(
        {"swing_length": 20, "width": 0.4, "retest": True, "mode": "close", "limit": None}, {"limit": int}
    )

    with pytest.raises(ValueError, match=re.escape(message)):
        _fail_before_data(monkeypatch, test_asset_cfg, {"test": strategy}, text)


def test_a_parameter_missing_from_one_listed_strategy_fails_before_data_loads(monkeypatch, test_asset_cfg):
    declares, _ = _recording_strategy({"swing_length": 20})
    lacks, _ = _recording_strategy({})

    with pytest.raises(ValueError, match="--strategy_params swing_length is not declared in other PARAMS"):
        _fail_before_data(monkeypatch, test_asset_cfg, {"test": declares, "other": lacks}, "swing_length=10")


def _min_below_max(params):
    if params["min_width"] > params["max_width"]:
        raise ValueError("min_width must not exceed max_width")


def test_validate_params_errors_are_reported_with_the_strategy_name_before_data_loads(monkeypatch, test_asset_cfg):
    strategy, _ = _recording_strategy({"min_width": 0.1, "max_width": 0.4}, validate_params=_min_below_max)

    with pytest.raises(ValueError, match="test: min_width must not exceed max_width"):
        _fail_before_data(monkeypatch, test_asset_cfg, {"test": strategy}, "min_width=0.5")


def test_validate_params_receives_every_declared_parameter_even_without_the_flag(monkeypatch, tmp_path, test_asset_cfg):
    validated = []
    strategy, _ = _recording_strategy({"swing_length": 20}, validate_params=validated.append)

    _run(monkeypatch, tmp_path, test_asset_cfg, {"test": strategy})

    assert validated == [{"swing_length": 20}]


def test_strategy_params_reach_signals_charts_and_metrics(monkeypatch, tmp_path, base_data, hourly_index, test_asset_cfg):
    monkeypatch.setattr(reports, "TRADES_DIR", tmp_path / "trades")
    seen = {}

    def generate_signals(df, asset, timeframe, params):
        seen["signals"] = params
        return pd.DataFrame([{"time": hourly_index[0], "side": "long", "stop": 99}])

    def plot_indicators(fig, data, view, asset, timeframe, params):
        seen["chart"] = params

    def calculate_metrics(data, signals, trades, asset, timeframe, params):
        seen["metrics"] = params
        return {}

    strategy = SimpleNamespace(
        generate_signals=generate_signals, plot_indicators=plot_indicators, calculate_metrics=calculate_metrics
    )
    monkeypatch.setattr(runner, "load_strategy", lambda _name: strategy)

    runner.run_variants(_task(base_data, tmp_path, test_asset_cfg, trade_html=None, strategy_params={"swing_length": 10}))

    assert {hook: params["swing_length"] for hook, params in seen.items()} == {"signals": 10, "chart": 10, "metrics": 10}
    # Every key the engine adds is reserved, so a strategy parameter can never be overwritten.
    engine_keys = {key for params in seen.values() for key in params} - {"swing_length"}
    assert engine_keys <= set(RESERVED_PARAMS)
    assert seen["chart"]["tick_size"] == test_asset_cfg.tick_size
    assert seen["chart"]["trade"]["side"] == "long"


def test_variant_labels_show_only_non_default_parameter_values(monkeypatch, tmp_path, test_asset_cfg):
    strategy, _ = _recording_strategy({"swing_length": 20, "mode": "close", "limit": None})
    plain, _ = _recording_strategy({"swing_length": 20})

    (task,) = _run(monkeypatch, tmp_path, test_asset_cfg, {"test": strategy}, strategy_params="mode=break;swing_length=20")
    (plain_task,) = _run(monkeypatch, tmp_path, test_asset_cfg, {"test": plain})

    assert cli.variant_label(task) == "test TEST 1h 1h mode=break 1 variants"
    assert cli.variant_label(plain_task) == "test TEST 1h 1h 1 variants"


def test_variant_pages_list_every_parameter_value(monkeypatch, tmp_path, base_data, test_asset_cfg):
    monkeypatch.setattr(reports, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(runner, "load_strategy", lambda _name: SimpleNamespace(generate_signals=lambda *_args, **_kwargs: pd.DataFrame()))
    with_params = runner.run_variants(
        _task(base_data, tmp_path, test_asset_cfg, strategy_params={"swing_length": 10, "limit": None, "retest": True})
    )
    without_params = runner.run_variants(_task(base_data, tmp_path, test_asset_cfg))

    path = reports.write_results_html(pd.DataFrame(with_params + without_params), _config(strategies=("ema50",)))
    pages = [(path.parent / "variants" / name).read_text(encoding="utf-8") for name in ("000000.html", "000001.html")]

    assert "<h2>Strategy parameters</h2>" in pages[0]
    assert (
        "<tr><td>swing_length</td><td>10</td></tr>\n<tr><td>limit</td><td>none</td></tr>\n"
        "<tr><td>retest</td><td>true</td></tr>"
    ) in pages[0]
    assert "Strategy parameters" not in pages[1]


def test_strategy_params_flag_defaults_off_and_maps_to_config(monkeypatch):
    configs = []
    monkeypatch.setattr(cli, "run", configs.append)
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "ema50"])
    cli.main()
    monkeypatch.setattr(sys, "argv", ["tester_framework", "--strategies", "ema50", "--strategy_params", "swing_length=10"])
    cli.main()

    assert [config.strategy_params for config in configs] == [None, "swing_length=10"]


def test_a_strategy_file_declaring_params_runs_with_an_override(monkeypatch, tmp_path, test_asset_cfg):
    (tmp_path / "with_params.py").write_text(
        "import pandas as pd\n"
        "PARAMS = {'stop_offset': 1.0}\n"
        "def generate_signals(df, asset, timeframe, params):\n"
        "    return pd.DataFrame([{'time': df.index[0], 'side': 'long', 'stop': df['close'].iloc[0] - params['stop_offset']}])\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(strategy_loader, "STRATEGY_DIR", tmp_path)
    tables = []

    _run(monkeypatch, tmp_path, test_asset_cfg, {"with_params": strategy_loader.load_strategy("with_params")}, tables=tables)
    _run(
        monkeypatch,
        tmp_path,
        test_asset_cfg,
        {"with_params": strategy_loader.load_strategy("with_params")},
        tables=tables,
        strategy_params="stop_offset=2",
    )

    # A long entered at 100 on flat bars ends at the data end; only the stop distance sets its R.
    assert [table["_strategy_params"].iloc[0] for table in tables] == [{"stop_offset": 1.0}, {"stop_offset": 2.0}]
    risks = [decode_analytics(table["_analytics"].iloc[0])["trades"][0]["stop"] for table in tables]
    assert risks == [99.0, 98.0]


def test_none_on_a_none_default_without_a_declared_type_is_accepted_and_other_text_stays_a_string(
    monkeypatch, tmp_path, test_asset_cfg
):
    first, seen_first = _recording_strategy({"limit": None})
    second, seen_second = _recording_strategy({"limit": None})

    _run(monkeypatch, tmp_path, test_asset_cfg, {"test": first}, strategy_params="limit=None")
    _run(monkeypatch, tmp_path, test_asset_cfg, {"test": second}, strategy_params="limit=15")

    assert seen_first[0]["limit"] is None
    assert seen_second[0]["limit"] == "15"
