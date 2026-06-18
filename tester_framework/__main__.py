from __future__ import annotations

import argparse

import pandas as pd

from .backtest import add_trade_counts, result_columns, risk_for, run_backtest, self_check
from .data import load_data
from .reports import reset_output_dirs, write_results_html, write_trade_html
from .settings import DEFAULT_TIMEFRAMES, TIMEFRAMES, load_assets
from .strategy_loader import load_strategy
from .utils import csv_items


__all__ = ["main", "parser", "run"]


def run(args: argparse.Namespace) -> None:
    asset_configs = load_assets()
    strategy = load_strategy(args.strategy)
    assets = [x.upper() for x in (csv_items(args.asset) or asset_configs.keys())]
    timeframes = csv_items(args.timeframe) or DEFAULT_TIMEFRAMES
    exits = csv_items(args.exit_structure) or ["1RR"]
    reset_output_dirs()

    for exit_structure in exits:
        rows = []
        for asset in assets:
            if asset not in asset_configs:
                raise ValueError(f"Unknown asset {asset}. Add it to config/assets.json")
            asset_cfg = asset_configs[asset]
            for timeframe in timeframes:
                if timeframe not in TIMEFRAMES:
                    raise ValueError(f"Unknown timeframe {timeframe}. Add it to TIMEFRAMES in tester_framework/settings.py")
                data = load_data(asset_cfg.ticker, timeframe, args.time_period)
                signals = strategy.generate_signals(data.copy(), asset=asset, timeframe=timeframe, params={})
                metrics, trades = run_backtest(
                    data,
                    signals,
                    asset=asset,
                    timeframe=timeframe,
                    exit_structure=exit_structure,
                    operation=args.operation,
                    risk_pct=risk_for(asset, args.risk),
                    capital=args.capital,
                    with_costs=args.with_costs,
                    asset_cfg=asset_cfg,
                )
                rows.append(add_trade_counts(metrics, trades, args.operation))
                for trade in trades:
                    write_trade_html(data, trade, strategy)

        table = pd.DataFrame(rows, columns=result_columns(args.operation))
        print(table.to_string(index=False))
        print(f"results: {write_results_html(table, args, exit_structure)}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", help="strategy/<name>.py to run")
    p.add_argument("--asset", help="comma list, default all configured assets")
    p.add_argument("--timeframe", help="comma list, default configured list")
    p.add_argument("--time_period", default="60d")
    p.add_argument("--operation", default="all", choices=["all", "long_only", "short_only"])
    p.add_argument("--exit_structure", default="1RR", help="comma list, e.g. 1RR,2RR,trailing_stop")
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
            return
        if not args.strategy:
            raise ValueError("--strategy is required")
        run(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
