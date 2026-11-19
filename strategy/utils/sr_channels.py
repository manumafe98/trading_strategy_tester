from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategy.utils.pivots import pivot_high_bars


PIVOT_PERIOD = 10
LOOPBACK = 290
MAX_CHANNELS = 10
EXPOSED_CHANNELS = 6
MIN_STRENGTH = 1
WIDTH_UNITS = ("%", "pips")


def pivot_highs(values: np.ndarray, period: int = PIVOT_PERIOD) -> np.ndarray:
    return _confirmed(np.asarray(values, dtype=float), period)


def pivot_lows(values: np.ndarray, period: int = PIVOT_PERIOD) -> np.ndarray:
    return _confirmed(-np.asarray(values, dtype=float), period)


def _confirmed(values: np.ndarray, period: int) -> np.ndarray:
    confirmed = np.zeros(len(values), dtype=bool)
    confirmed[period:] = pivot_high_bars(values, period)[: len(values) - period]
    return confirmed


def build_channels(
    bars: pd.DataFrame, min_width: float, max_width: float, width_unit: str, pip_size: float
) -> pd.DataFrame:
    validate_widths(min_width, max_width, width_unit)
    if not math.isfinite(pip_size) or pip_size <= 0:
        raise ValueError("pip_size must be finite and positive")
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    is_high = pivot_highs(highs)
    is_low = pivot_lows(lows)

    n = len(bars)
    tops = np.full((n, EXPOSED_CHANNELS), np.nan)
    bottoms = np.full((n, EXPOSED_CHANNELS), np.nan)
    strengths = np.full((n, EXPOSED_CHANNELS), np.nan)
    reference = np.full(n, np.nan)

    pivot_values: list[float] = []
    pivot_bars: list[int] = []
    channels: list[tuple[float, float, float]] = []
    for bar in range(n - 1):
        if is_high[bar] or is_low[bar]:
            pivot_values.insert(0, highs[bar - PIVOT_PERIOD] if is_high[bar] else lows[bar - PIVOT_PERIOD])
            pivot_bars.insert(0, bar)
            while pivot_bars and bar - pivot_bars[-1] > LOOPBACK:
                pivot_values.pop()
                pivot_bars.pop()
            start = max(0, bar - LOOPBACK)
            channels = _select_channels(
                pivot_values, highs[start : bar + 1], lows[start : bar + 1], min_width, max_width, width_unit, pip_size
            )
        row = bar + 1
        for slot, (top, bottom, strength) in enumerate(channels[:EXPOSED_CHANNELS]):
            tops[row, slot], bottoms[row, slot], strengths[row, slot] = top, bottom, strength
        reference[row] = closes[bar]

    columns: dict[str, np.ndarray] = {}
    for slot in range(EXPOSED_CHANNELS):
        columns[f"top_{slot + 1}"] = tops[:, slot]
        columns[f"bottom_{slot + 1}"] = bottoms[:, slot]
        columns[f"strength_{slot + 1}"] = strengths[:, slot]
    columns["reference_close"] = reference
    return pd.DataFrame(columns, index=bars.index)


def validate_widths(
    min_width: float, max_width: float, width_unit: str, names: tuple[str, str, str] = ("min_width", "max_width", "width_unit")
) -> None:
    min_name, max_name, unit_name = names
    if width_unit not in WIDTH_UNITS:
        raise ValueError(f"{unit_name} must be one of {', '.join(WIDTH_UNITS)}")
    if not all(math.isfinite(value) for value in (min_width, max_width)):
        raise ValueError(f"{min_name} and {max_name} must be finite")
    if min_width < 0:
        raise ValueError(f"{min_name} must be non-negative")
    if max_width <= 0:
        raise ValueError(f"{max_name} must be positive")
    if min_width > max_width:
        raise ValueError(f"{min_name} must not exceed {max_name}")


def width_in_price(width: float, width_unit: str, base_price: float, pip_size: float) -> float:
    return base_price * width / 100 if width_unit == "%" else width * pip_size


def _cluster(pivot_values: list[float], seed: int, max_width: float, width_unit: str, pip_size: float) -> tuple[float, float, int]:
    low = high = pivot_values[seed]
    strength = 0
    for pivot in pivot_values:
        width = high - pivot if pivot <= high else pivot - low
        allowed = width_in_price(max_width, width_unit, min(low, pivot), pip_size)
        if width <= allowed:
            if pivot <= high:
                low = min(low, pivot)
            else:
                high = max(high, pivot)
            strength += 20
    return high, low, strength


def _select_channels(
    pivot_values: list[float],
    highs: np.ndarray,
    lows: np.ndarray,
    min_width: float,
    max_width: float,
    width_unit: str,
    pip_size: float,
) -> list[tuple[float, float, float]]:
    candidates = []
    for seed in range(len(pivot_values)):
        top, bottom, strength = _cluster(pivot_values, seed, max_width, width_unit, pip_size)
        touches = int((((highs <= top) & (highs >= bottom)) | ((lows <= top) & (lows >= bottom))).sum())
        min_allowed = width_in_price(min_width, width_unit, bottom, pip_size)
        rejected = min_width > 0 and top - bottom < min_allowed
        candidates.append((top, bottom, -1.0 if rejected else float(strength + touches)))

    order = sorted(range(len(candidates)), key=lambda index: -candidates[index][2])
    accepted: list[tuple[float, float, float]] = []
    for index in order:
        top, bottom, strength = candidates[index]
        if strength < MIN_STRENGTH * 20:
            break
        covered = any(
            accepted_bottom <= top <= accepted_top or accepted_bottom <= bottom <= accepted_top
            for accepted_top, accepted_bottom, _ in accepted
        )
        if not covered:
            accepted.append(candidates[index])
            if len(accepted) >= MAX_CHANNELS:
                break
    return accepted
