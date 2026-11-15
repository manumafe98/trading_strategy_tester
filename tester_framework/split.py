from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .data import session_labels
from .models import AssetConfig
from .settings import OOS_FINANCIAL_COLUMNS
from .utils import net_value


__all__ = [
    "SELECT_BY",
    "Split",
    "entered_in_sample",
    "oos_cutoff",
    "rank_variants",
    "split_tags",
    "validate_selection_args",
    "validate_split_args",
    "walk_forward_efficiency",
]

# --select_by choices and the in-sample result column each one ranks on.
SELECT_BY = {"return_dd": "Return / DD", "sharpe": "Sharpe Ratio", "return": "Return"}


@dataclass(frozen=True)
class Split:
    """The cutoff of one asset and execution timeframe, with the bar times either side of it."""

    asset: str
    execution_timeframe: str
    cutoff: int
    bar_count: int
    in_sample: tuple[pd.Timestamp, pd.Timestamp]
    out_of_sample: tuple[pd.Timestamp, pd.Timestamp]

    @classmethod
    def from_index(cls, asset: str, execution_timeframe: str, index: pd.DatetimeIndex, cutoff: int) -> Split:
        return cls(asset, execution_timeframe, cutoff, len(index), (index[0], index[cutoff - 1]), (index[cutoff], index[-1]))


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
    labels = session_labels(index, asset_cfg.session_timezone, asset_cfg.session_start)
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


def entered_in_sample(trade: dict, cutoff: int) -> bool:
    """Trades belong to the period of their entry bar."""
    return trade["entry_i"] < cutoff


def split_tags(split: Split) -> tuple[str, str]:
    def period(name: str, bounds: tuple[pd.Timestamp, pd.Timestamp]) -> str:
        start, end = (pd.Timestamp(value).strftime("%Y-%m-%d %H:%M") for value in bounds)
        return f"{split.asset} {split.execution_timeframe} {name} {start} to {end} UTC"

    return period("in-sample", split.in_sample), period("out-of-sample", split.out_of_sample)


def validate_selection_args(select_by: str, select_top: int) -> None:
    if select_by not in SELECT_BY:
        raise ValueError(f"--select_by must be one of {', '.join(SELECT_BY)}, got: {select_by}")
    if isinstance(select_top, bool) or not isinstance(select_top, int) or select_top < 1:
        raise ValueError("--select_top must be a positive integer")


def walk_forward_efficiency(curve: np.ndarray, cutoff: int, bars_per_year: int) -> float:
    """Out-of-sample annualized return as a percentage of in-sample annualized return.

    NaN when the in-sample annualized return is not positive, or when equity at the curve start or the cutoff is not.
    """
    curve = np.asarray(curve, dtype="float64")
    bar_count = len(curve) - 1
    if not 0 < cutoff < bar_count:
        raise ValueError(f"WFE cutoff must be between 1 and {bar_count - 1}, got: {cutoff}")
    if not (curve[0] > 0 and curve[cutoff] > 0):
        return float("nan")

    def annualized(start: float, end: float, bars: int) -> float:
        return (end / start) ** (bars_per_year / bars) - 1

    in_sample = annualized(curve[0], curve[cutoff], cutoff)
    if not in_sample > 0:
        return float("nan")
    return annualized(curve[cutoff], curve[-1], len(curve) - 1 - cutoff) / in_sample * 100


def rank_variants(table: pd.DataFrame, select_by: str, select_top: int) -> pd.DataFrame:
    """Add IS Rank, OOS Rank and Selected per strategy and asset, and sort by IS Rank within each group.

    Higher is better, NaN ranks last, ties keep the incoming order. Gross / Net pairs rank on Net.
    """
    validate_selection_args(select_by, select_top)
    column = SELECT_BY[select_by]
    oos_column = next(oos for oos, source in OOS_FINANCIAL_COLUMNS.items() if source == column)
    table = table.copy()
    groups = ["Strategy", "Asset"]

    def rank(source: str) -> pd.Series:
        values = table[source].map(lambda value: float(net_value(value)))
        ordered = values.sort_values(ascending=False, na_position="last", kind="stable")
        return table.loc[ordered.index].groupby(groups, sort=False).cumcount().add(1).reindex(table.index)

    table["IS Rank"] = rank(column)
    table["OOS Rank"] = rank(oos_column)
    table["Selected"] = table["IS Rank"] <= select_top
    # Groups keep their incoming order; rows sort by IS Rank inside each group.
    group_order = table.groupby(groups, sort=False).ngroup()
    return table.assign(_group=group_order).sort_values(["_group", "IS Rank"], kind="stable").drop(columns="_group")
