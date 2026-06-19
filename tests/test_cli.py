from __future__ import annotations

import pytest

from tester_framework.__main__ import EXIT_MODES, exit_mode_variants, status_lines, worker_count


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
