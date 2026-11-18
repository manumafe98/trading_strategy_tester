from __future__ import annotations

import argparse
import itertools
import math
import os
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import replace
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from time import perf_counter

import pandas as pd

from .analytics import format_duration
from .backtest import BAR_DURATIONS, INTRABAR_RULES, TARGET_FILLS, result_columns, risk_for, risk_reward_ratios, validate_calendar_filters
from .cli import RunConfig, days_arg, months_arg
from .data import load_data, read_local_csv, time_period_args
from .models import OPERATIONS, IntrabarRule, TargetFill
from .reports import fill_rule_tags, format_metric, reset_output_dirs, write_results_html
from .runner import cache_data, run_variants
from .sessions import SessionSpec, parse_sessions, session_label
from .split import (
    SELECT_BY, Split, oos_cutoff, rank_variants, selection_tags, split_tags, validate_selection_args, validate_split_args,
)
from .settings import DEFAULT_MAX_VARIANTS, DEFAULT_SELECT_BY, DEFAULT_SELECT_TOP, DEFAULT_TIMEFRAMES, TIMEFRAMES, load_assets
from .strategy_loader import load_strategy
from .strategy_params import params_label, resolve_strategy_params
from .types import VariantBatchTask
from .utils import csv_items


__all__ = ["main", "parser", "run"]


EXIT_MODES = ("fixed", "trailing", "partial")
WorkerState = tuple[str, float] | None


def variant_label(task: VariantBatchTask) -> str:
    session = session_label(task["session"])
    parts = [task["strategy"], task["asset"], task["timeframe"], task["low_timeframe"]]
    if session:
        parts.append(session)
    if task["params_label"]:
        parts.append(task["params_label"])
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
        # Redraw only in a real terminal; pipes keep the old final-only output.
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
    if config.operation not in OPERATIONS:
        raise ValueError(f"--operation must be one of {', '.join(OPERATIONS)}; got: {config.operation}")
    if config.target_fill not in TARGET_FILLS:
        raise ValueError(f"--target_fill must be one of {', '.join(TARGET_FILLS)}; got: {config.target_fill}")
    if config.intrabar not in INTRABAR_RULES:
        raise ValueError(f"--intrabar must be one of {', '.join(INTRABAR_RULES)}; got: {config.intrabar}")
    validate_calendar_filters(config.days, config.months)
    strategies = {name: load_strategy(name) for name in config.strategies}
    session_specs = parse_sessions(config.sessions)
    if config.session_exit and not session_specs:
        raise ValueError("--session_exit requires --sessions with a session window")
    session_variants = session_specs or [None]
    validate_required_flags(strategies, session_specs)
    strategy_params = resolve_strategy_params(strategies, config.strategy_params)
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
    validate_split_args(config.oos_start, config.oos_fraction)
    split_mode = config.oos_start is not None or config.oos_fraction is not None
    if split_mode and config.max_trades is not None:
        raise ValueError("--max_trades cannot be combined with a split")
    if not split_mode and (config.select_by is not None or config.select_top is not None):
        raise ValueError("--select_by and --select_top need --oos_start or --oos_fraction")
    if split_mode:
        # Defaults are resolved once here; everything after reads the config.
        config = replace(
            config,
            select_by=DEFAULT_SELECT_BY if config.select_by is None else config.select_by,
            select_top=DEFAULT_SELECT_TOP if config.select_top is None else config.select_top,
        )
        validate_selection_args(config.select_by, config.select_top)
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
    low_timeframe_flags = list(dict.fromkeys(csv_items(config.low_timeframe)))
    # One entry per strategy, high timeframe and low timeframe; each is a batch per asset and session window.
    timeframe_pairs = []
    for strategy_name, strategy in strategies.items():
        declared = getattr(strategy, "LOW_TIMEFRAME", None)
        if low_timeframe_flags and declared is None:
            raise ValueError(f"--low_timeframe: {strategy_name} does not declare LOW_TIMEFRAME")
        for timeframe in timeframes:
            for low_timeframe in low_timeframe_flags or [declared or timeframe]:
                if low_timeframe not in TIMEFRAMES:
                    raise ValueError(f"Unknown low timeframe {low_timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
                for asset in assets:
                    if low_timeframe not in asset_configs[asset].bars_per_year:
                        raise ValueError(f"Missing bars_per_year for {asset} {low_timeframe}")
                if low_timeframe_flags:
                    low, high = BAR_DURATIONS[low_timeframe], BAR_DURATIONS[timeframe]
                    if low >= high:
                        raise ValueError(f"--low_timeframe: low timeframe {low_timeframe} must be smaller than high timeframe {timeframe}")
                    if high % low:
                        raise ValueError(f"--low_timeframe: low timeframe {low_timeframe} must divide high timeframe {timeframe} evenly")
                timeframe_pairs.append((strategy_name, strategy, timeframe, low_timeframe))
    batch_count = sum(len(strategy_params[name]) for name, *_ in timeframe_pairs) * len(assets) * len(session_variants)
    task_count = batch_count * len(variant_pairs)
    if config.max_variants < 1:
        raise ValueError("--max_variants must be a positive integer")
    if task_count > config.max_variants:
        raise ValueError(f"{task_count} variants exceed --max_variants {config.max_variants}; pass a larger --max_variants to run them")
    print(f"{task_count} variants", file=sys.stderr)
    workers = worker_count(config.workers, batch_count)
    risks = {asset: risk_for(asset, config.risk, assets) for asset in assets}
    rows = []
    splits: list[Split] = []
    outputs_reset = False
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
                for strategy_name, strategy, timeframe, low_timeframe in timeframe_pairs:
                    timeframe_groups.setdefault(low_timeframe, []).append((strategy_name, strategy, timeframe))
                for data_timeframe, strategy_timeframes in timeframe_groups.items():
                    status.update(phase="Loading data", detail=f"{asset} {data_timeframe}", total=task_count)
                    data = load_data(asset, asset_cfg, data_timeframe, config.time_period, config.data_source)
                    cutoff = None
                    if split_mode:
                        try:
                            cutoff = oos_cutoff(
                                data.index, asset_cfg, oos_start=config.oos_start, oos_fraction=config.oos_fraction
                            )
                        except ValueError as exc:
                            raise ValueError(f"{asset} {data_timeframe}: {exc}") from None
                        splits.append(Split.from_index(asset, data_timeframe, data.index, cutoff))
                    # Clear the previous bundle only once fresh data exists, so a failed load keeps it.
                    if not outputs_reset:
                        reset_output_dirs()
                        outputs_reset = True
                    data_cache = cache_data(data, cache_dir, cache_id)
                    cache_id += 1
                    for strategy_name, strategy, timeframe in strategy_timeframes:
                        defaults = getattr(strategy, "PARAMS", {})
                        for session, params in itertools.product(session_variants, strategy_params[strategy_name]):
                            tasks.append(
                                VariantBatchTask(
                                    strategy=strategy_name,
                                    data_cache=data_cache,
                                    variants=variant_pairs,
                                    asset=asset,
                                    asset_cfg=asset_cfg,
                                    timeframe=timeframe,
                                    low_timeframe=data_timeframe,
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
                                    session_exit=config.session_exit,
                                    oos_cutoff=cutoff,
                                    target_fill=config.target_fill,
                                    intrabar=config.intrabar,
                                    strategy_params=params,
                                    params_label=params_label(defaults, params),
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

                # Keep one task per slot so the dashboard shows real work, not queued futures.
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

        table = pd.DataFrame(rows)
        # Params shows only the values that differ from their defaults, so it is hidden when every row is empty.
        include_params = "Params" in table and bool(table["Params"].astype(bool).any())
        columns = result_columns(config.operation, include_session=bool(session_specs), split=split_mode, include_params=include_params)
        if not table.empty:
            strategy_order = {name: i for i, name in enumerate(strategies)}
            order = {asset: i for i, asset in enumerate(assets)}
            table["_strategy_order"] = table["Strategy"].map(strategy_order)
            table["_asset_order"] = table["Asset"].map(order)
            table = table.sort_values(["_strategy_order", "_asset_order", "_sort_return"], ascending=[True, True, False]).drop(columns=["_strategy_order", "_asset_order"])
            if split_mode:
                # Ranking needs every variant row, so it happens here rather than in the workers.
                table = rank_variants(table, config.select_by, config.select_top)
        console = table.reindex(columns=columns).copy()
        for column in console.columns:
            console[column] = console[column].map(lambda value, column=column: format_metric(column, value))
        result_path = write_results_html(table, config, columns, splits)
    finally:
        status.close()

    fill_rules = fill_rule_tags(config)
    if fill_rules:
        print(" | ".join(fill_rules))
    for split in splits:
        print(" | ".join(split_tags(split)))
    if splits and not table.empty:
        for tag in selection_tags(table):
            print(tag)
    print(console.to_string(index=False))
    print(f"results: {result_path}")
    elapsed = perf_counter() - started
    print(f"elapsed: {format_duration(elapsed)} ({elapsed:.2f}s)")


class RemovedTimeframeFlag(argparse.Action):
    """Fails `--timeframe` with a pointer to its replacement (ADR 0004)."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error("--timeframe was renamed to --high_timeframe")


def parser() -> argparse.ArgumentParser:
    # Abbreviations would let the old --strategy flag reach --strategy_params.
    p = argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument("--strategies", help="comma list of strategy/<name>.py to run")
    p.add_argument(
        "--strategy_params",
        metavar="NAME=VALUE;...",
        help="semicolon list of parameter values the strategies declare in PARAMS; comma lists run every combination, e.g. swing_length=10,20;entry_mode=break",
    )
    p.add_argument(
        "--max_variants",
        type=int,
        default=DEFAULT_MAX_VARIANTS,
        metavar="N",
        help=f"refuse a run of more than N variants (default {DEFAULT_MAX_VARIANTS})",
    )
    p.add_argument("--asset", help="comma list, default all configured assets")
    p.add_argument("--high_timeframe", help="comma list, default configured list")
    p.add_argument("--low_timeframe", help="comma list; only for strategies that declare LOW_TIMEFRAME, which is the default")
    p.add_argument("--timeframe", nargs="?", action=RemovedTimeframeFlag, help=argparse.SUPPRESS)
    p.add_argument("--sessions", help="comma list of asia, london, ny, ny_bank, all, none, or custom ranges like ny=09:30-12:00")
    p.add_argument(
        "--time_period",
        type=time_period_value,
        default="60d",
        help="rolling period, calendar year, or inclusive year range such as 2020-2021",
    )
    p.add_argument("--data_source", default="yfinance", choices=["yfinance", "local"])
    p.add_argument("--days", type=days_arg, default=(), help="comma list of weekdays, e.g. monday,wed")
    p.add_argument("--months", type=months_arg, default=(), help="comma list of months, e.g. january,sep")
    p.add_argument("--operation", default="all", choices=OPERATIONS)
    p.add_argument("--risk_reward_ratio", default="1", help="comma list of positive numeric RR targets, e.g. 1,2,3")
    p.add_argument("--exit_mode", default="fixed", help="comma list of fixed, trailing, partial; use all to run every mode")
    p.add_argument("--risk", default="1", help="global percent or map, e.g. 1 or MGC=1,MNQ=0.5")
    p.add_argument("--capital", type=float, default=10000)
    p.add_argument("--with_costs", action="store_true")
    p.add_argument(
        "--session_exit",
        action="store_true",
        help="close open trades at the close of the last bar inside their session window; requires --sessions",
    )
    p.add_argument(
        "--target_fill",
        default=TargetFill.TICK_THROUGH.value,
        choices=TARGET_FILLS,
        help="tick_through: a target fills when price trades one tick beyond it; touch: when price reaches it",
    )
    p.add_argument(
        "--intrabar",
        default=IntrabarRule.PATH.value,
        choices=INTRABAR_RULES,
        help="path: TradingView's open-high-low-close order; stop_first: a bar reaching stop and target exits at the stop",
    )
    p.add_argument("--workers", type=int, help="parallel variant workers, capped at logical CPU count")
    p.add_argument("--max_trades", type=int, default=None, help="diagnostic cap on first N closed trades per variant")
    p.add_argument(
        "--oos_start",
        metavar="YYYY-MM-DD",
        help="split the period: the out-of-sample period starts at the first market session starting on or after this date",
    )
    p.add_argument(
        "--oos_fraction",
        type=float,
        metavar="F",
        help="split the period: hold back the latest fraction F (0-1) of bars as the out-of-sample period",
    )
    p.add_argument(
        "--select_by",
        choices=tuple(SELECT_BY),
        help=f"split mode: in-sample metric that ranks variants per strategy and asset (default {DEFAULT_SELECT_BY})",
    )
    p.add_argument(
        "--select_top",
        type=int,
        metavar="N",
        help=f"split mode: mark the top N in-sample variants per strategy and asset as selected (default {DEFAULT_SELECT_TOP})",
    )
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
            timeframe=args.high_timeframe,
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
            session_exit=args.session_exit,
            oos_start=args.oos_start,
            oos_fraction=args.oos_fraction,
            select_by=args.select_by,
            select_top=args.select_top,
            target_fill=TargetFill(args.target_fill),
            intrabar=IntrabarRule(args.intrabar),
            strategy_params=args.strategy_params,
            low_timeframe=args.low_timeframe,
            max_variants=args.max_variants,
        )
        run(config)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
