from __future__ import annotations

import json
from pathlib import Path

from .models import AssetConfig


DEFAULT_TIMEFRAMES = ["1h"]
FINANCIAL_COLUMNS = ("Return", "Max DD", "Sharpe Ratio", "Return / DD")
COST_PAIR_COLUMNS = ("W", "BE", "L", "Win Rate", "Expectancy R", *FINANCIAL_COLUMNS)
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
RESULTS_DIR = ROOT / "results"
STRATEGY_DIR = ROOT / "strategy"
TRADES_DIR = ROOT / "trades"

TIMEFRAMES = {
    "1m": ("1m", None),
    "2m": ("2m", None),
    "5m": ("5m", None),
    "10m": ("5m", "10min"),
    "15m": ("15m", None),
    "30m": ("30m", None),
    "1h": ("1h", None),
    "1d": ("1d", None),
}


def get_project_root() -> Path:
    return ROOT


def load_assets() -> dict[str, AssetConfig]:
    path = CONFIG_DIR / "assets.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing asset config: {path}")
    raw_assets = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_assets, dict):
        raise ValueError(f"{path} must contain an object of asset configurations")
    assets = {}
    required = set(AssetConfig.__dataclass_fields__)
    for name, raw in raw_assets.items():
        if not isinstance(raw, dict):
            raise ValueError(f"{name} asset config must be an object")
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"{name} missing asset config keys: {', '.join(missing)}")
        try:
            raw_bars = raw["bars_per_year"]
            if not isinstance(raw_bars, dict):
                raise ValueError("bars_per_year must be an object")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_bars.values()):
                raise ValueError("bars_per_year values must be integers")
            missing_bars = sorted(set(TIMEFRAMES) - raw_bars.keys())
            if missing_bars:
                raise ValueError(f"bars_per_year missing timeframes: {', '.join(missing_bars)}")
            assets[name.upper()] = AssetConfig(
                ticker=str(raw["ticker"]),
                point_value=float(raw["point_value"]),
                tick_size=float(raw["tick_size"]),
                qty_step=float(raw["qty_step"]),
                min_qty=float(raw["min_qty"]),
                spread_points=float(raw["spread_points"]),
                slippage_points=float(raw["slippage_points"]),
                commission_per_side=float(raw["commission_per_side"]),
                session_timezone=str(raw["session_timezone"]),
                session_start=str(raw["session_start"]),
                bars_per_year={str(tf): value for tf, value in raw_bars.items()},
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} invalid asset config: {exc}") from exc
    return assets


__all__ = [
    "CONFIG_DIR",
    "DEFAULT_TIMEFRAMES",
    "COST_PAIR_COLUMNS",
    "FINANCIAL_COLUMNS",
    "RESULTS_DIR",
    "ROOT",
    "STRATEGY_DIR",
    "TIMEFRAMES",
    "TRADES_DIR",
    "get_project_root",
    "load_assets",
]
