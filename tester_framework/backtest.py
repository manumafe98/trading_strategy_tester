from __future__ import annotations

import math

import pandas as pd

from .models import AssetConfig, ExitReason, Side
from .utils import clean_exit_name, csv_items


__all__ = ["add_trade_counts", "exit_rr", "result_columns", "risk_for", "run_backtest", "self_check"]
DEFAULT_RISK = 1.0


def exit_rr(exit_structure: str) -> float | None:
    key = clean_exit_name(exit_structure)
    if key in {"trailing_stop", "trailing"}:
        return None
    if key.endswith("rr"):
        return float(key[:-2])
    raise ValueError(f"Unsupported exit_structure: {exit_structure}")


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


def fill_exit(cfg: AssetConfig, trade: dict, raw_exit_price: float, exit_i: int, exit_time, reason: ExitReason, with_costs: bool) -> dict:
    side = trade["side"]
    direction = 1 if side == Side.LONG else -1
    exit_price = adjusted_exit(float(raw_exit_price), side, cfg, with_costs)
    commission = cfg.commission_per_side * trade["qty"] * 2 if with_costs else 0.0
    pnl = (exit_price - trade["entry"]) * direction * trade["qty"] * cfg.point_value - commission
    closed = trade.copy()
    closed.update(
        {
            "exit_i": exit_i,
            "exit_time": exit_time,
            "exit": exit_price,
            "exit_reason": reason,
            "pnl": pnl,
        }
    )
    return closed


def run_backtest(
    data: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    asset: str,
    timeframe: str,
    exit_structure: str,
    operation: str,
    risk_pct: float,
    capital: float,
    with_costs: bool,
    asset_cfg: AssetConfig,
) -> tuple[dict, list[dict]]:
    rr = exit_rr(exit_structure)
    equity = capital
    equity_curve = [capital]
    trades = []
    busy_until = -1
    signals = normalize_signals(signals)
    if operation == "long_only":
        signals = signals[signals["side"] == Side.LONG]
    if operation == "short_only":
        signals = signals[signals["side"] == Side.SHORT]

    # ponytail: independent asset/timeframe runs; add portfolio state only when cross-asset sizing matters.
    for _, signal in signals.iterrows():
        entry_i = data.index.searchsorted(signal["time"], side="right")
        if entry_i >= len(data) or entry_i <= busy_until:
            continue

        side = Side(signal["side"])
        entry = adjusted_entry(float(data.iloc[entry_i]["open"]), side, asset_cfg, with_costs)
        stop = float(signal["stop"])
        if (side == Side.LONG and stop >= entry) or (side == Side.SHORT and stop <= entry):
            continue
        risk_points = abs(entry - stop)
        qty = rounded_qty((equity * risk_pct / 100) / (risk_points * asset_cfg.point_value), asset_cfg.qty_step)
        if qty < asset_cfg.min_qty:
            continue

        direction = 1 if side == Side.LONG else -1
        target = None if rr is None else entry + direction * risk_points * rr
        trade = {
            "asset": asset,
            "timeframe": timeframe,
            "exit_structure": exit_structure,
            "signal_time": signal["time"],
            "entry_i": entry_i,
            "entry_time": data.index[entry_i],
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "qty": qty,
        }
        trailing_stop = stop

        for i in range(entry_i, len(data)):
            bar = data.iloc[i]
            against = float(bar["low"] if side == Side.LONG else bar["high"])
            favor = float(bar["high"] if side == Side.LONG else bar["low"])
            stop_hit = against * direction <= trailing_stop * direction
            target_hit = target is not None and favor * direction >= target * direction
            if stop_hit:
                trade = fill_exit(asset_cfg, trade, trailing_stop, i, data.index[i], ExitReason.STOP, with_costs)
                break
            if target_hit:
                trade = fill_exit(asset_cfg, trade, target, i, data.index[i], ExitReason.TARGET, with_costs)
                break
            if rr is None:
                trailing_stop = max(trailing_stop, favor - risk_points) if side == Side.LONG else min(trailing_stop, favor + risk_points)
        else:
            # ponytail: incomplete trades are discarded; add partial/open-trade accounting only if research needs it.
            busy_until = len(data) - 1
            continue

        equity += trade["pnl"]
        equity_curve.append(equity)
        busy_until = trade["exit_i"]
        trades.append(trade)

    return make_metrics(asset, timeframe, equity_curve), trades


def make_metrics(asset: str, timeframe: str, equity_curve: list[float]) -> dict:
    curve = pd.Series(equity_curve, dtype="float64")
    returns = curve.pct_change().dropna()
    total_return = (curve.iloc[-1] / curve.iloc[0] - 1) * 100
    drawdown = (curve / curve.cummax() - 1) * 100
    max_dd = abs(float(drawdown.min()))
    sharpe = 0.0
    if len(returns) > 1 and float(returns.std()) != 0:
        sharpe = math.sqrt(len(returns)) * float(returns.mean() / returns.std())
    return {
        "Asset": asset,
        "TF": timeframe,
        "Return": round(float(total_return), 2),
        "Max DD": round(max_dd, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Return / DD": round(float(total_return) / max_dd, 2) if max_dd else 0.0,
    }


def add_trade_counts(metrics: dict, trades: list[dict], operation: str) -> dict:
    metrics = {"Trades": len(trades), **metrics}
    if operation == "all":
        metrics["Long"] = sum(1 for trade in trades if trade["side"] == Side.LONG)
        metrics["Short"] = sum(1 for trade in trades if trade["side"] == Side.SHORT)
    return metrics


def result_columns(operation: str) -> list[str]:
    columns = ["Asset", "TF", "Trades"]
    if operation == "all":
        columns += ["Long", "Short"]
    return columns + ["Return", "Max DD", "Sharpe Ratio", "Return / DD"]


def self_check() -> None:
    def verify(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

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
    metrics, trades = run_backtest(base, signals, asset="TEST", timeframe="1h", exit_structure="1RR", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.TARGET and metrics["Return"] > 0, "long target trade failed")

    stop_data = base.copy()
    stop_data.loc[idx[1], ["high", "low"]] = [100.5, 98.5]
    metrics, trades = run_backtest(stop_data, signals, asset="TEST", timeframe="1h", exit_structure="1RR", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.STOP and metrics["Return"] < 0, "stop trade failed")

    both_data = base.copy()
    both_data.loc[idx[1], ["high", "low"]] = [102, 98]
    _, trades = run_backtest(both_data, signals, asset="TEST", timeframe="1h", exit_structure="1RR", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(trades[0]["exit_reason"] == ExitReason.STOP, "same-bar stop priority failed")

    metrics, trades = run_backtest(base, pd.DataFrame(), asset="TEST", timeframe="1h", exit_structure="1RR", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
    verify(not trades and metrics["Return"] == 0 and metrics["Max DD"] == 0, "empty signals failed")

    unresolved = base.copy()
    unresolved.loc[idx[1], ["high", "low"]] = [100.5, 99.5]
    metrics, trades = run_backtest(unresolved, signals, asset="TEST", timeframe="1h", exit_structure="1RR", operation="all", risk_pct=1, capital=10000, with_costs=False, asset_cfg=cfg)
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
    print("self-check ok")
