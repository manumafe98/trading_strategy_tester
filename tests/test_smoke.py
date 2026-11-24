from __future__ import annotations

import subprocess
import sys

import pytest

from tester_framework.data import local_csv_path
from tester_framework.settings import ROOT, default_data_dir

pytestmark = pytest.mark.smoke

COMMON_ARGS = ("--asset", "MGC", "--time_period", "2021", "--data_source", "local", "--operation", "all",
               "--risk", "1", "--capital", "10000", "--trade_html", "0")

REFERENCE_RUNS = {
    "ema50": (
        ("--strategies", "ema50", "--high_timeframe", "1h", "--risk_reward_ratio", "1", "--with_costs"),
        "ema50 MGC 1h 1h 1 fixed 139 361 63 76 66 / 66 0 / 0 73 / 73 47.48% / 47.48% -0.05R / -0.15R 8h 10m "
        "-5.62% / -14.86% 14.95% / 21.35% -0.59 / -1.60 -0.38 / -0.70",
    ),
    "ema50_touch_path": (
        ("--strategies", "ema50", "--high_timeframe", "1h", "--risk_reward_ratio", "1", "--with_costs",
         "--target_fill", "touch", "--intrabar", "path"),
        "ema50 MGC 1h 1h 1 fixed 139 361 63 76 66 / 66 0 / 0 73 / 73 47.48% / 47.48% -0.05R / -0.15R 8h 9m "
        "-5.62% / -14.86% 14.95% / 21.35% -0.59 / -1.60 -0.38 / -0.70",
    ),
    "choch_sr": (
        ("--strategies", "choch_sr", "--high_timeframe", "15m", "--risk_reward_ratio", "2",
         "--target_fill", "touch", "--intrabar", "path", "--stop_distance", "0.1-0.5%"),
        "choch_sr MGC 15m 1m 2 fixed 0.1-0.5% 394 1282 198 196 132 0 262 33.50% -0.01R 9h 17m -9.91% 34.54% -0.34 -0.29",
    ),
}


def has_local_data(asset: str) -> bool:
    try:
        local_csv_path(asset, default_data_dir())
    except (FileNotFoundError, ValueError):
        return False
    return True


def result_row(stdout: str, strategy: str) -> str:
    rows = [" ".join(line.split()) for line in stdout.splitlines() if line.split()[:1] == [strategy]]
    assert len(rows) == 1, stdout
    return rows[0]


@pytest.mark.skipif(not has_local_data("MGC"), reason="local MGC data not found")
@pytest.mark.parametrize("name", REFERENCE_RUNS)
def test_reference_run_prints_its_known_result(name):
    args, expected = REFERENCE_RUNS[name]
    completed = subprocess.run(
        [sys.executable, "-m", "tester_framework", *args, *COMMON_ARGS],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert result_row(completed.stdout, args[1]) == expected
