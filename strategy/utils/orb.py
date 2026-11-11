from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tester_framework.sessions import SessionSpec, add_session_columns, session_start_utc
from tester_framework.utils import decimal_places


REQUIRED_SESSION_MESSAGE = "ORB strategies require --sessions; none is not supported."
TIMEFRAME_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
ORB_PLOT_KEYS = ("plot_start_time", "signal_time", "orb_high", "orb_low")


def timeframe_minutes(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MINUTES:
        supported = ", ".join(TIMEFRAME_MINUTES)
        raise ValueError(f"ORB strategies support intraday timeframes only: {supported}")
    return TIMEFRAME_MINUTES[timeframe]


def session_params(params: dict | None, timeframe: str, strategy_name: str) -> tuple[SessionSpec, int, int, int]:
    orb_minutes = timeframe_minutes(timeframe)
    session = (params or {}).get("session")
    if not session:
        raise ValueError(REQUIRED_SESSION_MESSAGE)
    try:
        start_minute = int(session["start_minute"])
        end_minute = int(session["end_minute"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{strategy_name} requires a valid session strategy parameter") from exc
    if end_minute - start_minute <= orb_minutes:
        raise ValueError(f"{strategy_name} session must leave time after the {timeframe} opening range")
    return session, orb_minutes, start_minute, end_minute


@dataclass(frozen=True)
class Breakout:
    """First close outside one day's opening range; `position` indexes the bar inside `day_data`."""

    day_data: pd.DataFrame
    position: int
    is_long: bool
    entry: float
    orb_high: float
    orb_low: float
    plot_start_time: pd.Timestamp

    @property
    def time(self) -> pd.Timestamp:
        return self.day_data.index[self.position]

    def bar(self, column: str) -> float:
        return float(self.day_data[column].to_numpy(dtype=float)[self.position])

    def row(self, stop: float, reason: str) -> dict:
        return {
            "time": self.time,
            "side": "long" if self.is_long else "short",
            "stop": stop,
            "entry": self.entry,
            "reason": reason,
            "plot_start_time": self.plot_start_time,
            "orb_high": self.orb_high,
            "orb_low": self.orb_low,
        }


def find_breakouts(df: pd.DataFrame, session: SessionSpec, orb_minutes: int, start_minute: int, end_minute: int) -> list[Breakout]:
    """Per session day: in-window bars, opening range high/low, and the first close beyond it."""
    if df.empty:
        return []
    work = add_session_columns(df, session)
    minute = work["_session_minute"]
    work = work[(minute >= start_minute) & (minute < end_minute)]
    breakouts = []

    for day, day_data in work.groupby("_session_date", sort=True):
        minute = day_data["_session_minute"].to_numpy()
        in_orb = (minute >= start_minute) & (minute < start_minute + orb_minutes)
        if not in_orb.any():
            continue
        orb_high = float(day_data["high"][in_orb].max())
        orb_low = float(day_data["low"][in_orb].min())
        closes = day_data["close"].to_numpy(dtype=float)
        after_orb = (minute >= start_minute + orb_minutes) & (minute < end_minute)
        hits = np.flatnonzero(after_orb & ((closes > orb_high) | (closes < orb_low)))
        if not len(hits):
            continue
        position = int(hits[0])
        entry = float(closes[position])
        breakouts.append(
            Breakout(
                day_data=day_data,
                position=position,
                is_long=entry > orb_high,
                entry=entry,
                orb_high=orb_high,
                orb_low=orb_low,
                plot_start_time=session_start_utc(day, session),
            )
        )
    return breakouts


def signals_frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns).sort_values("time") if rows else pd.DataFrame(columns=columns)


def plot_opening_range(fig, trade: dict) -> None:
    """Draw the ORB high/low lines from the session start to the signal bar."""
    start = pd.Timestamp(trade["plot_start_time"])
    end = pd.Timestamp(trade["signal_time"])
    for key, name, color in (("orb_high", "ORB high", "#26a69a"), ("orb_low", "ORB low", "#ef5350")):
        fig.add_trace(
            {
                "type": "scatter",
                "mode": "lines",
                "x": [start, end],
                "y": [trade[key], trade[key]],
                "name": name,
                "line": {"color": color, "width": 2},
            }
        )


__all__ = [
    "Breakout",
    "ORB_PLOT_KEYS",
    "REQUIRED_SESSION_MESSAGE",
    "TIMEFRAME_MINUTES",
    "decimal_places",
    "find_breakouts",
    "plot_opening_range",
    "session_params",
    "signals_frame",
    "timeframe_minutes",
]
