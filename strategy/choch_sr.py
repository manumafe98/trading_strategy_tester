"""CHoCH + support/resistance channels, ported from `pine_scripts_examples/choch_sr_mtf_strategy.pine`.

A setup is a touch on a support/resistance channel on the high timeframe followed by the first CHoCH the
opposite way on the low timeframe: a support touch then a bullish CHoCH is a long, a resistance touch then
a bearish CHoCH a short. Entries are close entries on the CHoCH bar.

The strategy ignores positions: it never clears touches or waits after its own signals. Each signal carries
`setup_time`, the latest contact bar of its touch, and the engine's setup-time rule discards retests that
are not in a new high-timeframe bar after the previous trade's exit.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategy.utils.market_structure import market_structure
from strategy.utils.sr_channels import EXPOSED_CHANNELS, WIDTH_UNITS, build_channels
from tester_framework.data import LOCAL_RESAMPLE_RULES, resample_data
from tester_framework.utils import decimal_places


LOW_TIMEFRAME = "1m"
PARAMS = {
    "swing_length": 20,
    "zone_min_width": 0.1,
    "zone_max_width": 0.4,
    "zone_unit": "%",
    "stop_min": 0.1,
    "stop_max": 0.5,
    "stop_unit": "%",
}
UNITS = WIDTH_UNITS
EPSILON = 1e-8
SUPPORT, RESISTANCE = 1, -1
ROLE_NAMES = {SUPPORT: "support", RESISTANCE: "resistance", 0: "in-price"}
OUTPUT_COLUMNS = [
    "time",
    "side",
    "stop",
    "entry",
    "setup_time",
    "zone_top",
    "zone_bottom",
    "zone_role",
    "touch_time",
    "choch_level",
    "swing_time",
    "stop_swing_time",
]


def validate_params(params):
    if params["swing_length"] < 1:
        raise ValueError("swing_length must be at least 1")
    for unit_key in ("zone_unit", "stop_unit"):
        if params[unit_key] not in UNITS:
            raise ValueError(f"{unit_key} must be one of {', '.join(UNITS)}")
    for low_key, high_key in (("zone_min_width", "zone_max_width"), ("stop_min", "stop_max")):
        if params[low_key] < 0:
            raise ValueError(f"{low_key} must be non-negative")
        if params[high_key] <= 0:
            raise ValueError(f"{high_key} must be positive")
        if params[low_key] > params[high_key]:
            raise ValueError(f"{low_key} must not exceed {high_key}")


class Touch:
    """A pending touch: the channel it follows, the role it was touched with, its first and latest contact bars."""

    # A plain class: the strategy loader runs this module outside sys.modules, which dataclasses need.
    def __init__(self, top: float, bottom: float, role: int, bar: int):
        self.top, self.bottom, self.role = top, bottom, role
        self.first_bar = self.latest_bar = bar

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2


def channel_role(top: float, bottom: float, reference_close: float) -> int:
    if math.isnan(top) or math.isnan(bottom) or top == 0 or bottom == 0:
        return 0
    return SUPPORT if reference_close > top else RESISTANCE if reference_close < bottom else 0


def nearest_channel(touch: Touch, channels: list[tuple[float, float]], tick_size: float) -> tuple[float, float] | None:
    """The channel whose midpoint is closest to the touch's, within half their summed widths plus a tick."""
    best, best_distance = None, math.inf
    for top, bottom in channels:
        allowed = ((touch.top - touch.bottom) + (top - bottom)) / 2 + tick_size
        distance = abs((top + bottom) / 2 - touch.midpoint)
        if distance <= allowed and distance < best_distance:
            best, best_distance = (top, bottom), distance
    return best


def stop_distance_ok(entry: float, stop: float, params: dict) -> bool:
    distance = abs(entry - stop)
    measured = distance / entry * 100 if params["stop_unit"] == "%" else distance / params["pip_size"]
    return params["stop_min"] - EPSILON <= measured <= params["stop_max"] + EPSILON


def generate_signals(df, asset, timeframe, params):
    validate_params(params)
    tick_size = float(params.get("tick_size", 0))
    if tick_size <= 0:
        raise ValueError("choch_sr requires a positive tick_size strategy parameter")
    if timeframe not in LOCAL_RESAMPLE_RULES or timeframe == "1d":
        raise ValueError(f"choch_sr supports intraday high timeframes only, got {timeframe}")
    # A clock-aligned resample; a 1m high timeframe resamples the 1m bars to themselves.
    rule = LOCAL_RESAMPLE_RULES[timeframe] or "1min"
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    high_bars = resample_data(df, rule)
    channels = build_channels(
        high_bars, params["zone_min_width"], params["zone_max_width"], params["zone_unit"], params["pip_size"]
    )
    tops = channels[[f"top_{slot + 1}" for slot in range(EXPOSED_CHANNELS)]].to_numpy()
    bottoms = channels[[f"bottom_{slot + 1}" for slot in range(EXPOSED_CHANNELS)]].to_numpy()
    reference_closes = channels["reference_close"].to_numpy()
    period_starts = df.index.floor(rule)
    high_rows = high_bars.index.get_indexer(period_starts)
    new_period = np.r_[True, period_starts[1:] != period_starts[:-1]]

    structure = market_structure(df, params["swing_length"])
    bullish_choch = structure["bullish_choch"].to_numpy()
    bearish_choch = structure["bearish_choch"].to_numpy()
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    times = df.index

    touches: list[Touch] = []
    range_high = range_low = math.nan
    rows = []
    for bar in range(len(df)):
        # The developing high-timeframe range, from the closed low-timeframe bars since its bar opened.
        if new_period[bar]:
            range_high, range_low = highs[bar], lows[bar]
        else:
            range_high, range_low = max(range_high, highs[bar]), min(range_low, lows[bar])

        high_row = high_rows[bar]
        reference_close = reference_closes[high_row]
        in_force = [
            (top, bottom)
            for top, bottom in zip(tops[high_row], bottoms[high_row])
            if not math.isnan(top) and not math.isnan(bottom) and top != 0 and bottom != 0
        ]

        # Pending touches follow their channel by midpoint and drop when none matches or its role flips.
        kept = []
        for touch in touches:
            channel = nearest_channel(touch, in_force, tick_size)
            if channel is None:
                continue
            role = channel_role(*channel, reference_close)
            if role != 0 and role != touch.role:
                continue
            touch.top, touch.bottom = channel
            if range_high >= touch.bottom and range_low <= touch.top:
                touch.latest_bar = bar
            kept.append(touch)
        touches = kept

        for top, bottom in in_force:
            role = channel_role(top, bottom, reference_close)
            if role == 0 or not (range_high >= bottom and range_low <= top):
                continue
            midpoint = (top + bottom) / 2
            already_pending = any(
                abs(midpoint - touch.midpoint) <= ((touch.top - touch.bottom) + (top - bottom)) / 2 + tick_size
                for touch in touches
            )
            if not already_pending:
                touches.append(Touch(top, bottom, role, bar))

        for is_choch, role, side in ((bullish_choch, SUPPORT, "long"), (bearish_choch, RESISTANCE, "short")):
            if not is_choch[bar]:
                continue
            touch = next((t for t in touches if t.role == role and t.first_bar < bar), None)
            if touch is not None:
                row = setup_row(structure, bar, side, touch, closes[bar], reference_close, times, params)
                if row is not None:
                    rows.append(row)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS) if rows else pd.DataFrame(columns=OUTPUT_COLUMNS)


def setup_row(structure, bar, side, touch, entry, reference_close, times, params):
    """The signal for a CHoCH that uses `touch`, or None when the stop fails the side or distance checks."""
    tick_size = float(params["tick_size"])
    is_long = side == "long"
    prefix = "bullish" if is_long else "bearish"
    swing = structure.iloc[bar]
    marked_price = swing["last_ll_price"] if is_long else swing["last_hh_price"]
    marked_time = swing["last_ll_time"] if is_long else swing["last_hh_time"]
    if pd.isna(marked_price):
        return None
    if is_long:
        stop = math.floor((marked_price - tick_size) / tick_size + EPSILON) * tick_size
    else:
        stop = math.ceil((marked_price + tick_size) / tick_size - EPSILON) * tick_size
    stop = round(stop, decimal_places(tick_size))
    if (stop >= entry if is_long else stop <= entry) or not stop_distance_ok(entry, stop, params):
        return None
    # The marked swing is confirmed `swing_length` bars after its own bar.
    marked_bar = times.get_loc(marked_time) - params["swing_length"]
    return {
        "time": times[bar],
        "side": side,
        "stop": stop,
        "entry": entry,
        "setup_time": times[touch.latest_bar],
        "zone_top": touch.top,
        "zone_bottom": touch.bottom,
        "zone_role": ROLE_NAMES[channel_role(touch.top, touch.bottom, reference_close)],
        "touch_time": times[touch.first_bar],
        "choch_level": swing[f"{prefix}_break_price"],
        "swing_time": swing[f"{prefix}_break_swing_time"],
        "stop_swing_time": times[marked_bar],
    }
