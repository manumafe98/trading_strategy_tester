from __future__ import annotations


def clean_exit_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def csv_items(value: str | None) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


__all__ = ["clean_exit_name", "csv_items"]
