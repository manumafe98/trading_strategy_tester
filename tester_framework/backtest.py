from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from .analytics import classify_outcome, trade_stats
from .models import AssetConfig, ExitReason, Side
from .utils import csv_items


DEFAULT_RISK = 1.0

__all__ = ["add_trade_counts", "result_columns", "risk_for", "risk_reward_ratios", "run_backtest"]


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
            key = key.strip().upper()
            if not key:
                raise ValueError("--risk asset override must have a name")
        else:
            value = part
            key = None
        try:
            parsed = float(value)
        except ValueError:
            raise ValueError(f"--risk must be numeric, got: {value}") from None
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError("--risk must be finite and positive")
        if key is None:
            default = parsed
        else:
            overrides[key] = parsed
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


def _decimal_places(step: float) -> int:
    return abs(Decimal(str(step)).as_tuple().exponent)


def rounded_qty(qty: float, step: float) -> float:
    places = _decimal_places(step)
    return round(math.floor(qty / step) * step, places)


def tick_price(price: float, tick_size: float, direction: int) -> float:
    tick = Decimal(str(tick_size))
    units = Decimal(str(price)) / tick
    rounding = ROUND_CEILING if direction > 0 else ROUND_FLOOR
    return float(units.to_integral_value(rounding=rounding) * tick)


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
    gross_pnl = sum(fill["gross_pnl"] for fill in exits)
    net_pnl = sum(fill["net_pnl"] for fill in exits)
    risk_value = trade["initial_risk"] * trade["qty"] * trade["point_value"]
    net_realized_r = net_pnl / risk_value
    closed.update(
        {
            "exits": exits,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "pnl": net_pnl,
            "realized_r": realized_r,
            "net_realized_r": net_realized_r,
            "outcome": classify_outcome(realized_r),
            "net_outcome": classify_outcome(net_realized_r),
            "giveback_r": max(0.0, float(trade["mfe_r"]) - max(realized_r, 0.0)),
            "holding_duration": pd.Timestamp(last["exit_time"]) - pd.Timestamp(trade["entry_time"]),
        }
    )
    return closed


def _prepare_trade(
    signal: pd.Series,
    data: pd.DataFrame,
    asset_cfg: AssetConfig,
    current_net: float,
    risk_pct: float,
    risk_reward_ratio: float,
    exit_mode: str,
    asset: str,
    timeframe: str,
    entry_i: int,
    close_entry: bool,
    with_costs: bool,
) -> dict | None:
    """Validate a signal and build the trade record, or return None if it should be discarded."""
    side = Side(signal["side"])
    raw_entry = float(signal["entry"] if close_entry else data.iloc[entry_i]["open"])
    stop = float(signal["stop"])
    if not math.isfinite(raw_entry) or not math.isfinite(stop):
        return None
    stop = tick_price(stop, asset_cfg.tick_size, -1 if side == Side.LONG else 1)
    net_entry = adjusted_entry(raw_entry, side, asset_cfg, with_costs)
    if (side == Side.LONG and stop >= raw_entry) or (side == Side.SHORT and stop <= raw_entry):
        return None
    risk_points = abs(raw_entry - stop)
    qty = rounded_qty((current_net * risk_pct / 100) / (risk_points * asset_cfg.point_value), asset_cfg.qty_step)
    if qty < asset_cfg.min_qty:
        return None

    direction = 1 if side == Side.LONG else -1
    target_specs = partial_targets(qty, risk_reward_ratio, asset_cfg.qty_step, asset_cfg.min_qty) if exit_mode == "partial" else [(risk_reward_ratio, qty)]
    if not target_specs:
        return None
    targets = [
        {
            "r": target_r,
            "price": tick_price(raw_entry + direction * risk_points * target_r, asset_cfg.tick_size, direction),
            "qty": target_qty,
        }
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
        "point_value": asset_cfg.point_value,
        "tick_size": asset_cfg.tick_size,
        "qty": qty,
        "mfe_r": 0.0,
    }
    for column in ("plot_start_time", "orb_high", "orb_low"):
        if column in signal and pd.notna(signal[column]):
            trade[column] = signal[column]
    return trade


def _update_trailing_stop(
    current_stop: float,
    favor: float,
    raw_entry: float,
    risk_points: float,
    side: Side,
    tick_size: float,
) -> float:
    """Move the trailing stop to 50% of the favorable excursion once 1R is reached."""
    direction = 1 if side == Side.LONG else -1
    if (favor - raw_entry) * direction >= risk_points:
        next_stop = tick_price(favor - direction * risk_points * 0.5, tick_size, -direction)
        return max(current_stop, next_stop) if side == Side.LONG else min(current_stop, next_stop)
    return current_stop


def _update_peak_r(trade: dict, exits: list[dict], remaining_qty: float, price: float) -> None:
    realized = sum(fill["qty"] / trade["qty"] * fill["realized_r"] for fill in exits)
    direction = 1 if trade["side"] == Side.LONG else -1
    open_r = (price - trade["raw_entry"]) * direction / trade["initial_risk"]
    trade["mfe_r"] = max(float(trade["mfe_r"]), realized + remaining_qty / trade["qty"] * open_r)


def _bar_path(bar: pd.Series, side: Side) -> tuple[float, float, float, float]:
    opened, high, low, close = map(float, (bar["open"], bar["high"], bar["low"], bar["close"]))
    high_distance = abs(high - opened)
    low_distance = abs(opened - low)
    if high_distance < low_distance or (high_distance == low_distance and side == Side.SHORT):
        return opened, high, low, close
    return opened, low, high, close


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
    execution_timeframe: str | None = None,
) -> tuple[dict, list[dict]]:
    if not math.isfinite(risk_reward_ratio) or risk_reward_ratio <= 0:
        raise ValueError("--risk_reward_ratio must be positive")
    if exit_mode not in {"fixed", "trailing", "partial"}:
        raise ValueError(f"Unknown exit mode: {exit_mode}")
    if operation not in {"all", "long_only", "short_only"}:
        raise ValueError(f"Unknown operation: {operation}")
    if not math.isfinite(risk_pct) or risk_pct <= 0:
        raise ValueError("--risk must be finite and positive")
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("--capital must be finite and positive")
    if data.empty:
        raise ValueError(f"No data for {asset} {timeframe}")
    missing_data = {"open", "high", "low", "close"} - set(data.columns)
    if missing_data:
        raise ValueError(f"Data missing columns: {', '.join(sorted(missing_data))}")
    prices = data[["open", "high", "low", "close"]]
    if not np.isfinite(prices.to_numpy(dtype=float)).all():
        raise ValueError("Data prices must be finite")
    if ((prices["high"] < prices[["open", "low", "close"]].max(axis=1)) | (
        prices["low"] > prices[["open", "high", "close"]].min(axis=1)
    )).any():
        raise ValueError("Data contains invalid OHLC ranges")
    annualization_timeframe = execution_timeframe or timeframe
    if annualization_timeframe not in asset_cfg.bars_per_year:
        raise ValueError(f"Missing bars_per_year for {asset} {annualization_timeframe}")

    bar_count = len(data)
    bar_gross_equity = np.full(bar_count, capital, dtype=float)
    bar_net_equity = np.full(bar_count, capital, dtype=float)
    current_gross = capital
    current_net = capital
    last_bar_idx = 0
    trades = []
    discarded_signals = 0
    unresolved_trades = 0
    busy_until = -1
    try:
        signals = normalize_signals(signals)
    except ValueError as exc:
        raise ValueError(f"{asset} {timeframe}: {exc}") from exc
    if operation == "long_only":
        signals = signals[signals["side"] == Side.LONG]
    if operation == "short_only":
        signals = signals[signals["side"] == Side.SHORT]

    # ponytail: independent asset/timeframe runs; add portfolio state only when cross-asset sizing matters.
    for _, signal in signals.iterrows():
        signal_entry = signal.get("entry")
        close_entry = pd.notna(signal_entry)
        entry_i = data.index.searchsorted(signal["time"], side="left" if close_entry else "right")
        if entry_i >= bar_count or entry_i <= busy_until:
            discarded_signals += 1
            continue
        if close_entry and data.index[entry_i] != signal["time"]:
            discarded_signals += 1
            continue

        # Fill flat equity for the period before this potential trade.
        if last_bar_idx < entry_i:
            bar_gross_equity[last_bar_idx:entry_i] = current_gross
            bar_net_equity[last_bar_idx:entry_i] = current_net
            last_bar_idx = entry_i

        trade = _prepare_trade(
            signal, data, asset_cfg, current_net, risk_pct, risk_reward_ratio, exit_mode,
            asset, timeframe, entry_i, close_entry, with_costs,
        )
        if trade is None:
            discarded_signals += 1
            continue

        side = trade["side"]
        direction = 1 if side == Side.LONG else -1
        raw_entry = trade["raw_entry"]
        risk_points = trade["initial_risk"]
        qty = trade["qty"]
        current_stop = trade["stop"]
        remaining_qty = qty
        next_target = 0
        exits = []
        targets = trade["targets"]
        start_i = entry_i + int(close_entry)

        resolved = False
        for i in range(start_i, bar_count):
            bar = data.iloc[i]
            path = _bar_path(bar, side)
            opened = path[0]

            # Orders crossed between the previous close and this open fill at the open.
            if opened * direction <= current_stop * direction:
                exits.append(exit_fill(asset_cfg, trade, remaining_qty, opened, i, data.index[i], ExitReason.STOP, with_costs))
                trade = finalize_trade(trade, exits)
                resolved = True
                break
            _update_peak_r(trade, exits, remaining_qty, opened)
            while next_target < len(targets) and opened * direction >= targets[next_target]["price"] * direction:
                target = targets[next_target]
                fill_qty = min(remaining_qty, target["qty"])
                exits.append(exit_fill(asset_cfg, trade, fill_qty, opened, i, data.index[i], ExitReason.TARGET, with_costs, target["r"]))
                remaining_qty = max(0.0, round(remaining_qty - fill_qty, 8))
                next_target += 1
            if remaining_qty <= 0:
                trade = finalize_trade(trade, exits)
                resolved = True
                break
            if exit_mode == "trailing":
                current_stop = _update_trailing_stop(current_stop, opened, raw_entry, risk_points, side, asset_cfg.tick_size)

            for start, end in zip(path, path[1:]):
                if (end - start) * direction > 0:
                    while (
                        next_target < len(targets)
                        and start * direction < targets[next_target]["price"] * direction <= end * direction
                    ):
                        target = targets[next_target]
                        _update_peak_r(trade, exits, remaining_qty, target["price"])
                        fill_qty = min(remaining_qty, target["qty"])
                        exits.append(
                            exit_fill(
                                asset_cfg,
                                trade,
                                fill_qty,
                                target["price"],
                                i,
                                data.index[i],
                                ExitReason.TARGET,
                                with_costs,
                                target["r"],
                            )
                        )
                        remaining_qty = max(0.0, round(remaining_qty - fill_qty, 8))
                        next_target += 1
                    if remaining_qty <= 0:
                        trade = finalize_trade(trade, exits)
                        resolved = True
                        break
                    _update_peak_r(trade, exits, remaining_qty, end)
                    if exit_mode == "trailing":
                        current_stop = _update_trailing_stop(
                            current_stop, end, raw_entry, risk_points, side, asset_cfg.tick_size
                        )
                elif end * direction <= current_stop * direction <= start * direction:
                    exits.append(
                        exit_fill(
                            asset_cfg,
                            trade,
                            remaining_qty,
                            current_stop,
                            i,
                            data.index[i],
                            ExitReason.STOP,
                            with_costs,
                        )
                    )
                    trade = finalize_trade(trade, exits)
                    resolved = True
                    break

            if resolved:
                break

            close = path[-1]
            realized_gross = sum(fill["gross_pnl"] for fill in exits)
            realized_net = sum(fill["net_pnl"] for fill in exits)
            gross_mtm = (close - raw_entry) * direction * remaining_qty * asset_cfg.point_value
            net_exit = adjusted_exit(close, side, asset_cfg, with_costs)
            net_mtm = (net_exit - trade["net_entry"]) * direction * remaining_qty * asset_cfg.point_value
            if with_costs:
                net_mtm -= asset_cfg.commission_per_side * remaining_qty * 2
            bar_gross_equity[i] = current_gross + realized_gross + gross_mtm
            bar_net_equity[i] = current_net + realized_net + net_mtm

        if not resolved:
            # ponytail: incomplete trades are discarded; add partial/open-trade accounting only if research needs it.
            unresolved_trades += 1
            bar_gross_equity[last_bar_idx:bar_count] = current_gross
            bar_net_equity[last_bar_idx:bar_count] = current_net
            busy_until = bar_count - 1
            continue

        # Settle the completed trade into the bar-level equity curves.
        current_gross += trade["gross_pnl"]
        current_net += trade["net_pnl"]
        bar_gross_equity[trade["exit_i"]] = current_gross
        bar_net_equity[trade["exit_i"]] = current_net
        last_bar_idx = trade["exit_i"] + 1
        busy_until = trade["exit_i"]
        trades.append(trade)

    # Fill any remaining flat equity after the last completed trade.
    if last_bar_idx < bar_count:
        bar_gross_equity[last_bar_idx:bar_count] = current_gross
        bar_net_equity[last_bar_idx:bar_count] = current_net

    bars_per_year = asset_cfg.bars_per_year[annualization_timeframe]
    gross_curve = np.concatenate(([capital], bar_gross_equity))
    net_curve = np.concatenate(([capital], bar_net_equity))
    return make_metrics(asset, timeframe, gross_curve, net_curve, with_costs, bars_per_year, discarded_signals, unresolved_trades), trades


def _curve_metrics(equity_curve: np.ndarray, bars_per_year: int) -> dict:
    curve = pd.Series(equity_curve, dtype="float64")
    returns = curve.pct_change(fill_method=None).dropna()
    total_return = (curve.iloc[-1] / curve.iloc[0] - 1) * 100
    drawdown = (curve / curve.cummax() - 1) * 100
    max_dd = abs(float(drawdown.min()))
    sharpe = float("nan")
    if (curve > 0).all() and len(returns) > 1 and np.isfinite(returns).all():
        std = float(returns.std())
        if math.isfinite(std) and std > 0:
            sharpe = math.sqrt(bars_per_year) * float(returns.mean() / std)
    return {
        "Return": round(float(total_return), 2),
        "Max DD": round(max_dd, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Return / DD": round(float(total_return) / max_dd, 2) if max_dd else float("nan"),
    }


def make_metrics(
    asset: str,
    timeframe: str,
    gross_equity_curve: np.ndarray,
    net_equity_curve: np.ndarray,
    with_costs: bool,
    bars_per_year: int,
    discarded_signals: int = 0,
    unresolved_trades: int = 0,
) -> dict:
    gross = _curve_metrics(gross_equity_curve, bars_per_year)
    net = _curve_metrics(net_equity_curve, bars_per_year)
    return {
        "Asset": asset,
        "TF": timeframe,
        "Discarded": discarded_signals,
        "Unresolved": unresolved_trades,
        **(net if with_costs else gross),
        "Gross": gross,
        "Net": net,
    }


def add_trade_counts(metrics: dict, trades: list[dict], operation: str, with_costs: bool = False) -> dict:
    stats = trade_stats(trades)
    net_stats = trade_stats(trades, "All", "net_realized_r") if with_costs else stats

    def value(name: str):
        return (stats[name], net_stats[name]) if with_costs else stats[name]

    metrics = {
        "Trades": len(trades),
        "W": value("Wins"),
        "BE": value("BE"),
        "L": value("Losses"),
        "Win Rate": value("Win Rate"),
        "Expectancy R": value("Expectancy R"),
        "Avg Duration": stats["Avg Duration"],
        **metrics,
    }
    if operation == "all":
        metrics["Long"] = sum(1 for trade in trades if trade["side"] == Side.LONG)
        metrics["Short"] = sum(1 for trade in trades if trade["side"] == Side.SHORT)
    return metrics


def result_columns(operation: str) -> list[str]:
    columns = ["Asset", "TF", "RR", "Exit Mode", "Trades", "Discarded", "Unresolved"]
    if operation == "all":
        columns += ["Long", "Short"]
    return columns + ["W", "BE", "L", "Win Rate", "Expectancy R", "Avg Duration", "Return", "Max DD", "Sharpe Ratio", "Return / DD"]
