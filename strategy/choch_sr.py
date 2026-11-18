"""CHoCH + support/resistance channels, ported from `pine_scripts_examples/choch_sr_mtf_strategy.pine`.

A setup is a touch on a support/resistance channel on the high timeframe followed by the first CHoCH the
opposite way on the low timeframe: a support touch then a bullish CHoCH is a long, a resistance touch then
a bearish CHoCH a short. Entries are close entries on the CHoCH bar.

The strategy ignores positions: it never clears touches or waits after its own signals. Each signal carries
`setup_time`, the latest contact bar of its touch, and the engine's setup-time rule discards retests that
are not in a new high-timeframe bar after the previous trade's exit.

A futures roll (a change in the `contract` column) clears pivots, channels, pending touches and swings;
each contract's bars are processed on their own. Data without a `contract` column never resets.
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
    "channel_min_width": 0.1,
    "channel_max_width": 0.4,
    "channel_unit": "%",
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
    "channel_top",
    "channel_bottom",
    "channel_role",
    "touch_time",
    "choch_level",
    "swing_time",
    "stop_swing_time",
    "stop_swing_price",
    "plot_start_time",
]
PLOT_KEYS = (
    "signal_time",
    "exit_time",
    "channel_top",
    "channel_bottom",
    "touch_time",
    "choch_level",
    "swing_time",
    "stop_swing_time",
    "stop_swing_price",
)


def validate_params(params):
    if params["swing_length"] < 1:
        raise ValueError("swing_length must be at least 1")
    for unit_key in ("channel_unit", "stop_unit"):
        if params[unit_key] not in UNITS:
            raise ValueError(f"{unit_key} must be one of {', '.join(UNITS)}")
    for low_key, high_key in (("channel_min_width", "channel_max_width"), ("stop_min", "stop_max")):
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

    rows = []
    # A roll clears pivots, channels, touches and swings: each contract's bars are processed on their own.
    for segment in contract_segments(df):
        rows.extend(segment_signals(segment, rule, tick_size, params))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS) if rows else pd.DataFrame(columns=OUTPUT_COLUMNS)


def contract_segments(df: pd.DataFrame) -> list[pd.DataFrame]:
    """`df` split at every `contract` change; data without a `contract` column is one segment."""
    if "contract" not in df:
        return [df]
    contracts = df["contract"]
    segment_ids = (contracts != contracts.shift()).cumsum()
    return [segment for _, segment in df.groupby(segment_ids, sort=False)]


def segment_signals(df, rule, tick_size, params):
    """Signal rows for one contract's bars."""
    high_bars = resample_data(df, rule)
    channels = build_channels(
        high_bars, params["channel_min_width"], params["channel_max_width"], params["channel_unit"], params["pip_size"]
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
            # Any channel with the matching role counts. Touches are never cleared, so the Pine's first pending
            # touch could be a stale one; the latest contact is the one the setup-time rule can accept.
            usable = [t for t in touches if t.role == role and t.first_bar < bar]
            if usable:
                touch = max(usable, key=lambda t: t.latest_bar)
                row = signal_row(structure, bar, side, touch, closes[bar], reference_close, times, params)
                if row is not None:
                    rows.append(row)
    return rows


def signal_row(structure, bar, side, touch, entry, reference_close, times, params):
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
    touch_time = times[touch.first_bar]
    swing_time = swing[f"{prefix}_break_swing_time"]
    stop_swing_time = times[marked_bar]
    return {
        "time": times[bar],
        "side": side,
        "stop": stop,
        "entry": entry,
        "setup_time": times[touch.latest_bar],
        "channel_top": touch.top,
        "channel_bottom": touch.bottom,
        "channel_role": ROLE_NAMES[channel_role(touch.top, touch.bottom, reference_close)],
        "touch_time": touch_time,
        "choch_level": swing[f"{prefix}_break_price"],
        "swing_time": swing_time,
        "stop_swing_time": stop_swing_time,
        "stop_swing_price": marked_price,
        # The trade chart opens on the earliest bar it draws.
        "plot_start_time": min(touch_time, swing_time, stop_swing_time),
    }


def plot_indicators(fig, data, view, asset, timeframe, params):
    """The touched channel from the touch bar to the exit, the touch bar, the broken swing and the stop swing."""
    trade = (params or {}).get("trade") or {}
    if not all(key in trade for key in PLOT_KEYS):
        return
    is_long = trade["side"] == "long"
    color = "green" if is_long else "red"
    role = "support" if is_long else "resistance"
    touch_time = pd.Timestamp(trade["touch_time"])
    fig.add_shape(
        type="rect",
        x0=touch_time,
        x1=pd.Timestamp(trade["exit_time"]),
        y0=trade["channel_bottom"],
        y1=trade["channel_top"],
        fillcolor="rgba(0,200,0,0.15)" if is_long else "rgba(255,0,0,0.15)",
        line={"color": color, "width": 1},
        name=f"{role} channel",
    )
    # The touch comes from above a support channel and from below a resistance channel.
    touch_price = trade["channel_top"] if is_long else trade["channel_bottom"]
    fig.add_scatter(
        x=[touch_time],
        y=[touch_price],
        mode="markers",
        name="touch",
        marker={"color": color, "size": 10, "symbol": "x"},
    )
    fig.add_shape(
        type="line",
        x0=pd.Timestamp(trade["swing_time"]),
        x1=pd.Timestamp(trade["signal_time"]),
        y0=trade["choch_level"],
        y1=trade["choch_level"],
        line={"color": color, "width": 1, "dash": "dash"},
        label={"text": "CHoCH", "textposition": "end"},
        name="CHoCH",
    )
    label = "LL" if is_long else "HH"
    fig.add_scatter(
        x=[pd.Timestamp(trade["stop_swing_time"])],
        y=[trade["stop_swing_price"]],
        mode="markers+text",
        text=[label],
        textposition="bottom center" if is_long else "top center",
        name=f"stop swing ({label})",
        marker={"color": "orange", "size": 9, "symbol": "triangle-up" if is_long else "triangle-down"},
    )
