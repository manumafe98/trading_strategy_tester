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
from strategy.utils.sr_channels import EXPOSED_CHANNELS, build_channels, validate_widths, width_in_price
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
    "stop_source": "last_ll_hh",
    "max_touch_minutes": None,
    "touch_buckets": "0-5,5-15,15-30,30-60,60+",
}
PARAM_TYPES = {"max_touch_minutes": int}
STOP_SOURCES = ("last_ll_hh", "last_swing")
EPSILON = 1e-8
CHANNEL_CACHE: dict[tuple, pd.DataFrame] = {}
CHANNEL_CACHE_SIZE = 32
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
    "touch_to_choch_minutes",
    "choch_level",
    "swing_time",
    "stop_swing_time",
    "stop_swing_price",
    "stop_source",
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
    "stop_source",
)


def validate_params(params):
    if params["swing_length"] < 1:
        raise ValueError("swing_length must be at least 1")
    for names in (("channel_min_width", "channel_max_width", "channel_unit"), ("stop_min", "stop_max", "stop_unit")):
        validate_widths(*(params[name] for name in names), names=names)
    if params["stop_source"] not in STOP_SOURCES:
        raise ValueError(f"stop_source must be one of {', '.join(STOP_SOURCES)}, got {params['stop_source']}")
    if params["max_touch_minutes"] is not None and params["max_touch_minutes"] < 1:
        raise ValueError("max_touch_minutes must be at least 1 or none")
    touch_bands(params["touch_buckets"])


def touch_bands(text: str) -> list[tuple[float, float, str]]:
    bands = []
    previous_high = 0.0
    for band in text.split(","):
        band = band.strip()
        try:
            if band.endswith("+"):
                low, high = float(band[:-1]), math.inf
            else:
                low_text, _, high_text = band.partition("-")
                low, high = float(low_text), float(high_text)
        except ValueError:
            raise ValueError(f"touch_buckets band {band!r} must look like 5-15 or 60+") from None
        if low != previous_high or not high > low:
            raise ValueError(
                "touch_buckets must be increasing bands from 0 with no gaps or overlaps, open-ended only at the end"
            )
        label = f"{low:g}+m" if high == math.inf else f"{low:g}-{high:g}m"
        bands.append((low, high, label))
        previous_high = high
    return bands


class Touch:
    """A pending touch: the channel it follows, the role it was touched with, the bar it was first seen on, and
    the first and latest bars of its current contact.

    Contact is interrupted on a bar whose developing high-timeframe range does not overlap the channel, which
    happens when a high-timeframe bar opens clear of it; the next contact starts a new run.
    """

    # A plain class: the strategy loader runs this module outside sys.modules, which dataclasses need.
    def __init__(self, top: float, bottom: float, role: int, bar: int):
        self.top, self.bottom, self.role = top, bottom, role
        self.first_bar = self.contact_start = self.latest_bar = bar
        self.prior_contact_start = self.prior_latest_bar = bar
        self.in_contact = True

    def update_contact(self, bar: int, overlaps: bool) -> None:
        self.prior_contact_start, self.prior_latest_bar = self.contact_start, self.latest_bar
        if overlaps:
            if not self.in_contact:
                self.contact_start = bar
            self.latest_bar = bar
        self.in_contact = overlaps

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2


def is_channel(top: float, bottom: float) -> bool:
    """Whether a channel slot holds a channel: unused slots are NaN, and the Pine leaves 0 for none."""
    return not (math.isnan(top) or math.isnan(bottom) or top == 0 or bottom == 0)


def channel_role(top: float, bottom: float, reference_close: float) -> int:
    if not is_channel(top, bottom):
        return 0
    return SUPPORT if reference_close > top else RESISTANCE if reference_close < bottom else 0


def match_distance(touch: Touch, top: float, bottom: float, tick_size: float) -> float:
    """The distance between the touch's and the channel's midpoints, or inf when it exceeds half their summed
    widths plus a tick."""
    allowed = ((touch.top - touch.bottom) + (top - bottom)) / 2 + tick_size
    distance = abs((top + bottom) / 2 - touch.midpoint)
    return distance if distance <= allowed else math.inf


def nearest_channel(touch: Touch, channels: list[tuple[float, float]], tick_size: float) -> tuple[float, float] | None:
    """The matching channel whose midpoint is closest to the touch's."""
    best, best_distance = None, math.inf
    for top, bottom in channels:
        distance = match_distance(touch, top, bottom, tick_size)
        if distance < best_distance:
            best, best_distance = (top, bottom), distance
    return best


def stop_distance_ok(entry: float, stop: float, params: dict) -> bool:
    unit, pip_size = params["stop_unit"], params["pip_size"]
    shortest = width_in_price(params["stop_min"] - EPSILON, unit, entry, pip_size)
    longest = width_in_price(params["stop_max"] + EPSILON, unit, entry, pip_size)
    return shortest <= abs(entry - stop) <= longest


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
    channels = cached_channels(high_bars, params)
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
        in_force = [(top, bottom) for top, bottom in zip(tops[high_row], bottoms[high_row]) if is_channel(top, bottom)]

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
            touch.update_contact(bar, range_high >= touch.bottom and range_low <= touch.top)
            kept.append(touch)
        touches = kept

        for top, bottom in in_force:
            role = channel_role(top, bottom, reference_close)
            if role == 0 or not (range_high >= bottom and range_low <= top):
                continue
            if not any(match_distance(touch, top, bottom, tick_size) < math.inf for touch in touches):
                touches.append(Touch(top, bottom, role, bar))

        for is_choch, role, side in ((bullish_choch, SUPPORT, "long"), (bearish_choch, RESISTANCE, "short")):
            if not is_choch[bar]:
                continue
            # Any channel with the matching role counts. Touches are never cleared, so the Pine's first pending
            # touch could be a stale one; the latest contact is the one the setup-time rule can accept.
            usable = [
                t for t in touches if t.role == role and t.first_bar < bar and within_touch_limit(t, bar, times, params)
            ]
            if usable:
                touch = max(usable, key=lambda t: t.prior_latest_bar)
                row = signal_row(structure, bar, side, touch, closes[bar], reference_close, times, tick_size, params)
                if row is not None:
                    row["touch_to_choch_minutes"] = (times[bar] - row["touch_time"]) / pd.Timedelta(minutes=1)
                    rows.append(row)
    return rows


def within_touch_limit(touch: Touch, bar: int, times: pd.DatetimeIndex, params: dict) -> bool:
    limit = params["max_touch_minutes"]
    return limit is None or times[bar] - times[touch.prior_contact_start] <= pd.Timedelta(minutes=limit)


def cached_channels(high_bars: pd.DataFrame, params: dict) -> pd.DataFrame:
    settings = (params["channel_min_width"], params["channel_max_width"], params["channel_unit"], params["pip_size"])
    bars_hash = int(pd.util.hash_pandas_object(high_bars[["high", "low", "close"]], index=True).sum())
    key = (bars_hash, len(high_bars), *settings)
    if key not in CHANNEL_CACHE:
        if len(CHANNEL_CACHE) >= CHANNEL_CACHE_SIZE:
            CHANNEL_CACHE.pop(next(iter(CHANNEL_CACHE)))
        CHANNEL_CACHE[key] = build_channels(high_bars, *settings)
    return CHANNEL_CACHE[key]


def signal_row(structure, bar, side, touch, entry, reference_close, times, tick_size, params):
    """The signal for a CHoCH that uses `touch`, or None when the stop fails the side or distance checks."""
    is_long = side == "long"
    prefix = "bullish" if is_long else "bearish"
    swing = structure.iloc[bar]
    marked_price, stop_swing_time = stop_swing(swing, is_long, times, params)
    if pd.isna(marked_price):
        return None
    if is_long:
        stop = math.floor((marked_price - tick_size) / tick_size + EPSILON) * tick_size
    else:
        stop = math.ceil((marked_price + tick_size) / tick_size - EPSILON) * tick_size
    stop = round(stop, decimal_places(tick_size))
    if (stop >= entry if is_long else stop <= entry) or not stop_distance_ok(entry, stop, params):
        return None
    touch_time = times[touch.prior_contact_start]
    swing_time = swing[f"{prefix}_break_swing_time"]
    return {
        "time": times[bar],
        "side": side,
        "stop": stop,
        "entry": entry,
        "setup_time": times[touch.prior_latest_bar],
        "channel_top": touch.top,
        "channel_bottom": touch.bottom,
        "channel_role": ROLE_NAMES[channel_role(touch.top, touch.bottom, reference_close)],
        "touch_time": touch_time,
        "choch_level": swing[f"{prefix}_break_price"],
        "swing_time": swing_time,
        "stop_swing_time": stop_swing_time,
        "stop_swing_price": marked_price,
        "stop_source": params["stop_source"],
        # The trade chart opens on the earliest bar it draws.
        "plot_start_time": min(touch_time, swing_time, stop_swing_time),
    }


def stop_swing(swing, is_long, times, params):
    if params["stop_source"] == "last_swing":
        side = "low" if is_long else "high"
        return swing[f"last_swing_{side}_price"], swing[f"last_swing_{side}_time"]
    marked = "ll" if is_long else "hh"
    marked_price, confirmed_time = swing[f"last_{marked}_price"], swing[f"last_{marked}_time"]
    if pd.isna(marked_price):
        return marked_price, None
    # The marked swing is confirmed `swing_length` bars after its own bar.
    return marked_price, times[times.get_loc(confirmed_time) - params["swing_length"]]


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
    label = "swing" if trade["stop_source"] == "last_swing" else "LL" if is_long else "HH"
    fig.add_scatter(
        x=[pd.Timestamp(trade["stop_swing_time"])],
        y=[trade["stop_swing_price"]],
        mode="markers+text",
        text=[label],
        textposition="bottom center" if is_long else "top center",
        name=f"stop swing ({label})",
        marker={"color": "orange", "size": 9, "symbol": "triangle-up" if is_long else "triangle-down"},
    )


def calculate_metrics(data, signals, trades, asset, timeframe, params):
    r_key = "net_realized_r" if params["with_costs"] else "realized_r"
    metrics = {}
    for low, high, label in touch_bands(params["touch_buckets"]):
        band_r = [float(trade[r_key]) for trade in trades if low <= trade["touch_to_choch_minutes"] < high]
        metrics[f"Trades {label}"] = len(band_r)
        metrics[f"Win rate {label}"] = round(sum(r > 0 for r in band_r) / len(band_r) * 100, 2) if band_r else math.nan
        metrics[f"Avg R {label}"] = round(sum(band_r) / len(band_r), 4) if band_r else math.nan
    return metrics
