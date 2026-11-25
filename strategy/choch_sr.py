from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategy.utils.market_structure import market_structure, one_tick_beyond
from strategy.utils.sr_channels import EXPOSED_CHANNELS, build_channels, validate_widths
from tester_framework.data import LOCAL_RESAMPLE_RULES, resample_data


LOW_TIMEFRAME = "1m"
PARAMS = {
    "swing_length": 20,
    "channel_min_width": 0.1,
    "channel_max_width": 0.4,
    "channel_unit": "%",
    "stop_source": "last_ll_hh",
    "entry_mode": "close",
    "max_touch_minutes": None,
    "touch_buckets": "0-5|5-15|15-30|30-60|60+",
}
PARAM_TYPES = {"max_touch_minutes": int}
STOP_SOURCES = {
    "last_ll_hh": {"columns": ("ll", "hh"), "labels": ("LL", "HH"), "dated_by_confirmation_bar": True},
    "last_swing": {"columns": ("swing_low", "swing_high"), "labels": ("swing", "swing"), "dated_by_confirmation_bar": False},
}
ENTRY_MODES = {
    "close": {"confirmation": "close", "choch_column": "choch", "stop_entry": False},
    "break": {"confirmation": "wick", "choch_column": "choch_at_open", "stop_entry": True},
}
CHANNEL_CACHE: dict[tuple, pd.DataFrame] = {}
CHANNEL_CACHE_SIZE = 32
SUPPORT, RESISTANCE = 1, -1
ROLE_NAMES = {SUPPORT: "support", RESISTANCE: "resistance", 0: "in-price"}
OUTPUT_COLUMNS = [
    "time",
    "side",
    "stop",
    "entry",
    "stop_entry",
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
    "entry_mode",
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
    names = ("channel_min_width", "channel_max_width", "channel_unit")
    validate_widths(*(params[name] for name in names), names=names)
    for name, choices in (("stop_source", STOP_SOURCES), ("entry_mode", ENTRY_MODES)):
        validate_choice(name, params[name], choices)
    if params["max_touch_minutes"] is not None and params["max_touch_minutes"] < 1:
        raise ValueError("max_touch_minutes must be at least 1 or none")
    parse_touch_buckets(params["touch_buckets"])


def validate_choice(name: str, value, choices) -> None:
    if value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(choices)}, got {value}")


def parse_touch_buckets(text: str) -> list[tuple[float, float, str]]:
    buckets = []
    previous_high = 0.0
    for bucket in text.split("|"):
        bucket = bucket.strip()
        try:
            if bucket.endswith("+"):
                low, high = float(bucket[:-1]), math.inf
            else:
                low_text, _, high_text = bucket.partition("-")
                low, high = float(low_text), float(high_text)
        except ValueError:
            raise ValueError(f"touch_buckets band {bucket!r} must look like 5-15 or 60+") from None
        if low != previous_high or not high > low:
            raise ValueError(
                "touch_buckets must be increasing bands from 0 with no gaps or overlaps, open-ended only at the end"
            )
        label = f"{low:g}+m" if high == math.inf else f"{low:g}-{high:g}m"
        buckets.append((low, high, label))
        previous_high = high
    return buckets


class Touch:
    def __init__(self, top: float, bottom: float, role: int, bar: int):
        self.top, self.bottom, self.role = top, bottom, role
        self.first_bar = self.contact_start = self.latest_retest_bar = bar
        self.prior_contact_start = self.prior_latest_retest_bar = bar
        self.in_contact = True

    def update_contact(self, bar: int, overlaps: bool, holds_role: bool) -> None:
        self.prior_contact_start, self.prior_latest_retest_bar = self.contact_start, self.latest_retest_bar
        if overlaps:
            if not self.in_contact:
                self.contact_start = bar
            if holds_role:
                self.latest_retest_bar = bar
        self.in_contact = overlaps

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2


def is_channel(top: float, bottom: float) -> bool:
    return not (math.isnan(top) or math.isnan(bottom) or top == 0 or bottom == 0)


def channel_role(top: float, bottom: float, reference_close: float) -> int:
    if not is_channel(top, bottom):
        return 0
    return SUPPORT if reference_close > top else RESISTANCE if reference_close < bottom else 0


def match_distance(touch: Touch, top: float, bottom: float, tick_size: float) -> float:
    allowed = ((touch.top - touch.bottom) + (top - bottom)) / 2 + tick_size
    distance = abs((top + bottom) / 2 - touch.midpoint)
    return distance if distance <= allowed else math.inf


def nearest_channel(touch: Touch, channels: list[tuple[float, float]], tick_size: float) -> tuple[float, float] | None:
    best, best_distance = None, math.inf
    for top, bottom in channels:
        distance = match_distance(touch, top, bottom, tick_size)
        if distance < best_distance:
            best, best_distance = (top, bottom), distance
    return best


def generate_signals(df, asset, timeframe, params):
    validate_params(params)
    tick_size = float(params.get("tick_size", 0))
    if tick_size <= 0:
        raise ValueError("choch_sr requires a positive tick_size strategy parameter")
    if timeframe not in LOCAL_RESAMPLE_RULES or timeframe == "1d":
        raise ValueError(f"choch_sr supports intraday high timeframes only, got {timeframe}")
    rule = LOCAL_RESAMPLE_RULES[timeframe] or "1min"
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows = []
    for segment in contract_segments(df):
        rows.extend(segment_signals(segment, rule, tick_size, params))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS) if rows else pd.DataFrame(columns=OUTPUT_COLUMNS)


def contract_segments(df: pd.DataFrame) -> list[pd.DataFrame]:
    if "contract" not in df:
        return [df]
    contracts = df["contract"]
    segment_ids = (contracts != contracts.shift()).cumsum()
    return [segment for _, segment in df.groupby(segment_ids, sort=False)]


def segment_signals(df, rule, tick_size, params):
    high_bars = resample_data(df, rule)
    channels = cached_channels(high_bars, params)
    tops = channels[[f"top_{slot + 1}" for slot in range(EXPOSED_CHANNELS)]].to_numpy()
    bottoms = channels[[f"bottom_{slot + 1}" for slot in range(EXPOSED_CHANNELS)]].to_numpy()
    reference_closes = channels["reference_close"].to_numpy()
    period_starts = df.index.floor(rule)
    high_rows = high_bars.index.get_indexer(period_starts)
    new_period = np.r_[True, period_starts[1:] != period_starts[:-1]]

    entry_mode = ENTRY_MODES[params["entry_mode"]]
    structure = market_structure(df, params["swing_length"], entry_mode["confirmation"], tick_size)
    bullish_choch = structure[f"bullish_{entry_mode['choch_column']}"].to_numpy()
    bearish_choch = structure[f"bearish_{entry_mode['choch_column']}"].to_numpy()
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)
    times = df.index

    touches: list[Touch] = []
    range_high = range_low = math.nan
    rows = []
    for bar in range(len(df)):
        if new_period[bar]:
            range_high, range_low = highs[bar], lows[bar]
        else:
            range_high, range_low = max(range_high, highs[bar]), min(range_low, lows[bar])

        high_row = high_rows[bar]
        reference_close = reference_closes[high_row]
        in_force = [(top, bottom) for top, bottom in zip(tops[high_row], bottoms[high_row]) if is_channel(top, bottom)]

        kept = []
        for touch in touches:
            channel = nearest_channel(touch, in_force, tick_size)
            if channel is None:
                continue
            role = channel_role(*channel, reference_close)
            if role != 0 and role != touch.role:
                continue
            touch.top, touch.bottom = channel
            touch.update_contact(bar, range_high >= touch.bottom and range_low <= touch.top, role == touch.role)
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
            usable = [
                t for t in touches if t.role == role and t.first_bar < bar and within_touch_limit(t, bar, times, params)
            ]
            if usable:
                touch = max(usable, key=lambda t: t.prior_latest_retest_bar)
                row = signal_row(
                    structure, bar, side, touch, closes[bar], reference_close, times, tick_size, params
                )
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
    is_long = side == "long"
    prefix = "bullish" if is_long else "bearish"
    swing = structure.iloc[bar]
    is_stop_entry = ENTRY_MODES[params["entry_mode"]]["stop_entry"]
    swings_before_bar = structure.iloc[bar - 1] if is_stop_entry else swing
    stop_swing_price, stop_swing_time = stop_swing(swings_before_bar, is_long, times, params)
    if pd.isna(stop_swing_price):
        return None
    stop = one_tick_beyond(stop_swing_price, not is_long, tick_size)
    if is_stop_entry:
        entry = one_tick_beyond(swing[f"{prefix}_break_price"], is_long, tick_size)
    if stop >= entry if is_long else stop <= entry:
        return None
    touch_time = times[touch.prior_contact_start]
    swing_time = swing[f"{prefix}_break_swing_time"]
    return {
        "time": times[bar],
        "side": side,
        "stop": stop,
        "entry": math.nan if is_stop_entry else entry,
        "stop_entry": entry if is_stop_entry else math.nan,
        "setup_time": times[touch.prior_latest_retest_bar],
        "channel_top": touch.top,
        "channel_bottom": touch.bottom,
        "channel_role": ROLE_NAMES[channel_role(touch.top, touch.bottom, reference_close)],
        "touch_time": touch_time,
        "choch_level": swing[f"{prefix}_break_price"],
        "swing_time": swing_time,
        "stop_swing_time": stop_swing_time,
        "stop_swing_price": stop_swing_price,
        "stop_source": params["stop_source"],
        "entry_mode": params["entry_mode"],
        "plot_start_time": min(touch_time, swing_time, stop_swing_time),
    }


def stop_swing(swing, is_long, times, params):
    source = STOP_SOURCES[params["stop_source"]]
    column = source["columns"][0 if is_long else 1]
    price, time = swing[f"last_{column}_price"], swing[f"last_{column}_time"]
    if pd.isna(price):
        return price, None
    if source["dated_by_confirmation_bar"]:
        time = times[times.get_loc(time) - params["swing_length"]]
    return price, time


def plot_indicators(fig, data, view, asset, timeframe, params):
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
    label = STOP_SOURCES[trade["stop_source"]]["labels"][0 if is_long else 1]
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
    for low, high, label in parse_touch_buckets(params["touch_buckets"]):
        bucket_r = [float(trade[r_key]) for trade in trades if low <= trade["touch_to_choch_minutes"] < high]
        metrics[f"Trades {label}"] = len(bucket_r)
        metrics[f"Win rate {label}"] = round(sum(r > 0 for r in bucket_r) / len(bucket_r) * 100, 2) if bucket_r else math.nan
        metrics[f"Avg R {label}"] = round(sum(bucket_r) / len(bucket_r), 4) if bucket_r else math.nan
    return metrics
