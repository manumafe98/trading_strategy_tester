from __future__ import annotations

import json

import pytest

from tester_framework import settings
from tester_framework.settings import TIMEFRAMES, load_assets


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
