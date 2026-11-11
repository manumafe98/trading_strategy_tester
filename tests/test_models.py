from __future__ import annotations

from dataclasses import replace

import pytest

from tester_framework.models import AssetConfig


def config() -> AssetConfig:
    return AssetConfig(
        ticker="TEST",
        point_value=1,
        tick_size=0.1,
        qty_step=1,
        min_qty=1,
        min_risk_ticks=1,
        spread_points=0,
        slippage_points=0,
        commission_per_side=0,
        session_timezone="UTC",
        session_start="00:00",
        bars_per_year={"1h": 252},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("point_value", 0), ("tick_size", float("nan")), ("qty_step", -1), ("spread_points", -0.1)],
)
def test_asset_config_rejects_invalid_numbers(field, value):
    with pytest.raises(ValueError):
        replace(config(), **{field: value})


def test_asset_config_rejects_invalid_session():
    with pytest.raises(ValueError):
        replace(config(), session_timezone="Mars/Olympus")
    with pytest.raises(ValueError):
        replace(config(), session_start="25:00")


def test_asset_config_rejects_invalid_annualization():
    with pytest.raises(ValueError):
        replace(config(), bars_per_year={"1h": 0})


@pytest.mark.parametrize("value", [0, 1.5, True])
def test_asset_config_rejects_invalid_min_risk_ticks(value):
    with pytest.raises(ValueError, match="min_risk_ticks"):
        replace(config(), min_risk_ticks=value)
