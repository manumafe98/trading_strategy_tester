from __future__ import annotations

import json
from dataclasses import replace

import pytest

from tester_framework import settings
from tester_framework.models import AssetConfig
from tester_framework.settings import TIMEFRAMES, load_assets


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


def test_configured_assets_have_complete_annualization():
    assets = load_assets()
    assert all(set(asset.bars_per_year) >= set(TIMEFRAMES) for asset in assets.values())
    assert assets["BTCUSD"].bars_per_year["1h"] == 8760
    assert assets["EURUSD"].bars_per_year["1h"] == 6048
    assert assets["MGC"].bars_per_year["1h"] == 5796


def test_configured_assets_define_min_risk_ticks():
    assets = load_assets()
    assert {name: asset.min_risk_ticks for name, asset in assets.items()} == {
        "MNQ": 4, "MES": 4, "MGC": 4, "EURUSD": 1, "GBPUSD": 1, "BTCUSD": 1,
    }


def test_load_assets_fails_when_min_risk_ticks_missing(tmp_path, monkeypatch):
    raw = json.loads((settings.CONFIG_DIR / "assets.json").read_text(encoding="utf-8"))
    del raw["MNQ"]["min_risk_ticks"]
    (tmp_path / "assets.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    with pytest.raises(ValueError, match="MNQ missing asset config keys: min_risk_ticks"):
        load_assets()


@pytest.mark.parametrize("value", [0, 1.5, True])
def test_asset_config_rejects_invalid_min_risk_ticks(value):
    with pytest.raises(ValueError, match="min_risk_ticks"):
        replace(config(), min_risk_ticks=value)
