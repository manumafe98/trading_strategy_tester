from __future__ import annotations

import re
from pathlib import Path

import pytest

import tester_framework.strategy_loader as strategy_loader
from tester_framework.strategy_loader import load_strategy


@pytest.mark.parametrize("name", ["../some_external_strategy", "utils/fvg"])
def test_path_like_names_rejected(name):
    with pytest.raises(ValueError, match="plain file name"):
        load_strategy(name)


def test_absolute_name_rejected():
    with pytest.raises(ValueError, match="plain file name"):
        load_strategy(str(Path(strategy_loader.STRATEGY_DIR, "ema50").resolve()))


def test_name_with_py_suffix_rejected():
    with pytest.raises(ValueError, match="without the .py suffix"):
        load_strategy("ema50.py")


def test_missing_strategy_rejected():
    with pytest.raises(FileNotFoundError):
        load_strategy("definitely_not_a_real_strategy")


def test_module_without_generate_signals_rejected(monkeypatch, tmp_path):
    (tmp_path / "no_signals.py").write_text("LOW_TIMEFRAME = '5m'\n", encoding="utf-8")
    monkeypatch.setattr(strategy_loader, "STRATEGY_DIR", tmp_path)

    with pytest.raises(ValueError, match="must define generate_signals"):
        load_strategy("no_signals")


def test_load_existing_strategy():
    strategy = load_strategy("ema50")
    assert callable(getattr(strategy, "generate_signals", None))


def _write_strategy(tmp_path, monkeypatch, body):
    (tmp_path / "with_params.py").write_text(
        "def generate_signals(df, asset, timeframe, params):\n    return None\n" + body, encoding="utf-8"
    )
    monkeypatch.setattr(strategy_loader, "STRATEGY_DIR", tmp_path)


@pytest.mark.parametrize("name", ["tick_size", "pip_size", "session", "trade", "capital", "exit_mode"])
def test_declaring_a_reserved_parameter_name_fails_at_load(monkeypatch, tmp_path, name):
    _write_strategy(tmp_path, monkeypatch, f"PARAMS = {{'{name}': 1}}\n")

    with pytest.raises(ValueError, match=f"reserved.*{name}"):
        load_strategy("with_params")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("PARAMS = [('swing_length', 20)]\n", "PARAMS must be a dict"),
        ("PARAMS = {'widths': [1, 2]}\n", "widths default must be int, float, bool, str or None"),
        ("PARAMS = {'limit': None}\nPARAM_TYPES = 'int'\n", "PARAM_TYPES must be a dict"),
        ("PARAMS = {'limit': None}\nPARAM_TYPES = {'other': int}\n", "PARAM_TYPES names undeclared parameter: other"),
        ("PARAMS = {'limit': 5}\nPARAM_TYPES = {'limit': int}\n", "PARAM_TYPES entry limit needs a None default"),
        ("PARAMS = {'limit': None}\nPARAM_TYPES = {'limit': list}\n", "PARAM_TYPES entry limit must be int, float, bool or str"),
    ],
)
def test_bad_parameter_declarations_fail_at_load(monkeypatch, tmp_path, body, message):
    _write_strategy(tmp_path, monkeypatch, body)

    with pytest.raises(ValueError, match=re.escape(message)):
        load_strategy("with_params")


def test_valid_parameter_declarations_load(monkeypatch, tmp_path):
    _write_strategy(
        tmp_path,
        monkeypatch,
        "PARAMS = {'swing_length': 20, 'width': 0.4, 'retest': True, 'mode': 'close', 'limit': None}\n"
        "PARAM_TYPES = {'limit': int}\n",
    )

    assert load_strategy("with_params").PARAMS["swing_length"] == 20
