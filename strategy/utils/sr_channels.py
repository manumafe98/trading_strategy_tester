"""Support/resistance channels from pivot clustering.

Ports `f_sr_state` and `f_get_sr_values` from `pine_scripts_examples/choch_sr_mtf_strategy.pine`, which
follow `pine_scripts_examples/support_and_resistance.pine` with the indicator's pick-and-mark selection
written as one pass over the candidates sorted by strength.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


PIVOT_PERIOD = 10
LOOPBACK = 290
MAX_CHANNELS = 10
EXPOSED_CHANNELS = 6
MIN_STRENGTH = 1
WIDTH_UNITS = ("%", "pips")


def pivot_highs(values: np.ndarray, period: int = PIVOT_PERIOD) -> np.ndarray:
    """Boolean per confirmation bar: the bar `period` bars back is a pivot high.

    The pivot may equal a bar on its left but must be strictly above every bar on its right.
    """
    return _pivots(np.asarray(values, dtype=float), period)


def pivot_lows(values: np.ndarray, period: int = PIVOT_PERIOD) -> np.ndarray:
    """Mirror of `pivot_highs`: equal allowed on the left, strictly below every bar on the right."""
    return _pivots(-np.asarray(values, dtype=float), period)


def _pivots(values: np.ndarray, period: int) -> np.ndarray:
    confirmed = np.zeros(len(values), dtype=bool)
    if len(values) < 2 * period + 1:
        return confirmed
    windows = sliding_window_view(values, 2 * period + 1)
    center = windows[:, period]
    left_ok = (windows[:, :period] <= center[:, None]).all(axis=1)
    right_ok = (windows[:, period + 1 :] < center[:, None]).all(axis=1)
    confirmed[2 * period :] = left_ok & right_ok
    return confirmed


def build_channels(
    bars: pd.DataFrame, min_width: float, max_width: float, width_unit: str, pip_size: float
) -> pd.DataFrame:
    """Channels in force on each high-timeframe bar, built only from bars that have already closed.

    Row k holds the channels computed at the close of bar k-1 (`top_n`, `bottom_n`, `strength_n` for the
    first six, strongest first) and that bar's close as `reference_close`. Unused slots and row 0 are NaN.
    Widths are in percent of price (`%`) or in `pip_size` units (`pips`); `min_width` 0 disables the
    minimum filter.
    """
    _validate_widths(min_width, max_width, width_unit, pip_size)
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

    pivot_values: list[float] = []  # newest first
    pivot_bars: list[int] = []  # the bar each pivot was confirmed on
    channels: list[tuple[float, float, float]] = []
    for bar in range(n - 1):
        if is_high[bar] or is_low[bar]:
            # A bar confirming both keeps only the pivot high, as the Pine does.
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


def _validate_widths(min_width: float, max_width: float, width_unit: str, pip_size: float) -> None:
    if width_unit not in WIDTH_UNITS:
        raise ValueError(f"width_unit must be one of {', '.join(WIDTH_UNITS)}")
    if not all(math.isfinite(value) for value in (min_width, max_width, pip_size)):
        raise ValueError("channel widths and pip_size must be finite")
    if min_width < 0:
        raise ValueError("min_width must be non-negative")
    if max_width <= 0:
        raise ValueError("max_width must be positive")
    if min_width > max_width:
        raise ValueError("min_width must not exceed max_width")
    if pip_size <= 0:
        raise ValueError("pip_size must be positive")


def _cluster(pivot_values: list[float], seed: int, max_width: float, width_unit: str, pip_size: float) -> tuple[float, float, int]:
    low = high = pivot_values[seed]
    strength = 0
    for pivot in pivot_values:
        width = high - pivot if pivot <= high else pivot - low
        allowed = min(low, pivot) * max_width / 100 if width_unit == "%" else max_width * pip_size
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
        min_allowed = bottom * min_width / 100 if width_unit == "%" else min_width * pip_size
        rejected = min_width > 0 and top - bottom < min_allowed
        candidates.append((top, bottom, -1.0 if rejected else float(strength + touches)))

    # Strongest first; equal strengths keep the newer pivot first (a stable sort over newest-first pivots).
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
