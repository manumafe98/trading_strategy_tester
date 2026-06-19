from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


class ExitReason(StrEnum):
    STOP = "stop"
    TARGET = "target"


@dataclass(frozen=True)
class AssetConfig:
    ticker: str
    point_value: float
    tick_size: float
    qty_step: float
    min_qty: float
    spread_points: float
    slippage_points: float
    commission_per_side: float
    session_timezone: str
    session_start: str
    bars_per_year: dict[str, int]

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            raise ValueError("ticker must not be empty")
        for name in ("point_value", "tick_size", "qty_step", "min_qty"):
            raw = getattr(self, name)
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("spread_points", "slippage_points", "commission_per_side"):
            raw = getattr(self, name)
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.bars_per_year or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in self.bars_per_year.values()
        ):
            raise ValueError("bars_per_year values must be positive integers")
        try:
            ZoneInfo(self.session_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(f"Unknown session timezone: {self.session_timezone}") from None
        try:
            time.fromisoformat(self.session_start)
        except ValueError:
            raise ValueError(f"Invalid session_start: {self.session_start}") from None


__all__ = ["AssetConfig", "ExitReason", "Side"]
