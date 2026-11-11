from __future__ import annotations

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


@pytest.mark.xfail(strict=True, reason="loader strips the suffix, so 'ema50.py' loads strategy/ema50.py (#12)")
def test_name_with_py_suffix_rejected():
    with pytest.raises(ValueError):
        load_strategy("ema50.py")


def test_missing_strategy_rejected():
    with pytest.raises(FileNotFoundError):
        load_strategy("definitely_not_a_real_strategy")


def test_module_without_generate_signals_rejected(monkeypatch, tmp_path):
    (tmp_path / "no_signals.py").write_text("EXECUTION_TIMEFRAME = '5m'\n", encoding="utf-8")
    monkeypatch.setattr(strategy_loader, "STRATEGY_DIR", tmp_path)

    with pytest.raises(ValueError, match="must define generate_signals"):
        load_strategy("no_signals")


def test_load_existing_strategy():
    strategy = load_strategy("ema50")
    assert callable(getattr(strategy, "generate_signals", None))
