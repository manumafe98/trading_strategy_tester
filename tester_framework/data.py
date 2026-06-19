from __future__ import annotations

from pathlib import Path

import pandas as pd

from .settings import ROOT, TIMEFRAMES


__all__ = ["load_data", "normalize_data"]
OHLCV = ["open", "high", "low", "close", "volume"]
RESAMPLE_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
LOCAL_RESAMPLE_RULES = {
    "1m": None,
    "2m": "2min",
    "5m": "5min",
    "10m": "10min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "1d": "1D",
}


def normalize_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    if "volume" not in df:
        df["volume"] = 0
    missing = [c for c in OHLCV if c not in df]
    if missing:
        raise ValueError(f"Data missing columns: {', '.join(missing)}")
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(None)
    df = df[OHLCV]
    df.index = idx
    df.index.name = "time"
    return df.dropna(subset=["open", "high", "low", "close"]).sort_index()


def resample_data(df: pd.DataFrame, rule: str | None) -> pd.DataFrame:
    if rule:
        df = df.resample(rule).agg(RESAMPLE_AGG)
        df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def load_data(asset: str, ticker: str, timeframe: str, period: str, data_source: str = "yfinance") -> pd.DataFrame:
    if data_source == "yfinance":
        return load_yfinance_data(ticker, timeframe, period)
    if data_source == "local":
        return load_local_data(asset, timeframe, period)
    raise ValueError(f"Unknown data_source {data_source}")


def load_yfinance_data(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
    # ponytail: keep yfinance lazy so --self_check does not pay import/startup cost.
    import yfinance as yf

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
    base_interval, resample_rule = TIMEFRAMES[timeframe]
    df = yf.Ticker(ticker).history(period=period, interval=base_interval, auto_adjust=False, actions=False)
    df = normalize_data(df)
    df = resample_data(df, resample_rule)
    if df.empty:
        raise ValueError(f"No data for {ticker} {timeframe} {period}")
    return df


def load_local_data(asset: str, timeframe: str, period: str, data_dir: Path | None = None) -> pd.DataFrame:
    if timeframe not in LOCAL_RESAMPLE_RULES:
        raise ValueError(f"Unknown timeframe {timeframe}. Add it to local timeframe rules in tester_framework/data.py")
    path = local_csv_path(asset, data_dir or ROOT / "data")
    df = read_local_csv(path)
    df = filter_local_period(df, period)
    df = resample_data(df, LOCAL_RESAMPLE_RULES[timeframe])
    if df.empty:
        raise ValueError(f"No local data for {asset} {timeframe} {period}")
    return df


def local_csv_path(asset: str, data_dir: Path) -> Path:
    matches = sorted(data_dir.glob(f"*/{asset.upper()}/*.csv"))
    if not matches:
        raise FileNotFoundError(f"No local CSV for {asset} under {data_dir}")
    if len(matches) > 1:
        found = ", ".join(str(path) for path in matches)
        raise ValueError(f"Multiple local CSVs for {asset}: {found}")
    return matches[0]


def read_local_csv(path: Path) -> pd.DataFrame:
    columns = pd.read_csv(path, nrows=0).columns
    original = {str(column).lower(): column for column in columns}
    time_column = original.get("timestamp") or original.get("ts_event")
    if time_column is None:
        raise ValueError(f"{path} missing timestamp or ts_event column")
    missing = [column for column in OHLCV if column not in original]
    if missing:
        raise ValueError(f"{path} missing columns: {', '.join(missing)}")
    symbol_column = original.get("symbol")
    usecols = [time_column] + [original[column] for column in OHLCV]
    if symbol_column is not None:
        usecols.append(symbol_column)
    df = pd.read_csv(path, usecols=usecols, index_col=time_column)
    if symbol_column is not None:
        df = df.sort_values(original["volume"]).groupby(level=0).tail(1).drop(columns=[symbol_column])
    return normalize_data(df)


def filter_local_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    value = str(period).strip().lower()
    if value == "max":
        return df
    if not value.endswith("d") or not value[:-1].isdigit():
        raise ValueError("Local data only supports --time_period like 60d or max")
    latest = df.index.max()
    return df[df.index >= latest - pd.Timedelta(days=int(value[:-1]))]
