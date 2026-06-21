from __future__ import annotations

from typing import TypedDict

from .models import AssetConfig
from .sessions import SessionSpec


class VariantBatchTask(TypedDict):
    strategy: str
    data_cache: tuple[str, str, tuple[str, ...]]
    variants: tuple[tuple[float, str], ...]
    asset: str
    asset_cfg: AssetConfig
    timeframe: str
    execution_timeframe: str
    operation: str
    risk_pct: float
    capital: float
    with_costs: bool
    time_period: str
    data_source: str
    max_trades: int | None
    trade_html: int | None
    session: SessionSpec | None
    days: tuple[int, ...]
    months: tuple[int, ...]
