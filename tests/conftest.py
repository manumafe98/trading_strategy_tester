from __future__ import annotations

import pandas as pd
import pytest

from tester_framework.models import AssetConfig


@pytest.fixture
def test_asset_cfg() -> AssetConfig:
    return AssetConfig(
        ticker="TEST",
        point_value=1.0,
        tick_size=0.1,
        qty_step=1.0,
        min_qty=1.0,
        spread_points=0.0,
        slippage_points=0.0,
        commission_per_side=0.0,
        session_timezone="UTC",
        session_start="00:00",
        bars_per_year={"1h": 1716},
    )


@pytest.fixture
def cost_asset_cfg() -> AssetConfig:
    return AssetConfig(
        ticker="TEST",
        point_value=1.0,
        tick_size=0.1,
        qty_step=1.0,
        min_qty=1.0,
        spread_points=0.2,
        slippage_points=0.1,
        commission_per_side=0.1,
        session_timezone="UTC",
        session_start="00:00",
        bars_per_year={"1h": 1716},
    )


@pytest.fixture
def hourly_index() -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01", periods=4, freq="h")


@pytest.fixture
def base_data(hourly_index) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 100.0, 100.0],
            "low": [100.0, 99.5, 100.0, 100.0],
            "close": [100.0, 101.0, 100.0, 100.0],
            "volume": [0, 0, 0, 0],
        },
        index=hourly_index,
    )
