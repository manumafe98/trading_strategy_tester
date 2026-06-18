from __future__ import annotations

import pandas as pd

from .settings import TIMEFRAMES


__all__ = ["load_data", "normalize_data"]


def normalize_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    if "volume" not in df:
        df["volume"] = 0
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df]
    if missing:
        raise ValueError(f"Data missing columns: {', '.join(missing)}")
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    df = df[required]
    df.index = idx
    df.index.name = "time"
    return df.dropna(subset=["open", "high", "low", "close"])


def load_data(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
    # ponytail: keep yfinance lazy so --self_check does not pay import/startup cost.
    import yfinance as yf

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
    base_interval, resample_rule = TIMEFRAMES[timeframe]
    df = yf.Ticker(ticker).history(period=period, interval=base_interval, auto_adjust=False, actions=False)
    df = normalize_data(df)
    if resample_rule:
        df = df.resample(resample_rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        df = df.dropna(subset=["open", "high", "low", "close"])
    if df.empty:
        raise ValueError(f"No data for {ticker} {timeframe} {period}")
    return df
