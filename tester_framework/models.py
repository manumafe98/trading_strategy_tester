from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Side(StrEnum):
    LONG = "long"
    SHORT = "short"


OPERATIONS = ("all", "long_only", "short_only")


class ExitReason(StrEnum):
    TARGET = "target"
    STOP = "stop"
    SESSION = "session"
    ROLL = "roll"
    END = "end"


class TargetFill(StrEnum):
    TICK_THROUGH = "tick_through"
    TOUCH = "touch"


class IntrabarRule(StrEnum):
    PATH = "path"
    STOP_FIRST = "stop_first"


@dataclass(frozen=True)
class AssetConfig:
    ticker: str
    point_value: float
    tick_size: float
    qty_step: float
    min_qty: float
    min_risk_ticks: int
    spread_points: float
    slippage_points: float
    commission_per_side: float
    session_timezone: str
    session_start: str
    bars_per_year: dict[str, int]
    pip_size: float | None = None

    def __post_init__(self) -> None:
        if self.pip_size is None:
            object.__setattr__(self, "pip_size", self.tick_size)
        if not self.ticker.strip():
            raise ValueError("ticker must not be empty")
        for name in ("point_value", "tick_size", "qty_step", "min_qty", "pip_size"):
            raw = getattr(self, name)
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("spread_points", "slippage_points", "commission_per_side"):
            raw = getattr(self, name)
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if isinstance(self.min_risk_ticks, bool) or not isinstance(self.min_risk_ticks, int) or self.min_risk_ticks < 1:
            raise ValueError("min_risk_ticks must be a positive integer")
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


STOP_DISTANCE_UNITS = ("%", "pips")
STOP_DISTANCE_TOLERANCE = 1e-8


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def distance_in_price(distance: float, unit: str, base_price: float, pip_size: float) -> float:
    return base_price * distance / 100 if unit == "%" else distance * pip_size


@dataclass(frozen=True)
class StopDistanceLimits:
    minimum: float
    maximum: float | None
    unit: str

    def __post_init__(self) -> None:
        if self.unit not in STOP_DISTANCE_UNITS:
            raise ValueError(f"--stop_distance unit must be % or pips; got: {self.unit}")
        bounds = (self.minimum,) if self.maximum is None else (self.minimum, self.maximum)
        if any(isinstance(bound, bool) or not math.isfinite(bound) or bound < 0 for bound in bounds):
            raise ValueError(f"--stop_distance bounds must be finite and non-negative; got: {self}")
        if self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(f"--stop_distance minimum must not exceed the maximum; got: {self}")

    @classmethod
    def parse(cls, text: str) -> StopDistanceLimits:
        body = text.strip().lower()
        open_ended = body.endswith("+")
        body = body.removesuffix("+")
        unit = next((unit for unit in STOP_DISTANCE_UNITS if body.endswith(unit)), None)
        if unit is None:
            raise ValueError(f"--stop_distance range needs a unit, % or pips; got: {text}")
        body = body.removesuffix(unit)
        minimum, separator, maximum = (body, "", "") if open_ended else body.partition("-")
        bounds = (_number(minimum), math.inf if open_ended else _number(maximum))
        if (not open_ended and not separator) or None in bounds:
            raise ValueError(f"--stop_distance must be a range like 0.1-0.5%, 5-20pips or 0.2%+; got: {text}")
        return cls(bounds[0], None if open_ended else bounds[1], unit)

    def __str__(self) -> str:
        if self.maximum is None:
            return f"{self.minimum:g}{self.unit}+"
        return f"{self.minimum:g}-{self.maximum:g}{self.unit}"

    def within(self, fill_price: float, stop: float, pip_size: float) -> bool:
        risk_points = abs(fill_price - stop)
        shortest = distance_in_price(self.minimum - STOP_DISTANCE_TOLERANCE, self.unit, fill_price, pip_size)
        if risk_points < shortest:
            return False
        return self.maximum is None or risk_points <= distance_in_price(
            self.maximum + STOP_DISTANCE_TOLERANCE, self.unit, fill_price, pip_size
        )


__all__ = ["OPERATIONS", "AssetConfig", "ExitReason", "IntrabarRule", "Side", "StopDistanceLimits", "TargetFill", "distance_in_price"]
