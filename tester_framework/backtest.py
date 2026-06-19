from __future__ import annotations

import math

import pandas as pd

from .analytics import analyze_trades, classify_outcome, strategy_metrics, trade_stats
from .models import AssetConfig, ExitReason, Side
from .utils import csv_items


__all__ = ["add_trade_counts", "result_columns", "risk_for", "risk_reward_ratios", "run_backtest", "self_check"]
DEFAULT_RISK = 1.0


def risk_reward_ratios(value: str | None) -> list[float]:
    ratios = []
    for part in csv_items(value) or ["1"]:
        try:
            rr = float(part)
        except ValueError:
            raise ValueError(f"--risk_reward_ratio must be numeric, got: {part}") from None
        if not math.isfinite(rr) or rr <= 0:
            raise ValueError("--risk_reward_ratio must be positive")
        ratios.append(rr)
    return ratios


def risk_for(asset: str, risk: str) -> float:
    parts = csv_items(risk)
    if not parts:
        return DEFAULT_RISK
    default = DEFAULT_RISK
    overrides = {}
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            overrides[key.strip().upper()] = float(value)
        else:
            default = float(part)
    return overrides.get(asset.upper(), default)


def normalize_signals(signals: pd.DataFrame) -> pd.DataFrame:
    signals = pd.DataFrame(signals).copy()
    if signals.empty:
        return pd.DataFrame(columns=["time", "side", "stop"])
    if "time" not in signals.columns:
        signals = signals.reset_index().rename(columns={signals.index.name or "index": "time"})
    signals.columns = [str(c).lower() for c in signals.columns]
    missing = {"side", "stop"} - set(signals.columns)
    if missing:
        raise ValueError(f"Signals missing columns: {', '.join(sorted(missing))}")
    signals["side"] = signals["side"].astype("string").str.lower()
    valid_sides = {Side.LONG.value, Side.SHORT.value}
    invalid = sorted(set(signals["side"].dropna()) - valid_sides)
    if invalid:
        raise ValueError(f"Signals side must be long or short, got: {', '.join(invalid)}")
    signals["time"] = pd.to_datetime(signals["time"])
    signals["stop"] = pd.to_numeric(signals["stop"], errors="coerce")
    if "entry" in signals.columns:
        signals["entry"] = pd.to_numeric(signals["entry"], errors="coerce")
    if "plot_start_time" in signals.columns:
        signals["plot_start_time"] = pd.to_datetime(signals["plot_start_time"])
    signals = signals.dropna(subset=["time", "side", "stop"]).copy()
    signals["side"] = signals["side"].map(Side)
    return signals.sort_values("time")


def rounded_qty(qty: float, step: float) -> float:
    return round(math.floor(qty / step) * step, 8)


def adjusted_entry(price: float, side: Side, cfg: AssetConfig, with_costs: bool) -> float:
    extra = cfg.spread_points / 2 + cfg.slippage_points if with_costs else 0.0
    return price + extra if side == Side.LONG else price - extra


def adjusted_exit(price: float, side: Side, cfg: AssetConfig, with_costs: bool) -> float:
    extra = cfg.spread_points / 2 + cfg.slippage_points if with_costs else 0.0
    return price - extra if side == Side.LONG else price + extra


def partial_targets(qty: float, risk_reward_ratio: float, step: float, min_qty: float) -> list[tuple[float, float]]:
    stages = math.floor(risk_reward_ratio)
    if stages < 2:
        return []
    units = round(qty / step)
    base, extras = divmod(units, stages)
    quantities = [base] * stages
    for i in range(stages - 1, stages - extras - 1, -1):
        quantities[i] += 1
    quantities = [round(units * step, 8) for units in quantities]
    if any(quantity < min_qty for quantity in quantities):
        return []
    target_rs = [float(r) for r in range(1, stages)] + [risk_reward_ratio]
    return list(zip(target_rs, quantities))


def exit_fill(
    cfg: AssetConfig,
    trade: dict,
    qty: float,
    raw_exit_price: float,
    exit_i: int,
    exit_time,
    reason: ExitReason,
    with_costs: bool,
    target_r: float | None = None,
) -> dict:
    side = trade["side"]
    direction = 1 if side == Side.LONG else -1
    raw_exit = float(raw_exit_price)
    net_exit = adjusted_exit(raw_exit, side, cfg, with_costs)
    commission = cfg.commission_per_side * qty * 2 if with_costs else 0.0
    gross_pnl = (raw_exit - trade["raw_entry"]) * direction * qty * cfg.point_value
    net_pnl = (net_exit - trade["net_entry"]) * direction * qty * cfg.point_value - commission
    realized_r = (raw_exit - trade["raw_entry"]) * direction / trade["initial_risk"]
    return {
        "exit_i": exit_i,
        "exit_time": exit_time,
        "raw_exit": raw_exit,
        "net_exit": net_exit,
        "exit": raw_exit,
        "exit_reason": reason,
        "qty": qty,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "pnl": net_pnl,
        "realized_r": realized_r,
        "target_r": target_r,
    }


def finalize_trade(trade: dict, exits: list[dict]) -> dict:
    last = exits[-1]
    realized_r = sum(fill["qty"] / trade["qty"] * fill["realized_r"] for fill in exits)
    closed = trade.copy()
    closed.update({key: last[key] for key in ("exit_i", "exit_time", "raw_exit", "net_exit", "exit", "exit_reason")})
    closed.update(
        {
            "exits": exits,
            "gross_pnl": sum(fill["gross_pnl"] for fill in exits),
            "net_pnl": sum(fill["net_pnl"] for fill in exits),
            "pnl": sum(fill["net_pnl"] for fill in exits),
            "realized_r": realized_r,
            "outcome": classify_outcome(realized_r),
            "giveback_r": max(0.0, float(trade["mfe_r"]) - realized_r),
            "holding_duration": pd.Timestamp(last["exit_time"]) - pd.Timestamp(trade["entry_time"]),
        }
    )
    return closed


def run_backtest(
    data: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    asset: str,
    timeframe: str,
    risk_reward_ratio: float,
    exit_mode: str,
    operation: str,
    risk_pct: float,
    capital: float,
    with_costs: bool,
    asset_cfg: AssetConfig,
) -> tuple[dict, list[dict]]:
    if not math.isfinite(risk_reward_ratio) or risk_reward_ratio <= 0:
        raise ValueError("--risk_reward_ratio must be positive")
    if exit_mode not in {"fixed", "trailing", "partial"}:
        raise ValueError(f"Unknown exit mode: {exit_mode}")
    gross_equity = capital
    net_equity = capital
    gross_equity_curve = [capital]
    net_equity_curve = [capital]
    trades = []
    busy_until = -1
    signals = normalize_signals(signals)
    if operation == "long_only":
        signals = signals[signals["side"] == Side.LONG]
    if operation == "short_only":
        signals = signals[signals["side"] == Side.SHORT]

    # ponytail: independent asset/timeframe runs; add portfolio state only when cross-asset sizing matters.
    for _, signal in signals.iterrows():
        signal_entry = signal.get("entry")
        close_entry = pd.notna(signal_entry)
        entry_i = data.index.searchsorted(signal["time"], side="left" if close_entry else "right")
        if entry_i >= len(data) or entry_i <= busy_until:
            continue
        if close_entry and data.index[entry_i] != signal["time"]:
            continue

        side = Side(signal["side"])
        raw_entry = float(signal_entry if close_entry else data.iloc[entry_i]["open"])
        net_entry = adjusted_entry(raw_entry, side, asset_cfg, with_costs)
        stop = float(signal["stop"])
        if (side == Side.LONG and stop >= raw_entry) or (side == Side.SHORT and stop <= raw_entry):
            continue
        risk_points = abs(raw_entry - stop)
        qty = rounded_qty((net_equity * risk_pct / 100) / (risk_points * asset_cfg.point_value), asset_cfg.qty_step)
        if qty < asset_cfg.min_qty:
            continue

        direction = 1 if side == Side.LONG else -1
        target_specs = partial_targets(qty, risk_reward_ratio, asset_cfg.qty_step, asset_cfg.min_qty) if exit_mode == "partial" else [(risk_reward_ratio, qty)]
        if not target_specs:
            continue
        targets = [
            {"r": target_r, "price": raw_entry + direction * risk_points * target_r, "qty": target_qty}
            for target_r, target_qty in target_specs
        ]
        trade = {
            "asset": asset,
            "timeframe": timeframe,
            "risk_reward_ratio": risk_reward_ratio,
            "exit_mode": exit_mode,
            "signal_time": signal["time"],
            "entry_i": entry_i,
            "entry_time": data.index[entry_i],
            "side": side,
            "raw_entry": raw_entry,
            "net_entry": net_entry,
            "entry": raw_entry,
            "stop": stop,
            "target": targets[-1]["price"],
            "targets": targets,
            "initial_risk": risk_points,
            "qty": qty,
            "mfe_r": 0.0,
        }
        for column in ("plot_start_time", "orb_high", "orb_low"):
            if column in signal and pd.notna(signal[column]):
                trade[column] = signal[column]
        current_stop = stop
        remaining_qty = qty
        next_target = 0
        exits = []

        for i in range(entry_i + int(close_entry), len(data)):
            bar = data.iloc[i]
            against = float(bar["low"] if side == Side.LONG else bar["high"])
            favor = float(bar["high"] if side == Side.LONG else bar["low"])
            stop_hit = against * direction <= current_stop * direction
            if stop_hit:
                exits.append(exit_fill(asset_cfg, trade, remaining_qty, current_stop, i, data.index[i], ExitReason.STOP, with_costs))
                trade = finalize_trade(trade, exits)
                break
            while next_target < len(targets) and favor * direction >= targets[next_target]["price"] * direction:
                target = targets[next_target]
                fill_qty = min(remaining_qty, target["qty"])
                exits.append(exit_fill(asset_cfg, trade, fill_qty, target["price"], i, data.index[i], ExitReason.TARGET, with_costs, target["r"]))
                remaining_qty = round(remaining_qty - fill_qty, 8)
                trade["mfe_r"] = max(float(trade["mfe_r"]), target["r"])
                next_target += 1
            if remaining_qty <= 0:
                trade = finalize_trade(trade, exits)
                break
            favorable_r = (favor - raw_entry) * direction / risk_points
            trade["mfe_r"] = max(float(trade["mfe_r"]), favorable_r)
            if exit_mode == "trailing" and favor * direction >= (raw_entry + direction * risk_points) * direction:
                next_stop = favor - direction * risk_points * 0.5
                current_stop = max(current_stop, next_stop) if side == Side.LONG else min(current_stop, next_stop)
        else:
            # ponytail: incomplete trades are discarded; add partial/open-trade accounting only if research needs it.
            busy_until = len(data) - 1
            continue

        gross_equity += trade["gross_pnl"]
        net_equity += trade["net_pnl"]
        gross_equity_curve.append(gross_equity)
        net_equity_curve.append(net_equity)
        busy_until = trade["exit_i"]
        trades.append(trade)

    return make_metrics(asset, timeframe, gross_equity_curve, net_equity_curve, with_costs), trades


def _curve_metrics(equity_curve: list[float]) -> dict:
    curve = pd.Series(equity_curve, dtype="float64")
    returns = curve.pct_change().dropna()
    total_return = (curve.iloc[-1] / curve.iloc[0] - 1) * 100
    drawdown = (curve / curve.cummax() - 1) * 100
    max_dd = abs(float(drawdown.min()))
    sharpe = 0.0
    if len(returns) > 1 and float(returns.std()) != 0:
        sharpe = math.sqrt(len(returns)) * float(returns.mean() / returns.std())
    return {
        "Return": round(float(total_return), 2),
        "Max DD": round(max_dd, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Return / DD": round(float(total_return) / max_dd, 2) if max_dd else 0.0,
    }


def make_metrics(asset: str, timeframe: str, gross_equity_curve: list[float], net_equity_curve: list[float], with_costs: bool) -> dict:
    gross = _curve_metrics(gross_equity_curve)
    net = _curve_metrics(net_equity_curve)
    return {"Asset": asset, "TF": timeframe, **(net if with_costs else gross), "Gross": gross, "Net": net}


def add_trade_counts(metrics: dict, trades: list[dict], operation: str) -> dict:
    stats = trade_stats(trades)
    metrics = {
        "Trades": len(trades),
        "W": stats["Wins"],
        "BE": stats["BE"],
        "L": stats["Losses"],
        "Win Rate": stats["Win Rate"],
        "Expectancy R": stats["Expectancy R"],
        "Avg Duration": stats["Avg Duration"],
        **metrics,
    }
    if operation == "all":
        metrics["Long"] = sum(1 for trade in trades if trade["side"] == Side.LONG)
        metrics["Short"] = sum(1 for trade in trades if trade["side"] == Side.SHORT)
    return metrics


def result_columns(operation: str) -> list[str]:
    columns = ["Asset", "TF", "RR", "Exit Mode", "Trades"]
    if operation == "all":
        columns += ["Long", "Short"]
    return columns + ["W", "BE", "L", "Win Rate", "Expectancy R", "Avg Duration", "Return", "Max DD", "Sharpe Ratio", "Return / DD"]


def self_check() -> None:
    def verify(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    try:
        risk_reward_ratios("1RR")
    except ValueError:
        pass
    else:
        raise RuntimeError("non-numeric risk_reward_ratio was accepted")

    idx = pd.date_range("2025-01-01", periods=4, freq="h")
    cfg = AssetConfig(ticker="TEST", point_value=1.0, qty_step=1.0, min_qty=1.0, spread_points=0.0, slippage_points=0.0, commission_per_side=0.0)
    base = pd.DataFrame(
        {
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 102.0, 100.0, 100.0],
            "low": [100.0, 99.5, 100.0, 100.0],
            "close": [100.0, 101.0, 100.0, 100.0],
            "volume": [0, 0, 0, 0],
        },
        index=idx,
    )
    signals = pd.DataFrame([{"time": idx[0], "side": "long", "stop": 99}])
    metrics, trades = run_backtest(base, signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.TARGET and metrics["Return"] > 0, "long target trade failed")

    stop_data = base.copy()
    stop_data.loc[idx[1], ["high", "low"]] = [100.5, 98.5]
    metrics, trades = run_backtest(stop_data, signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.STOP and metrics["Return"] < 0, "stop trade failed")

    both_data = base.copy()
    both_data.loc[idx[1], ["high", "low"]] = [102, 98]
    _, trades = run_backtest(both_data, signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.STOP, "same-bar stop priority failed")

    close_entry_data = base.copy()
    close_entry_data.loc[idx[1], ["high", "low"]] = [102.0, 100.5]
    close_entry = pd.DataFrame([{"time": idx[0], "side": "long", "entry": 101.0, "stop": 100.0}])
    _, trades = run_backtest(close_entry_data, close_entry, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["entry_i"] == 0 and trades[0]["entry"] == 101.0 and trades[0]["exit_reason"] == ExitReason.TARGET, "close-entry signal failed")

    trail_data = base.copy()
    trail_data.loc[idx[1], ["high", "low"]] = [101.2, 100.0]
    trail_data.loc[idx[2], ["high", "low"]] = [101.3, 100.6]
    _, trades = run_backtest(trail_data, signals, asset="TEST", timeframe="1h", risk_reward_ratio=2, exit_mode="trailing", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.STOP and abs(trades[0]["exit"] - 100.7) < 0.000001, "trailing stop failed")
    trail_analytics = analyze_trades(trades, "trailing")["managed"]
    verify(abs(trades[0]["realized_r"] - 0.7) < 0.000001, "trailing realized R failed")
    verify(abs(trades[0]["mfe_r"] - 1.2) < 0.000001 and abs(trades[0]["giveback_r"] - 0.5) < 0.000001, "trailing excursion failed")
    verify(trail_analytics["Target Completions"] == 0 and trail_analytics["Stop Completions"] == 1, "trailing exit counts failed")
    _, target_trades = run_backtest(base, signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="trailing", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    target_analytics = analyze_trades(target_trades, "trailing")["managed"]
    verify(target_analytics["Target Completions"] == 1 and target_analytics["Stop Completions"] == 0, "trailing target count failed")

    partial_idx = pd.date_range("2025-01-02", periods=4, freq="h")
    partial_data = pd.DataFrame(
        {
            "open": [100.0] * 4,
            "high": [100.0, 101.1, 102.1, 103.1],
            "low": [100.0, 99.5, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [0] * 4,
        },
        index=partial_idx,
    )
    partial_signal = pd.DataFrame([{"time": partial_idx[0], "side": "long", "entry": 100.0, "stop": 99.0}])
    _, partial_trades = run_backtest(partial_data, partial_signal, asset="TEST", timeframe="1h", risk_reward_ratio=3, exit_mode="partial", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    partial_trade = partial_trades[0]
    verify([fill["qty"] for fill in partial_trade["exits"]] == [33.0, 33.0, 34.0], "partial quantities failed")
    verify(abs(partial_trade["realized_r"] - 2.01) < 0.000001 and partial_trade["gross_pnl"] == 201.0, "partial realized R failed")
    partial_analytics = analyze_trades(partial_trades, "partial")["managed"]
    verify(partial_analytics["Target Completions"] == 1 and partial_analytics["Avg Realized R"] == 2.01, "partial analytics failed")
    verify(partial_targets(4, 2.5, 1, 1) == [(1.0, 2), (2.5, 2)], "decimal RR partial targets failed")
    verify(partial_targets(4, 3, 1, 1) == [(1.0, 1), (2.0, 1), (3, 2)] and not partial_targets(2, 3, 1, 1), "partial quantity rounding failed")

    partial_stop_data = partial_data.copy()
    partial_stop_data.loc[partial_idx[2], ["high", "low"]] = [100.5, 98.5]
    _, partial_stop_trades = run_backtest(partial_stop_data, partial_signal, asset="TEST", timeframe="1h", risk_reward_ratio=3, exit_mode="partial", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    partial_stop = partial_stop_trades[0]
    verify(abs(partial_stop["realized_r"] + 0.34) < 0.000001 and partial_stop["outcome"] == "loss", "partial stop aggregation failed")
    verify([fill["qty"] for fill in partial_stop["exits"]] == [33.0, 67.0], "partial stop quantity failed")

    sample_trades = [
        {"side": Side.LONG, "realized_r": 1.0, "entry_time": pd.Timestamp("2025-01-01 23:30:00-02:00"), "holding_duration": pd.Timedelta(hours=1)},
        {"side": Side.SHORT, "realized_r": 0.0, "entry_time": pd.Timestamp("2025-01-02 04:00:00"), "holding_duration": pd.Timedelta(hours=2)},
        {"side": Side.SHORT, "realized_r": -1.0, "entry_time": pd.Timestamp("2025-01-03 04:00:00"), "holding_duration": pd.Timedelta(hours=3)},
        {"side": Side.LONG, "realized_r": -0.5, "entry_time": pd.Timestamp("2025-02-01 04:00:00"), "holding_duration": pd.Timedelta(hours=4)},
    ]
    analytics = analyze_trades(sample_trades, "fixed")
    overall, long_stats, short_stats = analytics["outcomes"]
    verify((overall["Wins"], overall["BE"], overall["Losses"], overall["Win Rate"]) == (1, 1, 2, 25.0), "outcome counts failed")
    verify(overall["Expectancy R"] == -0.125 and overall["Max Losing Streak"] == 2, "expectancy or streak failed")
    verify(overall["Avg Duration"] == "2h 30m" and overall["Median Duration"] == "2h 30m", "duration analytics failed")
    verify((long_stats["Wins"], long_stats["Losses"]) == (1, 1) and (short_stats["BE"], short_stats["Losses"]) == (1, 1), "side outcomes failed")
    verify(analytics["daily"][0]["Period"] == "2025-01-02" and analytics["daily"][0]["Trades"] == 2, "UTC entry-day analytics failed")
    verify(analytics["monthly"][0]["Period"] == "2025-01" and analytics["monthly"][0]["Trades"] == 3, "entry-month analytics failed")
    verify(classify_outcome(1e-10) == "breakeven" and not analyze_trades([], "trailing")["outcomes"][0]["Trades"], "breakeven or empty analytics failed")

    cost_idx = pd.date_range("2025-02-01", periods=6, freq="h")
    cost_data = pd.DataFrame(
        {
            "open": [100.0] * 6,
            "high": [100.0, 101.1, 100.0, 100.0, 101.1, 100.0],
            "low": [100.0, 99.5, 100.0, 100.0, 99.5, 100.0],
            "close": [100.0] * 6,
            "volume": [0] * 6,
        },
        index=cost_idx,
    )
    cost_signals = pd.DataFrame(
        [
            {"time": cost_idx[0], "side": "long", "entry": 100.0, "stop": 99.0},
            {"time": cost_idx[3], "side": "long", "entry": 100.0, "stop": 99.0},
        ]
    )
    cost_cfg = AssetConfig(ticker="TEST", point_value=1.0, qty_step=1.0, min_qty=1.0, spread_points=0.2, slippage_points=0.1, commission_per_side=0.1)
    gross_metrics, gross_trades = run_backtest(cost_data, cost_signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cost_cfg)
    net_metrics, net_trades = run_backtest(cost_data, cost_signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=True, asset_cfg=cost_cfg)
    verify(gross_trades[0]["target"] == net_trades[0]["target"] == 101.0 and gross_trades[0]["raw_exit"] == net_trades[0]["raw_exit"], "costs changed raw execution")
    verify(gross_trades[0]["realized_r"] == net_trades[0]["realized_r"] == 1.0 and net_trades[0]["outcome"] == "win", "costs changed outcomes")
    verify(gross_trades[0]["qty"] == net_trades[0]["qty"] and net_trades[1]["qty"] < gross_trades[1]["qty"], "net-equity sizing failed")
    verify(net_metrics["Gross"]["Return"] > net_metrics["Net"]["Return"] and gross_metrics["Return"] > net_metrics["Return"], "gross/net metrics failed")
    _, net_partial_trades = run_backtest(partial_data, partial_signal, asset="TEST", timeframe="1h", risk_reward_ratio=3, exit_mode="partial", operation="all", risk_pct=1, capital=10000, with_costs=True, asset_cfg=cost_cfg)
    verify(net_partial_trades[0]["realized_r"] == partial_trade["realized_r"] and net_partial_trades[0]["gross_pnl"] > net_partial_trades[0]["net_pnl"], "partial cost accounting failed")

    from types import SimpleNamespace

    hook = SimpleNamespace(calculate_metrics=lambda data, signals, trades, asset, timeframe, params: {"Signals": len(signals), "RR": params["risk_reward_ratio"]})
    verify(strategy_metrics(hook, base, signals, [], "TEST", "1h", {"risk_reward_ratio": 2}) == {"Signals": 1, "RR": 2}, "strategy metrics hook failed")
    try:
        strategy_metrics(SimpleNamespace(calculate_metrics=lambda *args: []), base, signals, [], "TEST", "1h", {})
    except ValueError:
        pass
    else:
        raise RuntimeError("invalid strategy metrics were accepted")

    metrics, trades = run_backtest(base, pd.DataFrame(), asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(not trades and metrics["Return"] == 0 and metrics["Max DD"] == 0, "empty signals failed")

    unresolved = base.copy()
    unresolved.loc[idx[1], ["high", "low"]] = [100.5, 99.5]
    metrics, trades = run_backtest(unresolved, signals, asset="TEST", timeframe="1h", risk_reward_ratio=1, exit_mode="fixed", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(not trades and metrics["Return"] == 0, "unresolved trade handling failed")
    verify("Long" in result_columns("all") and "Long" not in result_columns("long_only"), "operation columns failed")

    from .reports import format_metric, metric_class

    verify(format_metric("Return", 1.234) == "+1.23%" and metric_class("Return", -1) == "bad", "report metric formatting failed")
    try:
        normalize_signals(pd.DataFrame([{"time": idx[0], "side": "buy", "stop": 99}]))
    except ValueError:
        pass
    else:
        raise RuntimeError("non-canonical signal side was accepted")

    from tempfile import TemporaryDirectory
    from pathlib import Path

    from .data import load_local_data

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
        verify(len(local) == 2 and local.iloc[0]["open"] == 10 and local.iloc[0]["close"] == 21, "timestamp local resample failed")
        verify(local.iloc[0]["volume"] == 3 and getattr(local.index, "tz", None) is None, "timestamp local normalization failed")

        futures_dir = data_dir / "futures" / "FUT"
        futures_dir.mkdir(parents=True)
        (futures_dir / "FUT.csv").write_text(
            "\n".join(
                [
                    "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol",
                    "2025-01-01T00:00:00.000000000Z,33,1,2,200,201,199,200.5,1,FUTH6",
                    "2025-01-01T00:00:00.000000000Z,33,1,1,100,101,99,100.5,7,FUTZ5",
                ]
            ),
            encoding="utf-8",
        )
        local = load_local_data("FUT", "1m", "max", data_dir=data_dir)
        verify(len(local) == 1 and local.index.name == "time" and local.iloc[0]["close"] == 100.5, "ts_event local parsing failed")

        try:
            load_local_data("FUT", "1m", "60mo", data_dir=data_dir)
        except ValueError:
            pass
        else:
            raise RuntimeError("unsupported local period was accepted")
    print("self-check ok")
