"""Low-timeframe market structure, ported from the Pine's one-minute market structure section.

`choch_sr_mtf_strategy.pine` lines 281-363 (Leviathan's `market_structure.pine`): swings, their
HH/LH/HL/LL labels, breaks of the active swings, CHoCH, and the last marked LL/HH.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


CONFIRMATIONS = ("close", "wick")


def _swing_high_bars(highs: np.ndarray, swing_length: int) -> np.ndarray:
    """Per bar, whether it is a swing high: the highest of `swing_length` bars on each side."""
    is_swing = np.zeros(len(highs), dtype=bool)
    if len(highs) < 2 * swing_length + 1:
        return is_swing
    windows = np.lib.stride_tricks.sliding_window_view(highs, 2 * swing_length + 1)
    value = windows[:, swing_length]
    left = windows[:, :swing_length].max(axis=1)
    right = windows[:, swing_length + 1 :].max(axis=1)
    # Tie rule (unverified against ta.pivothigh): an equal high on the left is allowed, one on the right is not.
    is_swing[swing_length:-swing_length] = (left <= value) & (right < value)
    return is_swing


def market_structure(df: pd.DataFrame, swing_length: int, confirmation: str = "close") -> pd.DataFrame:
    """Market structure per bar of `df`, without lookahead.

    A swing is a `swing_length`-bar pivot and appears on its confirmation bar, `swing_length` bars after
    its own bar. Breaking a swing high is bullish, breaking a swing low bearish; `confirmation="close"`
    needs a close beyond the swing, `"wick"` the high or low. Each swing breaks at most once. On one bar
    the high side is processed before the low side, as in the Pine.

    Returned columns, indexed like `df` (`*_time` columns are bar times, NaT when absent):
    - `swing_high_price/label/time`, `swing_low_price/label/time`: a swing confirmed on this bar, its
      label (HH/LH, HL/LL) and its own bar.
    - `bullish_break_price/swing_time`, `bearish_break_price/swing_time`: the swing broken on this bar
      and its own bar; `bullish_choch`, `bearish_choch`: whether that break is a CHoCH.
    - `last_hh_price/time`, `last_ll_price/time`: the last marked HH/LL and the bar that confirmed it.
    - `last_swing_high_price/time`, `last_swing_low_price/time`: the most recent confirmed swing of
      any label and its own bar, kept after it breaks.
    """
    if isinstance(swing_length, bool) or not isinstance(swing_length, int) or swing_length < 1:
        raise ValueError(f"swing_length must be an int >= 1, got {swing_length!r}")
    if confirmation not in CONFIRMATIONS:
        raise ValueError(f"confirmation must be one of {CONFIRMATIONS}, got {confirmation!r}")

    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    high_source = closes if confirmation == "close" else highs
    low_source = closes if confirmation == "close" else lows
    n = len(df)
    swing_high_bars = _swing_high_bars(highs, swing_length)
    # A swing low of the lows is a swing high of their negation, with the same tie rule.
    swing_low_bars = _swing_high_bars(-lows, swing_length)

    def prices() -> np.ndarray:
        return np.full(n, np.nan)

    def bar_positions() -> np.ndarray:  # -1 when absent; mapped to bar times at the end
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
        "last_hh_price": prices(),
        "last_hh_time": bar_positions(),
        "last_ll_price": prices(),
        "last_ll_time": bar_positions(),
        "last_swing_high_price": prices(),
        "last_swing_high_time": bar_positions(),
        "last_swing_low_price": prices(),
        "last_swing_low_time": bar_positions(),
    }

    previous_high = previous_low = math.nan
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
                # NaN compares False, so the first swing high is LH, as in the Pine.
                label = "HH" if price >= previous_high else "LH"
                out["swing_high_price"][i] = price
                out["swing_high_label"][i] = label
                out["swing_high_time"][i] = center
                previous_high, previous_high_time, high_active = price, center, True
                if label == "HH":
                    last_hh, last_hh_time = price, i
            if swing_low_bars[center]:
                price = lows[center]
                label = "HL" if price >= previous_low else "LL"
                out["swing_low_price"][i] = price
                out["swing_low_label"][i] = label
                out["swing_low_time"][i] = center
                previous_low, previous_low_time, low_active = price, center, True
                if label == "LL":
                    last_ll, last_ll_time = price, i

        if high_active and high_source[i] > previous_high:
            high_active = False
            out["bullish_break_price"][i] = previous_high
            out["bullish_break_swing_time"][i] = previous_high_time
            out["bullish_choch"][i] = previous_break_direction == -1
            previous_break_direction = 1
        if low_active and low_source[i] < previous_low:
            low_active = False
            out["bearish_break_price"][i] = previous_low
            out["bearish_break_swing_time"][i] = previous_low_time
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


__all__ = ["CONFIRMATIONS", "market_structure"]
