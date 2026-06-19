from __future__ import annotations

import pytest

from tester_framework.strategy_loader import load_strategy


def test_path_traversal_rejected():
    with pytest.raises(ValueError):
        load_strategy("../some_external_strategy")


def test_missing_strategy_rejected():
    with pytest.raises(FileNotFoundError):
        load_strategy("definitely_not_a_real_strategy")


def test_load_existing_strategy():
    strategy = load_strategy("ema50")
    assert callable(getattr(strategy, "generate_signals", None))
