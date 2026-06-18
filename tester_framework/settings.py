from __future__ import annotations

import json
from pathlib import Path

from .models import AssetConfig


DEFAULT_TIMEFRAMES = ["1h"]
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
RESULTS_DIR = ROOT / "results"
STRATEGY_DIR = ROOT / "strategy"
TRADES_DIR = ROOT / "trades"

TIMEFRAMES = {
    "1m": ("1m", None),
    "2m": ("2m", None),
    "5m": ("5m", None),
    "5min": ("5m", None),
    "10m": ("5m", "10min"),
    "10min": ("5m", "10min"),
    "15m": ("15m", None),
    "15min": ("15m", None),
    "30m": ("30m", None),
    "30min": ("30m", None),
    "1h": ("1h", None),
    "60m": ("60m", None),
    "1d": ("1d", None),
}


def get_project_root() -> Path:
    return ROOT


def load_assets() -> dict[str, AssetConfig]:
    path = CONFIG_DIR / "assets.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing asset config: {path}")
    raw_assets = json.loads(path.read_text(encoding="utf-8"))
    assets = {}
    required = set(AssetConfig.__dataclass_fields__)
    for name, raw in raw_assets.items():
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"{name} missing asset config keys: {', '.join(missing)}")
        assets[name.upper()] = AssetConfig(
            ticker=str(raw["ticker"]),
            point_value=float(raw["point_value"]),
            qty_step=float(raw["qty_step"]),
            min_qty=float(raw["min_qty"]),
            spread_points=float(raw["spread_points"]),
            slippage_points=float(raw["slippage_points"]),
            commission_per_side=float(raw["commission_per_side"]),
        )
    return assets


__all__ = [
    "CONFIG_DIR",
    "DEFAULT_TIMEFRAMES",
    "RESULTS_DIR",
    "ROOT",
    "STRATEGY_DIR",
    "TIMEFRAMES",
    "TRADES_DIR",
    "get_project_root",
    "load_assets",
]
