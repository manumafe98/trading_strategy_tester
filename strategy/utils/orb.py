from __future__ import annotations

from decimal import Decimal

import pandas as pd


NY = "America/New_York"
ORB_START_MINUTE = 570
TRADE_END_MINUTE = 720
TIMEFRAME_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}


def timeframe_minutes(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MINUTES:
        supported = ", ".join(TIMEFRAME_MINUTES)
        raise ValueError(f"ORB strategies support intraday timeframes only: {supported}")
    return TIMEFRAME_MINUTES[timeframe]


def ny_index(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert(NY)


def ny_session_start_utc(day) -> pd.Timestamp:
    return (
        pd.Timestamp(year=day.year, month=day.month, day=day.day, hour=9, minute=30, tz=NY)
        .tz_convert("UTC")
        .tz_localize(None)
    )


def add_ny_columns(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    ny = ny_index(work.index)
    work["_ny_date"] = ny.date
    work["_ny_minute"] = ny.hour * 60 + ny.minute
    return work


def decimal_places(step: float) -> int:
    return max(0, -Decimal(str(step)).as_tuple().exponent)


__all__ = [
    "NY",
    "ORB_START_MINUTE",
    "TRADE_END_MINUTE",
    "TIMEFRAME_MINUTES",
    "add_ny_columns",
    "decimal_places",
    "ny_index",
    "ny_session_start_utc",
    "timeframe_minutes",
]
