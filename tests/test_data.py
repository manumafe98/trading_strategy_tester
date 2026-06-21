from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from tester_framework.data import load_local_data, load_yfinance_data
from tester_framework.models import AssetConfig


def test_timestamp_local_resample():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        forex_dir = data_dir / "forex" / "TEST"
        forex_dir.mkdir(parents=True)
        (forex_dir / "TEST.csv").write_text(
            "\n".join(
                [
                    "timestamp,open,high,low,close,volume",
                    "2024-12-30T00:00:00.000Z,1,2,0,1.5,1",
                    "2025-01-01T00:00:00.000Z,10,11,9,10.5,1",
                    "2025-01-01T00:01:00.000Z,20,22,18,21,2",
                    "2025-01-01T00:02:00.000Z,30,33,28,32,3",
                    "2025-01-01T00:03:00.000Z,40,44,38,43,4",
                ]
            ),
            encoding="utf-8",
        )
        local = load_local_data("TEST", "2m", "1d", data_dir=data_dir)
        assert len(local) == 2
        assert local.iloc[0]["open"] == 10
        assert local.iloc[0]["close"] == 21
        assert local.iloc[0]["volume"] == 3
        assert getattr(local.index, "tz", None) is None


def test_ts_event_local_parsing():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        futures_dir = data_dir / "futures" / "FUT"
        futures_dir.mkdir(parents=True)
        (futures_dir / "FUT.csv").write_text(
            "\n".join(
                [
                    "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol",
                    "2025-01-01T00:00:00.000000000Z,33,1,1,100,101,99,100.5,10,FUTZ5",
                    "2025-01-01T00:00:00.000000000Z,33,1,2,200,201,199,200.5,5,FUTH6",
                    "2025-01-02T00:00:00.000000000Z,33,1,1,101,102,100,101.5,1,FUTZ5",
                    "2025-01-02T00:00:00.000000000Z,33,1,2,201,202,200,201.5,20,FUTH6",
                    "2025-01-02T00:01:00.000000000Z,33,1,1,102,103,101,102.5,1,FUTZ5",
                    "2025-01-02T00:01:00.000000000Z,33,1,2,202,203,201,202.5,20,FUTH6",
                ]
            ),
            encoding="utf-8",
        )
        local = load_local_data("FUT", "1m", "max", data_dir=data_dir)
        assert len(local) == 2
        assert local.index.name == "time"
        assert list(local["close"]) == [101.5, 102.5]


def test_mixed_case_local_columns():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        path = data_dir / "forex" / "TEST"
        path.mkdir(parents=True)
        (path / "TEST.csv").write_text(
            "Timestamp,Open,High,Low,Close,Volume\n2025-01-01T00:00:00Z,1,2,0,1.5,1\n",
            encoding="utf-8",
        )
        local = load_local_data("TEST", "1m", "max", data_dir=data_dir)
        assert local.iloc[0]["close"] == 1.5


def test_daily_resample_uses_session_boundary_across_dst():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        path = data_dir / "forex" / "TEST"
        path.mkdir(parents=True)
        (path / "TEST.csv").write_text(
            "timestamp,open,high,low,close,volume\n"
            "2025-03-08T22:00:00Z,1,2,0,1.5,1\n"
            "2025-03-09T21:00:00Z,2,3,1,2.5,1\n",
            encoding="utf-8",
        )
        cfg = AssetConfig(
            ticker="TEST", point_value=1, tick_size=0.00001, qty_step=1, min_qty=1,
            spread_points=0, slippage_points=0, commission_per_side=0,
            session_timezone="America/New_York", session_start="17:00", bars_per_year={"1d": 252},
        )
        local = load_local_data("TEST", "1d", "max", data_dir=data_dir, asset_cfg=cfg)
        assert list(local.index) == [pd.Timestamp("2025-03-08 22:00:00"), pd.Timestamp("2025-03-09 21:00:00")]


def test_calendar_period_filters_local_data_with_inclusive_year_range():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        path = data_dir / "forex" / "TEST"
        path.mkdir(parents=True)
        (path / "TEST.csv").write_text(
            "timestamp,open,high,low,close,volume\n"
            "2019-12-31T23:00:00Z,1,2,0,1.5,1\n"
            "2020-01-01T00:00:00Z,2,3,1,2.5,1\n"
            "2021-12-31T23:00:00Z,3,4,2,3.5,1\n"
            "2022-01-01T00:00:00Z,4,5,3,4.5,1\n",
            encoding="utf-8",
        )

        local = load_local_data("TEST", "1m", "2020-2021", data_dir=data_dir)

        assert list(local.index) == [pd.Timestamp("2020-01-01"), pd.Timestamp("2021-12-31 23:00:00")]


def test_yfinance_calendar_period_uses_dates_and_rolling_period_is_unchanged(monkeypatch):
    calls = []
    frame = pd.DataFrame(
        {"open": [1], "high": [2], "low": [0], "close": [1.5], "volume": [1]},
        index=pd.DatetimeIndex(["2021-01-04"]),
    )

    class FakeTicker:
        def __init__(self, _ticker):
            pass

        def history(self, **kwargs):
            calls.append(kwargs)
            return frame

    monkeypatch.setattr("yfinance.Ticker", FakeTicker)

    load_yfinance_data("TEST", "1d", "2021")
    load_yfinance_data("TEST", "1d", "2020-2021")
    load_yfinance_data("TEST", "1d", "60d")

    assert calls[0]["start"] == "2021-01-01"
    assert calls[0]["end"] == "2022-01-01"
    assert "period" not in calls[0]
    assert calls[1]["start"] == "2020-01-01"
    assert calls[1]["end"] == "2022-01-01"
    assert calls[2]["period"] == "60d"
    assert "start" not in calls[2]
    assert "end" not in calls[2]


def test_unsupported_local_period_rejected():
    with TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        futures_dir = data_dir / "futures" / "FUT"
        futures_dir.mkdir(parents=True)
        (futures_dir / "FUT.csv").write_text(
            "ts_event,open,high,low,close,volume\n2025-01-01T00:00:00Z,1,2,0,1.5,1\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_local_data("FUT", "1m", "60x", data_dir=data_dir)
