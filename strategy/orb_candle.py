from __future__ import annotations

import pandas as pd

from strategy.utils.orb import (
    ORB_START_MINUTE,
    TRADE_END_MINUTE,
    add_ny_columns,
    decimal_places,
    ny_session_start_utc,
    timeframe_minutes,
)


EXECUTION_TIMEFRAME = "1m"
OUTPUT_COLUMNS = ["time", "side", "stop", "entry", "reason", "plot_start_time", "orb_high", "orb_low"]


def generate_signals(df, asset, timeframe, params):
    orb_minutes = timeframe_minutes(timeframe)
    tick_size = float((params or {}).get("tick_size", 0))
    if tick_size <= 0:
        raise ValueError("orb_candle requires a positive tick_size strategy parameter")
    places = decimal_places(tick_size)
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    work = add_ny_columns(df)
    rows = []

    for day, day_data in work.groupby("_ny_date", sort=True):
        minute = day_data["_ny_minute"]
        orb = day_data[(minute >= ORB_START_MINUTE) & (minute < ORB_START_MINUTE + orb_minutes)]
        if orb.empty:
            continue

        orb_high = float(orb["high"].max())
        orb_low = float(orb["low"].min())
        candidates = day_data[(minute >= ORB_START_MINUTE + orb_minutes) & (minute < TRADE_END_MINUTE)]

        for time, bar in candidates.iterrows():
            close = float(bar["close"])
            if close > orb_high:
                rows.append(
                    {
                        "time": time,
                        "side": "long",
                        "stop": round(float(bar["low"]) - tick_size, places),
                        "entry": close,
                        "reason": "close_break_above_orb",
                        "plot_start_time": ny_session_start_utc(day),
                        "orb_high": orb_high,
                        "orb_low": orb_low,
                    }
                )
                break
            if close < orb_low:
                rows.append(
                    {
                        "time": time,
                        "side": "short",
                        "stop": round(float(bar["high"]) + tick_size, places),
                        "entry": close,
                        "reason": "close_break_below_orb",
                        "plot_start_time": ny_session_start_utc(day),
                        "orb_high": orb_high,
                        "orb_low": orb_low,
                    }
                )
                break

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values("time") if rows else pd.DataFrame(columns=OUTPUT_COLUMNS)


def plot_indicators(fig, data, view, asset, timeframe, params):
    trade = (params or {}).get("trade") or {}
    required = ("plot_start_time", "signal_time", "orb_high", "orb_low")
    if not all(key in trade for key in required):
        return

    start = pd.Timestamp(trade["plot_start_time"])
    end = pd.Timestamp(trade["signal_time"])
    fig.add_trace(
        {
            "type": "scatter",
            "mode": "lines",
            "x": [start, end],
            "y": [trade["orb_high"], trade["orb_high"]],
            "name": "ORB high",
            "line": {"color": "#26a69a", "width": 2},
        }
    )
    fig.add_trace(
        {
            "type": "scatter",
            "mode": "lines",
            "x": [start, end],
            "y": [trade["orb_low"], trade["orb_low"]],
            "name": "ORB low",
            "line": {"color": "#ef5350", "width": 2},
        }
    )
