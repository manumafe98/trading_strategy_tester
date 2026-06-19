from __future__ import annotations

from statistics import mean, median

import pandas as pd

from .models import ExitReason, Side


BREAKEVEN_TOLERANCE = 1e-9
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def classify_outcome(realized_r: float) -> str:
    if realized_r > BREAKEVEN_TOLERANCE:
        return "win"
    if realized_r < -BREAKEVEN_TOLERANCE:
        return "loss"
    return "breakeven"


def format_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts[:2])


def trade_stats(trades: list[dict], label: str = "All", value_key: str = "realized_r") -> dict:
    values = [float(trade[value_key]) for trade in trades]
    outcomes = [classify_outcome(value) for value in values]
    durations = [pd.Timedelta(trade["holding_duration"]).total_seconds() for trade in trades]
    wins = [value for value, outcome in zip(values, outcomes) if outcome == "win"]
    losses = [value for value, outcome in zip(values, outcomes) if outcome == "loss"]
    losing_streak = max_losing_streak = 0
    for outcome in outcomes:
        losing_streak = losing_streak + 1 if outcome == "loss" else 0
        max_losing_streak = max(max_losing_streak, losing_streak)
    count = len(trades)
    return {
        "Group": label,
        "Trades": count,
        "Wins": outcomes.count("win"),
        "BE": outcomes.count("breakeven"),
        "Losses": outcomes.count("loss"),
        "Win Rate": round(outcomes.count("win") / count * 100, 2) if count else 0.0,
        "Avg Win R": round(mean(wins), 4) if wins else 0.0,
        "Avg Loss R": round(mean(losses), 4) if losses else 0.0,
        "Expectancy R": round(mean(values), 4) if values else 0.0,
        "Max Losing Streak": max_losing_streak,
        "Avg Duration": format_duration(mean(durations)) if durations else "0s",
        "Median Duration": format_duration(median(durations)) if durations else "0s",
    }


def period_stats(trades: list[dict], period: str) -> list[dict]:
    labels = WEEKDAYS if period == "weekday" else MONTHS if period == "month" else None
    if labels is None:
        raise ValueError(f"Unknown calendar period: {period}")
    grouped: dict[str, list[dict]] = {}
    for trade in trades:
        timestamp = pd.Timestamp(trade["entry_time"])
        timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
        key = labels[timestamp.weekday()] if period == "weekday" else labels[timestamp.month - 1]
        grouped.setdefault(key, []).append(trade)
    rows = []
    for key in labels:
        if key not in grouped:
            continue
        items = grouped[key]
        stats = trade_stats(items)
        rows.append({"Period": key, **{name: stats[name] for name in ("Trades", "Wins", "BE", "Losses", "Win Rate")}})
    return rows


def analyze_trades(trades: list[dict], exit_mode: str) -> dict:
    outcome_rows = [
        trade_stats(trades),
        trade_stats([trade for trade in trades if trade["side"] == Side.LONG], "Long"),
        trade_stats([trade for trade in trades if trade["side"] == Side.SHORT], "Short"),
    ]
    managed_trades = trades if exit_mode in {"trailing", "partial"} else []
    return {
        "outcomes": outcome_rows,
        "weekday": period_stats(trades, "weekday"),
        "month": period_stats(trades, "month"),
        "managed": {
            "Mode": exit_mode,
            "Target Completions": sum(trade["exit_reason"] == ExitReason.TARGET for trade in managed_trades),
            "Stop Completions": sum(trade["exit_reason"] == ExitReason.STOP for trade in managed_trades),
            "Avg Realized R": round(mean(float(trade["realized_r"]) for trade in managed_trades), 4) if managed_trades else 0.0,
            "Avg MFE R": round(mean(float(trade["mfe_r"]) for trade in managed_trades), 4) if managed_trades else 0.0,
            "Avg Giveback R": round(mean(float(trade["giveback_r"]) for trade in managed_trades), 4) if managed_trades else 0.0,
            "trades": managed_trades,
        },
    }


def strategy_metrics(strategy, data, signals, trades, asset: str, timeframe: str, params: dict) -> dict:
    hook = getattr(strategy, "calculate_metrics", None)
    if not callable(hook):
        return {}
    metrics = hook(data, signals, trades, asset, timeframe, params)
    if not isinstance(metrics, dict):
        raise ValueError("calculate_metrics must return a flat dict of scalar values")
    if any(not isinstance(name, str) or not name or not pd.api.types.is_scalar(value) for name, value in metrics.items()):
        raise ValueError("calculate_metrics must return a flat dict of scalar values with non-empty string names")
    return metrics


__all__ = ["analyze_trades", "classify_outcome", "format_duration", "strategy_metrics", "trade_stats"]
