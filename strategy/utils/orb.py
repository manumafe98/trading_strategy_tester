from __future__ import annotations

from decimal import Decimal

from tester_framework.sessions import SessionSpec, add_session_columns, session_start_utc


REQUIRED_SESSION_MESSAGE = "ORB strategies require --sessions; none is not supported."
TIMEFRAME_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}


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


def decimal_places(step: float) -> int:
    return max(0, -Decimal(str(step)).as_tuple().exponent)


__all__ = [
    "REQUIRED_SESSION_MESSAGE",
    "TIMEFRAME_MINUTES",
    "add_session_columns",
    "decimal_places",
    "session_params",
    "session_start_utc",
    "timeframe_minutes",
]
