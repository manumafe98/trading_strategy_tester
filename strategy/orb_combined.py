from __future__ import annotations

import pandas as pd

from strategy.utils.fvg import find_qualifying_fvg, track_fvgs_to_bar
from strategy.utils.orb import (
    ORB_START_MINUTE,
    TRADE_END_MINUTE,
    add_ny_columns,
    decimal_places,
    ny_session_start_utc,
    timeframe_minutes,
)


EXECUTION_TIMEFRAME = "1m"
FVG_BOX_BARS = 22
EXT_LINE_BARS = 22
OUTPUT_COLUMNS = [
    "time",
    "side",
    "stop",
    "entry",
    "reason",
    "plot_start_time",
    "orb_high",
    "orb_low",
    "fvg_top",
    "fvg_bottom",
    "fvg_ext",
    "fvg_form_time",
    "fvg_ext_time",
]


def generate_signals(df, asset, timeframe, params):
    orb_minutes = timeframe_minutes(timeframe)
    tick_size = float((params or {}).get("tick_size", 0))
    if tick_size <= 0:
        raise ValueError("orb_combined requires a positive tick_size strategy parameter")
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
        if candidates.empty:
            continue

        day_index = day_data.index
        candidate_positions = day_data.index.get_indexer(candidates.index)

        breakout_idx = None
        breakout_dir = 0
        breakout_close = 0.0

        for pos in candidate_positions:
            bar = day_data.iloc[pos]
            close = float(bar["close"])
            if close > orb_high:
                breakout_idx = pos
                breakout_dir = 1
                breakout_close = close
                break
            if close < orb_low:
                breakout_idx = pos
                breakout_dir = -1
                breakout_close = close
                break

        if breakout_idx is None:
            continue

        fvgs = track_fvgs_to_bar(day_data, breakout_idx)
        fvg = find_qualifying_fvg(fvgs, breakout_dir, require_ext=True)
        if fvg is None or fvg.ext_price is None:
            continue

        if breakout_dir == 1:
            stop = round(fvg.ext_price - tick_size, places)
            reason = "close_break_above_orb_fvg_ext_cover"
        else:
            stop = round(fvg.ext_price + tick_size, places)
            reason = "close_break_below_orb_fvg_ext_cover"

        form_idx = max(0, fvg.bar_index - 2)
        ext_idx = max(0, fvg.bar_index - 1)
        rows.append(
            {
                "time": day_index[breakout_idx],
                "side": "long" if breakout_dir == 1 else "short",
                "stop": stop,
                "entry": breakout_close,
                "reason": reason,
                "plot_start_time": ny_session_start_utc(day),
                "orb_high": orb_high,
                "orb_low": orb_low,
                "fvg_top": fvg.top,
                "fvg_bottom": fvg.bottom,
                "fvg_ext": fvg.ext_price,
                "fvg_form_time": day_index[form_idx],
                "fvg_ext_time": day_index[ext_idx],
            }
        )

    return (
        pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values("time")
        if rows
        else pd.DataFrame(columns=OUTPUT_COLUMNS)
    )


def plot_indicators(fig, data, view, asset, timeframe, params):
    trade = (params or {}).get("trade") or {}
    required = (
        "plot_start_time",
        "signal_time",
        "orb_high",
        "orb_low",
        "fvg_top",
        "fvg_bottom",
        "fvg_ext",
        "fvg_form_time",
        "fvg_ext_time",
    )
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

    is_bull = trade["side"] == "long"
    color = "rgba(0,200,0,0.15)" if is_bull else "rgba(255,0,0,0.15)"
    line_color = "green" if is_bull else "red"

    form_time = pd.Timestamp(trade["fvg_form_time"])
    box_end = form_time + pd.Timedelta(minutes=FVG_BOX_BARS)
    fig.add_shape(
        type="rect",
        x0=form_time,
        x1=box_end,
        y0=trade["fvg_bottom"],
        y1=trade["fvg_top"],
        fillcolor=color,
        line={"color": line_color, "width": 1},
        name="FVG",
    )

    ext_time = pd.Timestamp(trade["fvg_ext_time"])
    ext_end = ext_time + pd.Timedelta(minutes=EXT_LINE_BARS)
    fig.add_shape(
        type="line",
        x0=ext_time,
        x1=ext_end,
        y0=trade["fvg_ext"],
        y1=trade["fvg_ext"],
        line={"color": line_color, "width": 1, "dash": "dash"},
        name="FVG ext",
    )
