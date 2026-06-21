from __future__ import annotations

import gc
from pathlib import Path

import numpy as np
import pandas as pd

from .analytics import analyze_trades, encode_analytics, strategy_metrics
from .backtest import add_trade_counts, run_backtest
from .reports import write_trade_html
from .sessions import filter_signals, session_label
from .settings import FINANCIAL_COLUMNS
from .strategy_loader import load_strategy
from .types import VariantBatchTask


def cache_data(data: pd.DataFrame, cache_dir: str, cache_id: int) -> tuple[str, str, tuple[str, ...]]:
    values_path = Path(cache_dir) / f"{cache_id}_values.npy"
    index_path = Path(cache_dir) / f"{cache_id}_index.npy"
    values = np.lib.format.open_memmap(values_path, mode="w+", dtype="float64", shape=data.shape)
    values[:] = data.to_numpy(dtype="float64", copy=False)
    values.flush()
    del values
    np.save(index_path, data.index.to_numpy(dtype="datetime64[ns]", copy=False), allow_pickle=False)
    return str(values_path), str(index_path), tuple(data.columns)


def _run_variant(
    task: VariantBatchTask,
    data: pd.DataFrame,
    strategy,
    signals: pd.DataFrame,
    risk_reward_ratio: float,
    exit_mode: str,
) -> dict[str, object]:
    session = session_label(task["session"])
    metrics, trades = run_backtest(
        data,
        signals,
        asset=task["asset"],
        timeframe=task["timeframe"],
        strategy=task["strategy"],
        risk_reward_ratio=risk_reward_ratio,
        exit_mode=exit_mode,
        operation=task["operation"],
        risk_pct=task["risk_pct"],
        capital=task["capital"],
        with_costs=task["with_costs"],
        asset_cfg=task["asset_cfg"],
        execution_timeframe=task["execution_timeframe"],
        max_trades=task["max_trades"],
        days=task["days"],
        months=task["months"],
    )
    params = {
        "risk_reward_ratio": risk_reward_ratio,
        "exit_mode": exit_mode,
        "operation": task["operation"],
        "risk_pct": task["risk_pct"],
        "capital": task["capital"],
        "with_costs": task["with_costs"],
        "time_period": task["time_period"],
        "data_source": task["data_source"],
        "tick_size": task["asset_cfg"].tick_size,
        "session": task["session"],
    }
    custom_metrics = strategy_metrics(strategy, data, signals, trades, task["asset"], task["timeframe"], params)
    charted = 0
    try:
        for chart_number, trade in enumerate(trades, 1):
            if session:
                trade["session"] = session
            if task["trade_html"] is None or chart_number <= task["trade_html"]:
                charted += 1
                trade["chart_path"] = write_trade_html(data, trade, strategy)
                if charted % 100 == 0:
                    # ponytail: Plotly retains cyclic objects long enough to exhaust parallel Windows workers.
                    gc.collect()
            else:
                trade["chart_path"] = None
    finally:
        if charted % 100:
            gc.collect()
    analytics = analyze_trades(trades, exit_mode)
    row = add_trade_counts(metrics, trades, task["operation"], task["with_costs"])
    if task["with_costs"]:
        for column in FINANCIAL_COLUMNS:
            row[column] = (metrics["Gross"][column], metrics["Net"][column])
    row["RR"] = f"{risk_reward_ratio:g}"
    row["Exit Mode"] = exit_mode
    if session:
        row["Session"] = session
    row["_risk_pct"] = task["risk_pct"]
    row["_sort_return"] = metrics["Net" if task["with_costs"] else "Gross"]["Return"]
    row["_analytics"] = encode_analytics(analytics)
    row["_strategy_metrics"] = custom_metrics
    return row


def run_variants(task: VariantBatchTask) -> list[dict[str, object]]:
    values = index_values = data = None
    session = session_label(task["session"])
    batch_parts = [task["strategy"], task["asset"], task["timeframe"]]
    if session:
        batch_parts.append(session)
    batch_label = " ".join(batch_parts)
    try:
        try:
            values_path, index_path, columns = task["data_cache"]
            values = np.load(values_path, mmap_mode="r", allow_pickle=False)
            index_values = np.load(index_path, mmap_mode="r", allow_pickle=False)
            index = pd.DatetimeIndex(index_values, copy=False, name="time")
            data = pd.DataFrame(values, index=index, columns=columns, copy=False)
            strategy = load_strategy(task["strategy"])
            signals = strategy.generate_signals(
                data,
                asset=task["asset"],
                timeframe=task["timeframe"],
                params={"tick_size": task["asset_cfg"].tick_size, "session": task["session"]},
            )
            signals = filter_signals(signals, task["session"])
        except MemoryError as exc:
            raise RuntimeError(f"{batch_label}: insufficient memory; retry with fewer --workers") from exc
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            detail = str(exc) or type(exc).__name__
            raise RuntimeError(f"{batch_label}: {detail}") from exc
        rows = []
        for risk_reward_ratio, exit_mode in task["variants"]:
            label = f"{batch_label} {risk_reward_ratio:g}R {exit_mode}"
            try:
                rows.append(_run_variant(task, data, strategy, signals, risk_reward_ratio, exit_mode))
            except MemoryError as exc:
                raise RuntimeError(f"{label}: insufficient memory; retry with fewer --workers") from exc
            except (FileNotFoundError, RuntimeError, ValueError) as exc:
                detail = str(exc) or type(exc).__name__
                raise RuntimeError(f"{label}: {detail}") from exc
        return rows
    finally:
        data = None
        for array in (values, index_values):
            mmap = getattr(array, "_mmap", None)
            if mmap is not None:
                mmap.close()


__all__ = ["cache_data", "run_variants"]
