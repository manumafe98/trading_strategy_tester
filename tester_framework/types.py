from __future__ import annotations

from typing import TypedDict

import pandas as pd

from .models import AssetConfig


class VariantTask(TypedDict):
    strategy: str
    data_cache: tuple[str, str, tuple[str, ...]]
    signals: pd.DataFrame
    asset: str
    asset_cfg: AssetConfig
    timeframe: str
    execution_timeframe: str
    risk_reward_ratio: float
    exit_mode: str
    operation: str
    risk_pct: float
    capital: float
    with_costs: bool
    time_period: str
    data_source: str
    max_trades: int | None
    trade_html: bool
