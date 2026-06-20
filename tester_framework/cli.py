from __future__ import annotations

from dataclasses import dataclass


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
    trade_html: bool = True
