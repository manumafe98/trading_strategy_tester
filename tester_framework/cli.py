from __future__ import annotations

import argparse
from dataclasses import dataclass


DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _calendar_values(value: str, names: tuple[str, ...], flag: str, offset: int = 0) -> tuple[int, ...]:
    aliases = {alias: index + offset for index, name in enumerate(names) for alias in (name.lower(), name[:3].lower())}
    values = []
    for item in str(value).split(","):
        key = item.strip().lower()
        if key not in aliases:
            raise argparse.ArgumentTypeError(f"{flag} contains unknown value: {item.strip() or '<empty>'}")
        if aliases[key] not in values:
            values.append(aliases[key])
    return tuple(values)


def days_arg(value: str) -> tuple[int, ...]:
    return _calendar_values(value, DAY_NAMES, "--days")


def months_arg(value: str) -> tuple[int, ...]:
    return _calendar_values(value, MONTH_NAMES, "--months", 1)


@dataclass(frozen=True)
class RunConfig:
    strategies: tuple[str, ...]
    asset: str | None
    timeframe: str | None
    sessions: str | None
    time_period: str
    data_source: str
    operation: str
    risk_reward_ratio: str
    exit_mode: str
    risk: str
    capital: float
    with_costs: bool
    workers: int | None
    max_trades: int | None = None
    trade_html: int | None = None
    days: tuple[int, ...] = ()
    months: tuple[int, ...] = ()
