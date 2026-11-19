from __future__ import annotations

import re
from decimal import Decimal


def decimal_places(step: float) -> int:
    return max(0, -Decimal(str(step)).as_tuple().exponent)


def clean_exit_name(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", name.strip().lower()).strip("_")


def csv_items(value: str | None) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


def variant_parts(
    strategy: str, asset: str, high_timeframe: str, low_timeframe: str, session: str | None = None, params_label: str = ""
) -> list[str]:
    parts = [strategy, asset, high_timeframe, low_timeframe]
    if session:
        parts.append(session)
    if params_label:
        parts.append(params_label)
    return parts


def variant_count(count: int) -> str:
    return f"{count} variant" if count == 1 else f"{count} variants"


def net_value(value):
    return value[-1] if isinstance(value, (tuple, list)) else value


__all__ = ["clean_exit_name", "csv_items", "decimal_places", "net_value", "variant_count", "variant_parts"]
