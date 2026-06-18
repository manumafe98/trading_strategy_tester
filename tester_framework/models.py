from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
    qty_step: float
    min_qty: float
    spread_points: float
    slippage_points: float
    commission_per_side: float


__all__ = ["AssetConfig", "ExitReason", "Side"]
