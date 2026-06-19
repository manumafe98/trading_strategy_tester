from __future__ import annotations

import gc
from pathlib import Path

import numpy as np
import pandas as pd

from .analytics import analyze_trades, strategy_metrics
from .backtest import add_trade_counts, run_backtest
from .reports import write_trade_html
from .settings import FINANCIAL_COLUMNS
from .strategy_loader import load_strategy
from .types import VariantTask


def cache_data(data: pd.DataFrame, cache_dir: str, cache_id: int) -> tuple[str, str, tuple[str, ...]]:
    values_path = Path(cache_dir) / f"{cache_id}_values.npy"
    index_path = Path(cache_dir) / f"{cache_id}_index.npy"
    values = np.lib.format.open_memmap(values_path, mode="w+", dtype="float64", shape=data.shape)
    values[:] = data.to_numpy(dtype="float64", copy=False)
    values.flush()
    del values
    np.save(index_path, data.index.to_numpy(dtype="datetime64[ns]", copy=False), allow_pickle=False)
    return str(values_path), str(index_path), tuple(data.columns)


def run_variant(task: VariantTask) -> dict[str, object]:
    values = index_values = data = None
    label = f'{task["asset"]} {task["timeframe"]} {task["risk_reward_ratio"]:g}R {task["exit_mode"]}'
    try:
        values_path, index_path, columns = task["data_cache"]
        values = np.load(values_path, mmap_mode="r", allow_pickle=False)
        index_values = np.load(index_path, mmap_mode="r", allow_pickle=False)
        index = pd.DatetimeIndex(index_values, copy=False, name="time")
        data = pd.DataFrame(values, index=index, columns=columns, copy=False)
        strategy = load_strategy(task["strategy"])
        metrics, trades = run_backtest(
            data,
            task["signals"],
            asset=task["asset"],
            timeframe=task["timeframe"],
            risk_reward_ratio=task["risk_reward_ratio"],
            exit_mode=task["exit_mode"],
            operation=task["operation"],
            risk_pct=task["risk_pct"],
            capital=task["capital"],
            with_costs=task["with_costs"],
            asset_cfg=task["asset_cfg"],
            execution_timeframe=task["execution_timeframe"],
        )
        params = {
            "risk_reward_ratio": task["risk_reward_ratio"],
            "exit_mode": task["exit_mode"],
            "operation": task["operation"],
            "risk_pct": task["risk_pct"],
            "capital": task["capital"],
            "with_costs": task["with_costs"],
            "time_period": task["time_period"],
            "data_source": task["data_source"],
            "tick_size": task["asset_cfg"].tick_size,
        }
        custom_metrics = strategy_metrics(strategy, data, task["signals"], trades, task["asset"], task["timeframe"], params)
        for chart_number, trade in enumerate(trades, 1):
            trade["chart_path"] = write_trade_html(data, trade, strategy)
            if chart_number % 100 == 0:
                # ponytail: Plotly retains cyclic objects long enough to exhaust parallel Windows workers.
                gc.collect()
        analytics = analyze_trades(trades, task["exit_mode"])
        row = add_trade_counts(metrics, trades, task["operation"], task["with_costs"])
        if task["with_costs"]:
            for column in FINANCIAL_COLUMNS:
                row[column] = (metrics["Gross"][column], metrics["Net"][column])
        row["RR"] = f'{task["risk_reward_ratio"]:g}'
        row["Exit Mode"] = task["exit_mode"]
        row["_sort_return"] = metrics["Net" if task["with_costs"] else "Gross"]["Return"]
        row["_analytics"] = analytics
        row["_strategy_metrics"] = custom_metrics
        return row
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        detail = str(exc) or type(exc).__name__
        raise RuntimeError(f"{label}: {detail}") from exc
    finally:
        data = None
        for array in (values, index_values):
            mmap = getattr(array, "_mmap", None)
            if mmap is not None:
                mmap.close()


__all__ = ["cache_data", "run_variant"]
