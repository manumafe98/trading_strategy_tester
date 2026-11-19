from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategy.utils.pivots import pivot_high_bars
from tester_framework.utils import decimal_places


CONFIRMATIONS = ("close", "wick")
EPSILON = 1e-8


def one_tick_beyond(price: float, upward: bool, tick_size: float) -> float:
    if upward:
        ticks = math.ceil((price + tick_size) / tick_size - EPSILON)
    else:
        ticks = math.floor((price - tick_size) / tick_size + EPSILON)
    return round(ticks * tick_size, decimal_places(tick_size))


def market_structure(
    df: pd.DataFrame, swing_length: int, confirmation: str = "close", tick_size: float | None = None
) -> pd.DataFrame:
    if isinstance(swing_length, bool) or not isinstance(swing_length, int) or swing_length < 1:
        raise ValueError(f"swing_length must be an int >= 1, got {swing_length!r}")
    if confirmation not in CONFIRMATIONS:
        raise ValueError(f"confirmation must be one of {CONFIRMATIONS}, got {confirmation!r}")
    by_wick = confirmation == "wick"
    if by_wick and not (tick_size is not None and tick_size > 0):
        raise ValueError(f"wick confirmation needs a positive tick_size, got {tick_size!r}")

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    opens = df["open"].to_numpy(dtype=float)
    n = len(df)
    swing_high_bars = pivot_high_bars(highs, swing_length)
    swing_low_bars = pivot_high_bars(-lows, swing_length)

    def prices() -> np.ndarray:
        return np.full(n, np.nan)

    def bar_positions() -> np.ndarray:
        return np.full(n, -1, dtype=np.int64)

    out = {
        "swing_high_price": prices(),
        "swing_high_label": np.full(n, None, dtype=object),
        "swing_high_time": bar_positions(),
        "swing_low_price": prices(),
        "swing_low_label": np.full(n, None, dtype=object),
        "swing_low_time": bar_positions(),
        "bullish_break_price": prices(),
        "bullish_break_swing_time": bar_positions(),
        "bullish_choch": np.zeros(n, dtype=bool),
        "bearish_break_price": prices(),
        "bearish_break_swing_time": bar_positions(),
        "bearish_choch": np.zeros(n, dtype=bool),
        "bullish_choch_at_open": np.zeros(n, dtype=bool),
        "bearish_choch_at_open": np.zeros(n, dtype=bool),
        "last_hh_price": prices(),
        "last_hh_time": bar_positions(),
        "last_ll_price": prices(),
        "last_ll_time": bar_positions(),
        "last_swing_high_price": prices(),
        "last_swing_high_time": bar_positions(),
        "last_swing_low_price": prices(),
        "last_swing_low_time": bar_positions(),
    }

    previous_high = previous_low = high_level = low_level = math.nan
    previous_high_time = previous_low_time = -1
    high_active = low_active = False
    previous_break_direction = 0
    last_hh = last_ll = math.nan
    last_hh_time = last_ll_time = -1

    for i in range(n):
        center = i - swing_length
        if center >= 0:
            if swing_high_bars[center]:
                price = highs[center]
                label = "HH" if price >= previous_high else "LH"
                out["swing_high_price"][i] = price
                out["swing_high_label"][i] = label
                out["swing_high_time"][i] = center
                previous_high, previous_high_time, high_active = price, center, True
                high_level = one_tick_beyond(price, True, tick_size) if by_wick else price
                if label == "HH":
                    last_hh, last_hh_time = price, i
            if swing_low_bars[center]:
                price = lows[center]
                label = "HL" if price >= previous_low else "LL"
                out["swing_low_price"][i] = price
                out["swing_low_label"][i] = label
                out["swing_low_time"][i] = center
                previous_low, previous_low_time, low_active = price, center, True
                low_level = one_tick_beyond(price, False, tick_size) if by_wick else price
                if label == "LL":
                    last_ll, last_ll_time = price, i

        if by_wick:
            high_broken = high_active and highs[i] >= high_level
            low_broken = low_active and lows[i] <= low_level
        else:
            high_broken = high_active and closes[i] > previous_high
            low_broken = low_active and closes[i] < previous_low
        if high_broken:
            high_active = False
            out["bullish_break_price"][i] = previous_high
            out["bullish_break_swing_time"][i] = previous_high_time
        if low_broken:
            low_active = False
            out["bearish_break_price"][i] = previous_low
            out["bearish_break_swing_time"][i] = previous_low_time
        path_reaches_low_first = by_wick and (
            opens[i] <= low_level or (opens[i] < high_level and opens[i] - lows[i] < highs[i] - opens[i])
        )
        out["bullish_choch_at_open"][i] = high_broken and previous_break_direction == -1
        out["bearish_choch_at_open"][i] = low_broken and previous_break_direction == 1
        for direction in (-1, 1) if path_reaches_low_first else (1, -1):
            if direction == 1 and high_broken:
                out["bullish_choch"][i] = previous_break_direction == -1
                previous_break_direction = 1
            if direction == -1 and low_broken:
                out["bearish_choch"][i] = previous_break_direction == 1
                previous_break_direction = -1

        out["last_hh_price"][i], out["last_hh_time"][i] = last_hh, last_hh_time
        out["last_ll_price"][i], out["last_ll_time"][i] = last_ll, last_ll_time
        out["last_swing_high_price"][i], out["last_swing_high_time"][i] = previous_high, previous_high_time
        out["last_swing_low_price"][i], out["last_swing_low_time"][i] = previous_low, previous_low_time

    result = pd.DataFrame(out, index=df.index)
    for column in result.columns:
        if column.endswith("_time"):
            positions = result[column].to_numpy()
            result[column] = pd.Series(df.index[np.maximum(positions, 0)], index=df.index).where(positions >= 0)
        elif column.endswith("_label"):
            result[column] = result[column].astype("string")
    return result


__all__ = ["CONFIRMATIONS", "market_structure", "one_tick_beyond"]
