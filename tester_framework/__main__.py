from __future__ import annotations

import argparse
import math
import os
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from time import perf_counter

import pandas as pd

from .analytics import format_duration
from .backtest import result_columns, risk_for, risk_reward_ratios, validate_calendar_filters
from .cli import RunConfig, days_arg, months_arg
from .data import load_data, read_local_csv, time_period_args
from .reports import format_metric, reset_output_dirs, write_results_html
from .runner import cache_data, run_variants
from .sessions import SessionSpec, parse_sessions, session_label
from .settings import DEFAULT_TIMEFRAMES, TIMEFRAMES, load_assets
from .strategy_loader import load_strategy
from .types import VariantBatchTask
from .utils import csv_items


__all__ = ["main", "parser", "run"]


EXIT_MODES = ("fixed", "trailing", "partial")
WorkerState = tuple[str, float] | None


def variant_label(task: VariantBatchTask) -> str:
    session = session_label(task["session"])
    parts = [task["strategy"], task["asset"], task["timeframe"]]
    if session:
        parts.append(session)
    parts.append(f'{len(task["variants"])} variants')
    return " ".join(parts)


def status_lines(
    phase: str,
    detail: str,
    completed: int,
    total: int,
    started: float,
    workers: list[WorkerState],
    now: float | None = None,
) -> list[str]:
    now = perf_counter() if now is None else now
    header = f"{phase} | {completed}/{total} complete | elapsed {format_duration(now - started)}"
    if completed >= total:
        idle = "done"
    elif phase == "Backtesting":
        idle = "idle waiting"
    else:
        idle = "waiting"
    lines = [header, detail]
    for index, worker in enumerate(workers, 1):
        if worker is None:
            lines.append(f"worker {index}: {idle}")
            continue
        label, worker_started = worker
        lines.append(f"worker {index}: {label} | {format_duration(now - worker_started)}")
    return lines


class StatusDisplay:
    def __init__(self, workers: int, started: float) -> None:
        self.started = started
        self.phase = "Starting"
        self.detail = ""
        self.completed = 0
        self.total = 0
        self.workers: list[WorkerState] = [None] * workers
        self._stream = sys.stderr
        # ponytail: redraw only in a real terminal; pipes keep the old final-only output.
        self._enabled = self._stream.isatty()
        self._line_count = 0
        self._lock = Lock()
        self._stop = Event()
        self._ticker: Thread | None = None

    def update(
        self,
        *,
        phase: str | None = None,
        detail: str | None = None,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        with self._lock:
            if phase is not None:
                self.phase = phase
            if detail is not None:
                self.detail = detail
            if completed is not None:
                self.completed = completed
            if total is not None:
                self.total = total

    def set_worker(self, slot: int, label: str | None, started: float | None = None) -> None:
        with self._lock:
            self.workers[slot] = None if label is None or started is None else (label, started)

    def clear_workers(self) -> None:
        with self._lock:
            self.workers = [None] * len(self.workers)

    def start(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            if self._ticker is not None:
                return
            self._stop = Event()
            self._ticker = Thread(target=self._tick, daemon=True)
            self._ticker.start()
        self.render()

    def pause(self) -> None:
        with self._lock:
            ticker = self._ticker
            if ticker is None:
                return
            self._stop.set()
            self._ticker = None
        ticker.join()

    def close(self) -> None:
        self.pause()
        if not self._enabled:
            return
        with self._lock:
            if not self._line_count:
                return
            self._stream.write(f"\x1b[{self._line_count}F")
            self._stream.write("".join("\x1b[2K\n" for _ in range(self._line_count)))
            self._stream.write(f"\x1b[{self._line_count}F")
            self._stream.flush()
            self._line_count = 0

    def render(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            lines = status_lines(
                self.phase,
                self.detail,
                self.completed,
                self.total,
                self.started,
                self.workers,
            )
            if self._line_count:
                self._stream.write(f"\x1b[{self._line_count}F")
            self._stream.write("".join(f"\x1b[2K{line}\n" for line in lines))
            self._stream.flush()
            self._line_count = len(lines)

    def _tick(self) -> None:
        stop = self._stop
        while not stop.wait(1):
            self.render()


def exit_mode_variants(value: str | None, risk_reward_ratio: float) -> list[str]:
    modes = []
    for mode in csv_items(value) or ["fixed"]:
        expanded = EXIT_MODES if mode == "all" else (mode,)
        for item in expanded:
            if item not in EXIT_MODES:
                raise ValueError(f"--exit_mode must use fixed, trailing, partial, or all; got: {item}")
            if item not in modes:
                modes.append(item)
    if risk_reward_ratio < 2:
        modes = [mode for mode in modes if mode != "partial"]
    if risk_reward_ratio <= 1:
        modes = [mode for mode in modes if mode != "trailing"]
    return modes


def worker_count(requested: int | None, task_count: int) -> int:
    if requested is not None and requested < 1:
        raise ValueError("--workers must be at least 1")
    cpus = os.cpu_count() or 1
    return min(requested or cpus, cpus, task_count)


def trade_html_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--trade_html must be a non-negative integer") from None
    if count < 0:
        raise argparse.ArgumentTypeError("--trade_html must be a non-negative integer")
    return count


def time_period_value(value: str) -> str:
    try:
        time_period_args(value, "yfinance")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return str(value).strip().lower()


def validate_required_flags(strategies: dict[str, object], sessions: list[SessionSpec]) -> None:
    errors = []
    available = {"sessions": sessions}
    for name, strategy in strategies.items():
        required = getattr(strategy, "REQUIRED_FLAGS", {})
        if not required:
            continue
        if not isinstance(required, dict):
            raise ValueError(f"{name} REQUIRED_FLAGS must be a dict of flag -> message")
        for flag, message in required.items():
            if flag not in available:
                raise ValueError(f"{name} declares unknown required flag: {flag}")
            if not available[flag]:
                errors.append(f"{name}: {message}")
    if errors:
        raise ValueError("\n".join(errors))


def run(config: RunConfig) -> None:
    started = perf_counter()
    asset_configs = load_assets()
    if not config.strategies:
        raise ValueError("--strategies is required")
    time_period_args(config.time_period, config.data_source)
    validate_calendar_filters(config.days, config.months)
    strategies = {name: load_strategy(name) for name in config.strategies}
    session_specs = parse_sessions(config.sessions)
    session_variants = session_specs or [None]
    validate_required_flags(strategies, session_specs)
    assets = list(dict.fromkeys(x.upper() for x in (csv_items(config.asset) or asset_configs.keys())))
    timeframes = list(dict.fromkeys(csv_items(config.timeframe) or DEFAULT_TIMEFRAMES))
    ratios = list(dict.fromkeys(risk_reward_ratios(config.risk_reward_ratio)))
    variants = {ratio: exit_mode_variants(config.exit_mode, ratio) for ratio in ratios}
    if not any(variants.values()):
        raise ValueError("No exit modes are compatible with the requested risk/reward ratios")
    variant_pairs = tuple((ratio, mode) for ratio in ratios for mode in variants[ratio])
    if not math.isfinite(config.capital) or config.capital <= 0:
        raise ValueError("--capital must be finite and positive")
    if config.max_trades is not None and config.max_trades < 1:
        raise ValueError("--max_trades must be a positive integer")
    if config.trade_html is not None and (
        isinstance(config.trade_html, bool) or not isinstance(config.trade_html, int) or config.trade_html < 0
    ):
        raise ValueError("--trade_html must be a non-negative integer")
    for asset in assets:
        if asset not in asset_configs:
            raise ValueError(f"Unknown asset {asset}. Add it to config/assets.json")
    for timeframe in timeframes:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unknown timeframe {timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
    for strategy in strategies.values():
        execution_timeframe = getattr(strategy, "EXECUTION_TIMEFRAME", None)
        for timeframe in timeframes:
            data_timeframe = execution_timeframe or timeframe
            if data_timeframe not in TIMEFRAMES:
                raise ValueError(f"Unknown execution timeframe {data_timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
            for asset in assets:
                if data_timeframe not in asset_configs[asset].bars_per_year:
                    raise ValueError(f"Missing bars_per_year for {asset} {data_timeframe}")
    task_count = (
        len(strategies)
        * len(assets)
        * len(timeframes)
        * len(session_variants)
        * len(variant_pairs)
    )
    batch_count = len(strategies) * len(assets) * len(timeframes) * len(session_variants)
    workers = worker_count(config.workers, batch_count)
    risks = {asset: risk_for(asset, config.risk) for asset in assets}
    rows = []
    reset_output_dirs()
    status = StatusDisplay(workers, started)
    strategy_label = ", ".join(strategies)
    status.update(phase="Preparing", detail=f"{strategy_label} | {task_count} variants", completed=0, total=task_count)
    status.start()

    try:
        with TemporaryDirectory(prefix="tester_framework_") as cache_dir, ProcessPoolExecutor(max_workers=workers) as executor:
            cache_id = 0
            for asset in assets:
                tasks: list[VariantBatchTask] = []
                asset_cfg = asset_configs[asset]
                risk_pct = risks[asset]
                timeframe_groups = {}
                for strategy_name, strategy in strategies.items():
                    execution_timeframe = getattr(strategy, "EXECUTION_TIMEFRAME", None)
                    for timeframe in timeframes:
                        timeframe_groups.setdefault(execution_timeframe or timeframe, []).append(
                            (strategy_name, strategy, timeframe)
                        )
                for data_timeframe, strategy_timeframes in timeframe_groups.items():
                    status.update(phase="Loading data", detail=f"{asset} {data_timeframe}", total=task_count)
                    data = load_data(asset, asset_cfg, data_timeframe, config.time_period, config.data_source)
                    data_cache = cache_data(data, cache_dir, cache_id)
                    cache_id += 1
                    for strategy_name, _strategy, timeframe in strategy_timeframes:
                        for session in session_variants:
                            tasks.append(
                                VariantBatchTask(
                                    strategy=strategy_name,
                                    data_cache=data_cache,
                                    variants=variant_pairs,
                                    asset=asset,
                                    asset_cfg=asset_cfg,
                                    timeframe=timeframe,
                                    execution_timeframe=data_timeframe,
                                    operation=config.operation,
                                    risk_pct=risk_pct,
                                    capital=config.capital,
                                    with_costs=config.with_costs,
                                    time_period=config.time_period,
                                    data_source=config.data_source,
                                    max_trades=config.max_trades,
                                    trade_html=config.trade_html,
                                    session=session,
                                    days=config.days,
                                    months=config.months,
                                )
                            )
                    del data

                if config.data_source == "local":
                    read_local_csv.cache_clear()
                status.update(phase="Backtesting", detail=f"{workers} workers | {asset}", total=task_count)
                pending_tasks = iter(tasks)
                active: dict[object, int] = {}

                def submit(slot: int) -> None:
                    try:
                        task = next(pending_tasks)
                    except StopIteration:
                        status.set_worker(slot, None)
                        return
                    started_at = perf_counter()
                    future = executor.submit(run_variants, task)
                    active[future] = slot
                    status.set_worker(slot, variant_label(task), started_at)

                # ponytail: keep one task per slot so the dashboard shows real work, not queued futures.
                for slot in range(workers):
                    submit(slot)
                try:
                    while active:
                        done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
                        for future in done:
                            slot = active.pop(future)
                            rows.extend(future.result())
                            status.update(completed=len(rows))
                            submit(slot)
                except BaseException:
                    for future in active:
                        future.cancel()
                    raise

        status.clear_workers()
        status.update(phase="Writing results", detail="console + html", completed=len(rows), total=task_count)
        status.start()

        columns = result_columns(config.operation, include_session=bool(session_specs))
        table = pd.DataFrame(rows)
        if not table.empty:
            strategy_order = {name: i for i, name in enumerate(strategies)}
            order = {asset: i for i, asset in enumerate(assets)}
            table["_strategy_order"] = table["Strategy"].map(strategy_order)
            table["_asset_order"] = table["Asset"].map(order)
            table = table.sort_values(["_strategy_order", "_asset_order", "_sort_return"], ascending=[True, True, False]).drop(columns=["_strategy_order", "_asset_order"])
        console = table.reindex(columns=columns).copy()
        for column in console.columns:
            console[column] = console[column].map(lambda value, column=column: format_metric(column, value))
        result_path = write_results_html(table, config, columns)
    finally:
        status.close()

    print(console.to_string(index=False))
    print(f"results: {result_path}")
    elapsed = perf_counter() - started
    print(f"elapsed: {format_duration(elapsed)} ({elapsed:.2f}s)")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--strategies", help="comma list of strategy/<name>.py to run")
    p.add_argument("--asset", help="comma list, default all configured assets")
    p.add_argument("--timeframe", help="comma list, default configured list")
    p.add_argument("--sessions", help="comma list of asia, london, ny, all, none, or custom ranges like ny=09:30-12:00")
    p.add_argument(
        "--time_period",
        type=time_period_value,
        default="60d",
        help="rolling period, calendar year, or inclusive year range such as 2020-2021",
    )
    p.add_argument("--data_source", default="yfinance", choices=["yfinance", "local"])
    p.add_argument("--days", type=days_arg, default=(), help="comma list of weekdays, e.g. monday,wed")
    p.add_argument("--months", type=months_arg, default=(), help="comma list of months, e.g. january,sep")
    p.add_argument("--operation", default="all", choices=["all", "long_only", "short_only"])
    p.add_argument("--risk_reward_ratio", default="1", help="comma list of positive numeric RR targets, e.g. 1,2,3")
    p.add_argument("--exit_mode", default="fixed", help="comma list of fixed, trailing, partial; use all to run every mode")
    p.add_argument("--risk", default="1", help="global percent or map, e.g. 1 or MGC=1,MNQ=0.5")
    p.add_argument("--capital", type=float, default=10000)
    p.add_argument("--with_costs", action="store_true")
    p.add_argument("--workers", type=int, help="parallel variant workers, capped at logical CPU count")
    p.add_argument("--max_trades", type=int, default=None, help="diagnostic cap on first N closed trades per variant")
    p.add_argument(
        "--trade_html",
        type=trade_html_count,
        metavar="N",
        help="trade charts per variant; 0 disables them, omitted writes all",
    )
    return p


def main() -> None:
    args = parser().parse_args()
    try:
        strategies = tuple(dict.fromkeys(csv_items(args.strategies)))
        if not strategies:
            raise ValueError("--strategies is required")
        config = RunConfig(
            strategies=strategies,
            asset=args.asset,
            timeframe=args.timeframe,
            sessions=args.sessions,
            time_period=args.time_period,
            data_source=args.data_source,
            operation=args.operation,
            risk_reward_ratio=args.risk_reward_ratio,
            exit_mode=args.exit_mode,
            risk=args.risk,
            capital=args.capital,
            with_costs=args.with_costs,
            workers=args.workers,
            max_trades=args.max_trades,
            trade_html=args.trade_html,
            days=args.days,
            months=args.months,
        )
        run(config)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
