import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from strategy import choch_sr  # noqa: E402
from tester_framework.backtest import run_backtest  # noqa: E402
from tester_framework.data import load_data  # noqa: E402
from tester_framework.models import IntrabarRule, TargetFill  # noqa: E402
from tester_framework.settings import load_assets  # noqa: E402

WINDOW_START = pd.Timestamp("2026-05-10 22:00")
WINDOW_END = pd.Timestamp("2026-06-03 02:59")

if len(sys.argv) < 2:
    sys.exit("usage: compare_trades.py TV_TRADES_JSON [single-contract]")
tv_trades_path = Path(sys.argv[1])

cfg = load_assets()["MGC"]
data = load_data("MGC", cfg, "1m", "2026", "local")
data = data.loc[WINDOW_START:WINDOW_END]
print("bars", len(data), data.index[0], data.index[-1], "contracts", data["contract"].unique() if "contract" in data else "-")
if len(sys.argv) > 2 and sys.argv[2] == "single-contract":
    data = data.drop(columns=["contract"])

params = {**choch_sr.PARAMS, "tick_size": cfg.tick_size, "pip_size": cfg.pip_size, "session": None}
signals = choch_sr.generate_signals(data, "MGC", "15m", params)
_, trades = run_backtest(
    data, signals, asset="MGC", timeframe="15m", strategy="choch_sr", risk_reward_ratio=2, exit_mode="fixed",
    operation="all", risk_pct=1, capital=10_000, with_costs=False, asset_cfg=cfg, low_timeframe="1m",
    target_fill=TargetFill.TOUCH, intrabar=IntrabarRule.PATH,
)
ours = pd.DataFrame(
    [{"time": t["signal_time"], "side": str(t["side"]), "entry": t["entry"], "stop": t["stop"], "exit_time": t["exit_time"],
      "exit": t["exit"], "setup_time": t.get("setup_time")} for t in trades]
)
tv = pd.DataFrame(json.loads(tv_trades_path.read_text()))
tv["time"] = pd.to_datetime(tv["entry_ms"], unit="ms")
tv["exit_time"] = pd.to_datetime(tv["exit_ms"], unit="ms")

merged = ours.merge(tv[["time", "side", "entry", "exit_time", "exit"]], on="time", how="outer", suffixes=("", "_tv"), indicator=True)
pd.set_option("display.width", 220)
print(merged.sort_values("time").to_string(index=False))
print(merged["_merge"].value_counts().to_string())
