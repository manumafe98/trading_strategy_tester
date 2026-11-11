from __future__ import annotations

import pandas as pd

from strategy.utils.fvg import find_qualifying_fvg, track_fvgs_to_bar
from strategy.utils.orb import (
    decimal_places,
    find_breakouts,
    ORB_PLOT_KEYS,
    plot_opening_range,
    REQUIRED_SESSION_MESSAGE,
    session_params,
    signals_frame,
)


EXECUTION_TIMEFRAME = "1m"
FVG_BOX_BARS = 22
REQUIRED_FLAGS = {"sessions": REQUIRED_SESSION_MESSAGE}
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
    "fvg_form_time",
]


def generate_signals(df, asset, timeframe, params):
    session, orb_minutes, start_minute, end_minute = session_params(params, timeframe, "orb_fvg")
    tick_size = float((params or {}).get("tick_size", 0))
    if tick_size <= 0:
        raise ValueError("orb_fvg requires a positive tick_size strategy parameter")
    places = decimal_places(tick_size)

    rows = []
    for breakout in find_breakouts(df, session, orb_minutes, start_minute, end_minute):
        fvgs = track_fvgs_to_bar(
            breakout.day_data,
            breakout.position,
            minute_col="_session_minute",
            fvg_start=start_minute,
            fvg_end=end_minute,
        )
        fvg = find_qualifying_fvg(fvgs, 1 if breakout.is_long else -1, require_ext=False)
        if fvg is None:
            continue

        if breakout.is_long:
            stop = round(fvg.bottom - tick_size, places)
            reason = "close_break_above_orb_fvg_cover"
        else:
            stop = round(fvg.top + tick_size, places)
            reason = "close_break_below_orb_fvg_cover"

        rows.append(
            {
                **breakout.row(stop, reason),
                "fvg_top": fvg.top,
                "fvg_bottom": fvg.bottom,
                "fvg_form_time": breakout.day_data.index[max(0, fvg.bar_index - 2)],
            }
        )
    return signals_frame(rows, OUTPUT_COLUMNS)


def plot_indicators(fig, data, view, asset, timeframe, params):
    trade = (params or {}).get("trade") or {}
    required = (*ORB_PLOT_KEYS, "fvg_top", "fvg_bottom", "fvg_form_time")
    if not all(key in trade for key in required):
        return

    plot_opening_range(fig, trade)

    form_time = pd.Timestamp(trade["fvg_form_time"])
    box_end = form_time + pd.Timedelta(minutes=FVG_BOX_BARS)
    is_bull = trade["side"] == "long"
    color = "rgba(0,200,0,0.15)" if is_bull else "rgba(255,0,0,0.15)"
    line_color = "green" if is_bull else "red"
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
