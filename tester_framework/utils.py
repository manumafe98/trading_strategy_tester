from __future__ import annotations

import re
from decimal import Decimal


def decimal_places(step: float) -> int:
    return max(0, -Decimal(str(step)).as_tuple().exponent)


def clean_exit_name(name: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "_", name.strip().lower()).strip("_")


def csv_items(value: str | None) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


__all__ = ["clean_exit_name", "csv_items", "decimal_places"]
