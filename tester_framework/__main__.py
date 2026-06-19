from __future__ import annotations

import argparse

import pandas as pd

from .analytics import analyze_trades, strategy_metrics
from .backtest import add_trade_counts, result_columns, risk_for, risk_reward_ratios, run_backtest, self_check
from .data import load_data
from .reports import reset_output_dirs, write_results_html, write_trade_html
from .settings import DEFAULT_TIMEFRAMES, TIMEFRAMES, load_assets
from .strategy_loader import load_strategy
from .utils import csv_items


__all__ = ["main", "parser", "run"]


def trailing_stop_variants(mode: str, risk_reward_ratio: float) -> list[bool]:
    if mode == "off":
        return [False]
    if mode == "on":
        return [True]
    if risk_reward_ratio <= 1:
        return [False]
    return [False, True]


def cli_self_check() -> None:
    if trailing_stop_variants("off", 2) != [False] or trailing_stop_variants("on", 2) != [True]:
        raise RuntimeError("trailing stop mode parsing failed")
    if trailing_stop_variants("both", 1) != [False] or trailing_stop_variants("both", 2) != [False, True]:
        raise RuntimeError("trailing stop combined variants failed")


def run(args: argparse.Namespace) -> None:
    asset_configs = load_assets()
    strategy = load_strategy(args.strategy)
    execution_timeframe = getattr(strategy, "EXECUTION_TIMEFRAME", None)
    assets = [x.upper() for x in (csv_items(args.asset) or asset_configs.keys())]
    timeframes = csv_items(args.timeframe) or DEFAULT_TIMEFRAMES
    ratios = risk_reward_ratios(args.risk_reward_ratio)
    rows = []
    financial_columns = ("Return", "Max DD", "Sharpe Ratio", "Return / DD")
    reset_output_dirs()

    for asset in assets:
        if asset not in asset_configs:
            raise ValueError(f"Unknown asset {asset}. Add it to config/assets.json")
        asset_cfg = asset_configs[asset]
        for timeframe in timeframes:
            if timeframe not in TIMEFRAMES:
                raise ValueError(f"Unknown timeframe {timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
            data_timeframe = execution_timeframe or timeframe
            if data_timeframe not in TIMEFRAMES:
                raise ValueError(f"Unknown execution timeframe {data_timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
            data = load_data(asset, asset_cfg.ticker, data_timeframe, args.time_period, args.data_source)
            signals = strategy.generate_signals(data.copy(), asset=asset, timeframe=timeframe, params={})
            risk_pct = risk_for(asset, args.risk)
            for risk_reward_ratio in ratios:
                for trailing_stop in trailing_stop_variants(args.trailing_stop, risk_reward_ratio):
                    metrics, trades = run_backtest(
                        data,
                        signals,
                        asset=asset,
                        timeframe=timeframe,
                        risk_reward_ratio=risk_reward_ratio,
                        trailing_stop=trailing_stop,
                        operation=args.operation,
                        risk_pct=risk_pct,
                        capital=args.capital,
                        with_costs=args.with_costs,
                        asset_cfg=asset_cfg,
                    )
                    params = {
                        "risk_reward_ratio": risk_reward_ratio,
                        "trailing_stop": trailing_stop,
                        "operation": args.operation,
                        "risk_pct": risk_pct,
                        "capital": args.capital,
                        "with_costs": args.with_costs,
                        "time_period": args.time_period,
                        "data_source": args.data_source,
                    }
                    custom_metrics = strategy_metrics(strategy, data, signals, trades, asset, timeframe, params)
                    for trade in trades:
                        trade["chart_path"] = write_trade_html(data, trade, strategy)
                    analytics = analyze_trades(trades, trailing_stop)
                    row = add_trade_counts(metrics, trades, args.operation)
                    if args.with_costs:
                        for column in financial_columns:
                            row[column] = (metrics["Gross"][column], metrics["Net"][column])
                    row["RR"] = f"{risk_reward_ratio:g}"
                    row["Trailing"] = "yes" if trailing_stop else "no"
                    row["_sort_return"] = metrics["Net" if args.with_costs else "Gross"]["Return"]
                    row["_analytics"] = analytics
                    row["_strategy_metrics"] = custom_metrics
                    rows.append(row)

    columns = result_columns(args.operation)
    table = pd.DataFrame(rows)
    if not table.empty:
        order = {asset: i for i, asset in enumerate(assets)}
        table["_asset_order"] = table["Asset"].map(order)
        table = table.sort_values(["_asset_order", "_sort_return"], ascending=[True, False]).drop(columns="_asset_order")
    console = table.reindex(columns=columns).copy()
    if args.with_costs:
        for column in financial_columns:
            console[column] = console[column].map(lambda pair: f"{pair[0]:.2f} / {pair[1]:.2f}")
    print(console.to_string(index=False))
    print(f"results: {write_results_html(table, args, columns)}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", help="strategy/<name>.py to run")
    p.add_argument("--asset", help="comma list, default all configured assets")
    p.add_argument("--timeframe", help="comma list, default configured list")
    p.add_argument("--time_period", default="60d")
    p.add_argument("--data_source", default="yfinance", choices=["yfinance", "local"])
    p.add_argument("--operation", default="all", choices=["all", "long_only", "short_only"])
    p.add_argument("--risk_reward_ratio", default="1", help="comma list of positive numeric RR targets, e.g. 1,2,3")
    p.add_argument("--trailing-stop", nargs="?", const="on", default="off", choices=["off", "on", "both"])
    p.add_argument("--risk", default="1", help="global percent or map, e.g. 1 or MGC=1,MNQ=0.5")
    p.add_argument("--capital", type=float, default=10000)
    p.add_argument("--with_costs", action="store_true")
    p.add_argument("--self_check", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    try:
        if args.self_check:
            self_check()
            cli_self_check()
            return
        if not args.strategy:
            raise ValueError("--strategy is required")
        run(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
