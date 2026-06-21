from __future__ import annotations

from functools import lru_cache
from datetime import date, time
from pathlib import Path
import re

import pandas as pd

from .models import AssetConfig
from .settings import ROOT, TIMEFRAMES


__all__ = ["load_data", "normalize_data", "time_period_args"]
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
CALENDAR_PERIOD = re.compile(r"^(\d{4})(?:-(\d{4}))?$")
ROLLING_PERIOD = re.compile(r"^[1-9]\d*(d|wk|mo|y)$")


def time_period_args(period: str, data_source: str) -> dict[str, str]:
    if data_source not in {"yfinance", "local"}:
        raise ValueError(f"Unknown data_source {data_source}")
    value = str(period).strip().lower()
    match = CALENDAR_PERIOD.fullmatch(value)
    if match:
        start_year = int(match.group(1))
        end_year = int(match.group(2) or start_year)
        if end_year < start_year:
            raise ValueError("--time_period year range must increase")
        try:
            start = date(start_year, 1, 1)
            end = date(end_year + 1, 1, 1)
        except ValueError:
            raise ValueError("--time_period contains an unsupported year") from None
        return {"start": start.isoformat(), "end": end.isoformat()}
    if data_source == "yfinance" and (value in {"ytd", "max"} or ROLLING_PERIOD.fullmatch(value)):
        return {"period": value}
    if data_source == "local" and (value == "max" or re.fullmatch(r"[1-9]\d*(d|mo|y)", value)):
        return {"period": value}
    supported = "Nd, Nwk, Nmo, Ny, ytd, max" if data_source == "yfinance" else "Nd, Nmo, Ny, max"
    raise ValueError(f"--time_period for {data_source} must use {supported}, YYYY, or YYYY-YYYY")


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
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    invalid = (df["high"] < df[["open", "low", "close"]].max(axis=1)) | (
        df["low"] > df[["open", "high", "close"]].min(axis=1)
    )
    if invalid.any():
        raise ValueError("Data contains invalid OHLC ranges")
    return df


def _session_offset(session_start: str) -> pd.Timedelta:
    value = time.fromisoformat(session_start)
    return pd.Timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)


def _session_labels(index, timezone: str, session_start: str) -> pd.DatetimeIndex:
    local = pd.DatetimeIndex(pd.to_datetime(index, utc=True)).tz_convert(timezone).tz_localize(None)
    return (local - _session_offset(session_start)).normalize()


def resample_data(df: pd.DataFrame, rule: str | None, asset_cfg: AssetConfig | None = None) -> pd.DataFrame:
    if rule:
        if rule == "1D" and asset_cfg is not None:
            df = df.copy()
            offset = _session_offset(asset_cfg.session_start)
            df.index = (
                pd.DatetimeIndex(df.index).tz_localize("UTC").tz_convert(asset_cfg.session_timezone).tz_localize(None)
                - offset
            )
            df = df.resample(rule, closed="left", label="left").agg(RESAMPLE_AGG)
            df.index = (
                (df.index + offset)
                .tz_localize(asset_cfg.session_timezone, ambiguous="raise", nonexistent="raise")
                .tz_convert("UTC")
                .tz_localize(None)
            )
        else:
            df = df.resample(rule).agg(RESAMPLE_AGG)
        df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def load_data(asset: str, asset_cfg: AssetConfig, timeframe: str, period: str, data_source: str = "yfinance") -> pd.DataFrame:
    if data_source == "yfinance":
        return load_yfinance_data(asset_cfg.ticker, timeframe, period)
    if data_source == "local":
        return load_local_data(asset, timeframe, period, asset_cfg=asset_cfg)
    raise ValueError(f"Unknown data_source {data_source}")


def load_yfinance_data(ticker: str, timeframe: str, period: str) -> pd.DataFrame:
    # ponytail: keep yfinance lazy so tests do not pay import/startup cost.
    import yfinance as yf

    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe {timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
    base_interval, resample_rule = TIMEFRAMES[timeframe]
    period_args = time_period_args(period, "yfinance")
    try:
        df = yf.Ticker(ticker).history(interval=base_interval, auto_adjust=False, actions=False, **period_args)
    except Exception as exc:
        raise RuntimeError(f"Failed to download {ticker} {timeframe} {period} from yfinance: {exc}") from exc
    if df.empty:
        raise ValueError(f"No data for {ticker} {timeframe} {period}")
    try:
        df = normalize_data(df)
    except ValueError as exc:
        raise ValueError(f"{ticker} {timeframe}: {exc}") from exc
    df = resample_data(df, resample_rule)
    if df.empty:
        raise ValueError(f"No data for {ticker} {timeframe} {period}")
    return df


def load_local_data(
    asset: str,
    timeframe: str,
    period: str,
    data_dir: Path | None = None,
    asset_cfg: AssetConfig | None = None,
) -> pd.DataFrame:
    if timeframe not in LOCAL_RESAMPLE_RULES:
        raise ValueError(f"Unknown timeframe {timeframe}. Add it to local timeframe rules in tester_framework/data.py")
    path = local_csv_path(asset, data_dir or ROOT / "data")
    try:
        df = read_local_csv(
            path,
            asset_cfg.session_timezone if asset_cfg else "UTC",
            asset_cfg.session_start if asset_cfg else "00:00",
        )
    except ValueError as exc:
        raise ValueError(f"{asset} {timeframe}: {exc}") from exc
    rule = LOCAL_RESAMPLE_RULES[timeframe]
    if rule == "1D":
        if asset_cfg is None:
            raise ValueError("asset config is required for local daily data")
        df = filter_local_period(resample_data(df, rule, asset_cfg), period)
    else:
        df = resample_data(filter_local_period(df, period), rule, asset_cfg)
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


def _continuous_futures(df: pd.DataFrame, timezone: str, session_start: str) -> pd.DataFrame:
    work = df.copy()
    work["_session"] = _session_labels(work.index, timezone, session_start)
    volume = work.groupby(["_session", "symbol"])["volume"].sum().unstack(fill_value=0)
    available = work.groupby(["_session", "symbol"]).size().unstack(fill_value=0).gt(0)
    ranked = volume.shift(1).where(available)
    valid = ranked.notna().any(axis=1)
    chosen = ranked[valid].idxmax(axis=1).rename("_chosen")
    work = work.join(chosen, on="_session")
    work = work[work["symbol"] == work["_chosen"]].drop(columns=["symbol", "_session", "_chosen"])
    if work.index.has_duplicates:
        raise ValueError("continuous futures selection produced duplicate timestamps")
    return work


@lru_cache(maxsize=1)
def read_local_csv(path: Path, session_timezone: str = "UTC", session_start: str = "00:00") -> pd.DataFrame:
    # ponytail: asset runs are contiguous, so one cached source avoids reparsing without retaining every asset.
    try:
        columns = pd.read_csv(path, nrows=0).columns
    except pd.errors.ParserError as exc:
        raise ValueError(f"{path} is not a valid CSV: {exc}") from exc
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
    try:
        df = pd.read_csv(path, usecols=usecols, index_col=time_column)
    except pd.errors.ParserError as exc:
        raise ValueError(f"{path} is not a valid CSV: {exc}") from exc
    renames = {original[column]: column for column in OHLCV}
    if symbol_column is not None:
        renames[symbol_column] = "symbol"
    df = df.rename(columns=renames)
    for column in OHLCV:
        if not pd.api.types.is_numeric_dtype(df[column]):
            raise ValueError(f"{path} column {original[column]} is not numeric")
    if symbol_column is not None:
        df = _continuous_futures(df, session_timezone, session_start)
    return normalize_data(df)


def filter_local_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    period_args = time_period_args(period, "local")
    if "start" in period_args:
        start = pd.Timestamp(period_args["start"])
        end = pd.Timestamp(period_args["end"])
        return df[(df.index >= start) & (df.index < end)]
    value = period_args["period"]
    if value == "max":
        return df
    if value.endswith("d") and value[:-1].isdigit():
        delta = pd.Timedelta(days=int(value[:-1]))
    elif value.endswith("mo") and value[:-2].isdigit():
        delta = pd.DateOffset(months=int(value[:-2]))
    elif value.endswith("y") and value[:-1].isdigit():
        delta = pd.DateOffset(years=int(value[:-1]))
    latest = df.index.max()
    return df[df.index >= latest - delta]
