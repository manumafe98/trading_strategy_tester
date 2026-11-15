from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .data import _session_labels
from .models import AssetConfig


__all__ = ["Split", "oos_cutoff", "split_tags", "validate_split_args"]


@dataclass(frozen=True)
class Split:
    """The cutoff of one asset and execution timeframe, with the bar times either side of it."""

    asset: str
    timeframe: str
    cutoff: int
    bar_count: int
    in_sample: tuple[pd.Timestamp, pd.Timestamp]
    out_of_sample: tuple[pd.Timestamp, pd.Timestamp]

    @classmethod
    def from_index(cls, asset: str, timeframe: str, index: pd.DatetimeIndex, cutoff: int) -> Split:
        return cls(asset, timeframe, cutoff, len(index), (index[0], index[cutoff - 1]), (index[cutoff], index[-1]))


def validate_split_args(oos_start: str | None, oos_fraction: float | None) -> None:
    if oos_start is not None and oos_fraction is not None:
        raise ValueError("Use --oos_start or --oos_fraction, not both")
    if oos_start is not None:
        try:
            date.fromisoformat(oos_start)
        except (TypeError, ValueError):
            raise ValueError(f"--oos_start must be a YYYY-MM-DD date, got: {oos_start}") from None
    if oos_fraction is not None and not (
        isinstance(oos_fraction, (int, float)) and not isinstance(oos_fraction, bool) and 0 < oos_fraction < 1
    ):
        raise ValueError(f"--oos_fraction must be between 0 and 1 (exclusive), got: {oos_fraction}")


def oos_cutoff(
    index: pd.DatetimeIndex,
    asset_cfg: AssetConfig,
    *,
    oos_start: str | None = None,
    oos_fraction: float | None = None,
) -> int:
    """Return the position of the first out-of-sample bar, always the first bar of a market session."""
    if oos_start is None and oos_fraction is None:
        raise ValueError("A split needs --oos_start or --oos_fraction")
    validate_split_args(oos_start, oos_fraction)
    labels = _session_labels(index, asset_cfg.session_timezone, asset_cfg.session_start)
    if oos_start is not None:
        # A market session label is the local date its session starts on.
        cutoff = int(labels.searchsorted(pd.Timestamp(oos_start), side="left"))
    else:
        cutoff = math.ceil((1 - oos_fraction) * len(index))
        if 0 < cutoff < len(index) and labels[cutoff] == labels[cutoff - 1]:
            cutoff = int(labels.searchsorted(labels[cutoff], side="right"))
    if cutoff <= 0:
        raise ValueError("Split cutoff leaves the in-sample period with no bars")
    if cutoff >= len(index):
        raise ValueError("Split cutoff leaves the out-of-sample period with no bars")
    return cutoff


def split_tags(split: Split) -> tuple[str, str]:
    def period(name: str, bounds: tuple[pd.Timestamp, pd.Timestamp]) -> str:
        start, end = (pd.Timestamp(value).strftime("%Y-%m-%d %H:%M") for value in bounds)
        return f"{split.asset} {split.timeframe} {name} {start} to {end} UTC"

    return period("in-sample", split.in_sample), period("out-of-sample", split.out_of_sample)
