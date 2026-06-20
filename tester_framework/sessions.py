from __future__ import annotations

from typing import TypedDict

import pandas as pd

from .utils import csv_items


class SessionSpec(TypedDict):
    name: str
    timezone: str
    start: str
    end: str
    start_minute: int
    end_minute: int
    label: str


SESSION_PRESETS = {
    "asia": ("Asia/Tokyo", 9 * 60, 15 * 60),
    "london": ("Europe/London", 8 * 60, 16 * 60 + 30),
    "ny": ("America/New_York", 9 * 60 + 30, 16 * 60),
}


def _parse_clock(value: str) -> int:
    hour_text, sep, minute_text = value.strip().partition(":")
    if not sep:
        raise ValueError(f"Session time must use HH:MM, got: {value}")
    try:
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        raise ValueError(f"Session time must use HH:MM, got: {value}") from None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Session time must use HH:MM, got: {value}")
    return hour * 60 + minute


def _format_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _session_spec(name: str, start_minute: int | None = None, end_minute: int | None = None) -> SessionSpec:
    if name not in SESSION_PRESETS:
        raise ValueError(f"--sessions supports only asia, london, ny, all, or none; got: {name}")
    timezone, preset_start, preset_end = SESSION_PRESETS[name]
    start_minute = preset_start if start_minute is None else start_minute
    end_minute = preset_end if end_minute is None else end_minute
    if start_minute >= end_minute:
        raise ValueError(f"--sessions {name} range must increase, got: {_format_clock(start_minute)}-{_format_clock(end_minute)}")
    if start_minute < preset_start or end_minute > preset_end:
        raise ValueError(
            f"--sessions {name} range must stay inside {_format_clock(preset_start)}-{_format_clock(preset_end)}"
        )
    full = start_minute == preset_start and end_minute == preset_end
    label = name if full else f"{name}={_format_clock(start_minute)}-{_format_clock(end_minute)}"
    return {
        "name": name,
        "timezone": timezone,
        "start": _format_clock(start_minute),
        "end": _format_clock(end_minute),
        "start_minute": start_minute,
        "end_minute": end_minute,
        "label": label,
    }


def parse_sessions(value: str | None) -> list[SessionSpec]:
    items = csv_items(value)
    if not items:
        return []
    lowered = [item.lower() for item in items]
    if "none" in lowered:
        if len(items) != 1 or items[0].lower() != "none":
            raise ValueError("--sessions none must be used by itself")
        return []

    sessions: list[SessionSpec] = []
    seen: set[tuple[str, int, int]] = set()
    for item in items:
        name, has_window, window = item.partition("=")
        key = name.strip().lower()
        if key == "all":
            if has_window:
                raise ValueError("--sessions all does not accept a custom time range")
            expanded = [_session_spec(session_name) for session_name in SESSION_PRESETS]
        else:
            if has_window:
                start_text, dash, end_text = window.partition("-")
                if not dash:
                    raise ValueError(f"--sessions {item} must use name=HH:MM-HH:MM")
                expanded = [_session_spec(key, _parse_clock(start_text), _parse_clock(end_text))]
            else:
                expanded = [_session_spec(key)]
        for session in expanded:
            session_key = (session["name"], session["start_minute"], session["end_minute"])
            if session_key in seen:
                continue
            seen.add(session_key)
            sessions.append(session)
    return sessions


def session_label(session: SessionSpec | None) -> str | None:
    return None if session is None else session["label"]


def utc_index(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(pd.to_datetime(index))
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    return idx


def local_index(index, timezone: str) -> pd.DatetimeIndex:
    return utc_index(index).tz_convert(timezone)


def add_session_columns(df: pd.DataFrame, session: SessionSpec) -> pd.DataFrame:
    work = df.copy()
    local = local_index(work.index, session["timezone"])
    work["_session_date"] = local.date
    work["_session_minute"] = local.hour * 60 + local.minute
    return work


def session_start_utc(day, session: SessionSpec) -> pd.Timestamp:
    return (
        pd.Timestamp(
            year=day.year,
            month=day.month,
            day=day.day,
            hour=session["start_minute"] // 60,
            minute=session["start_minute"] % 60,
            tz=session["timezone"],
        )
        .tz_convert("UTC")
        .tz_localize(None)
    )


def filter_signals(signals: pd.DataFrame, session: SessionSpec | None) -> pd.DataFrame:
    frame = pd.DataFrame(signals).copy()
    if session is None or frame.empty:
        return frame
    if "time" not in frame.columns:
        frame = frame.reset_index().rename(columns={frame.index.name or "index": "time"})
    local = local_index(frame["time"], session["timezone"])
    minutes = local.hour * 60 + local.minute
    mask = (minutes >= session["start_minute"]) & (minutes < session["end_minute"])
    return frame.loc[mask].copy()


__all__ = [
    "SESSION_PRESETS",
    "SessionSpec",
    "add_session_columns",
    "filter_signals",
    "local_index",
    "parse_sessions",
    "session_label",
    "session_start_utc",
    "utc_index",
]
