from __future__ import annotations

from strategy.utils.orb import (
    decimal_places,
    find_breakouts,
    OPENING_RANGE_PLOT_KEYS,
    plot_opening_range,
    REQUIRED_SESSION_MESSAGE,
    session_params,
    signals_frame,
)


EXECUTION_TIMEFRAME = "1m"
OUTPUT_COLUMNS = ["time", "side", "stop", "entry", "reason", "plot_start_time", "orb_high", "orb_low"]
REQUIRED_FLAGS = {"sessions": REQUIRED_SESSION_MESSAGE}


def generate_signals(df, asset, timeframe, params):
    session, orb_minutes, start_minute, end_minute = session_params(params, timeframe, "orb_candle")
    tick_size = float((params or {}).get("tick_size", 0))
    if tick_size <= 0:
        raise ValueError("orb_candle requires a positive tick_size strategy parameter")
    places = decimal_places(tick_size)

    rows = []
    for breakout in find_breakouts(df, session, orb_minutes, start_minute, end_minute):
        if breakout.is_long:
            stop = round(breakout.bar("low") - tick_size, places)
            reason = "close_break_above_orb"
        else:
            stop = round(breakout.bar("high") + tick_size, places)
            reason = "close_break_below_orb"
        rows.append(breakout.row(stop, reason))
    return signals_frame(rows, OUTPUT_COLUMNS)


def plot_indicators(fig, data, view, asset, timeframe, params):
    trade = (params or {}).get("trade") or {}
    if all(key in trade for key in OPENING_RANGE_PLOT_KEYS):
        plot_opening_range(fig, trade)
