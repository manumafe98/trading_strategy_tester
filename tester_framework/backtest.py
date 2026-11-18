from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from .analytics import classify_outcome, trade_stats
from .data import session_labels
from .models import OPERATIONS, AssetConfig, ExitReason, IntrabarRule, Side, TargetFill
from .sessions import SessionSpec, session_exit_bars
from .split import entered_in_sample, walk_forward_efficiency
from .settings import OOS_FINANCIAL_COLUMNS, SPLIT_COLUMNS
from .utils import csv_items, decimal_places


DEFAULT_RISK = 1.0
CORE_SIGNAL_COLUMNS = frozenset({"time", "side", "stop", "entry", "setup_time"})
ROLL_ENTRY_BLOCK = pd.Timedelta(minutes=60)
# Bar length per low timeframe; a close entry fills at its bar's start plus this.
BAR_DURATIONS = {
    timeframe: pd.Timedelta(minutes=minutes)
    for timeframe, minutes in {"1m": 1, "2m": 2, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}.items()
}
TARGET_FILLS = tuple(rule.value for rule in TargetFill)
INTRABAR_RULES = tuple(rule.value for rule in IntrabarRule)

__all__ = ["INTRABAR_RULES", "TARGET_FILLS", "add_trade_counts", "result_columns", "risk_for", "risk_reward_ratios", "run_backtest"]


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


def risk_for(asset: str, risk: str, assets: tuple[str, ...] | list[str]) -> float:
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
            if key not in {name.upper() for name in assets}:
                raise ValueError(f"--risk override for unknown asset: {key}")
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
    if "setup_time" in signals.columns:
        signals["setup_time"] = pd.to_datetime(signals["setup_time"])
        if (signals["setup_time"] > signals["time"]).any():
            raise ValueError("Signals setup_time must not be after time")
    if "plot_start_time" in signals.columns:
        signals["plot_start_time"] = pd.to_datetime(signals["plot_start_time"])
    signals = signals.dropna(subset=["time", "side", "stop"]).copy()
    signals["side"] = signals["side"].map(Side)
    return signals.sort_values("time")


def rounded_qty(qty: float, step: float) -> float:
    places = decimal_places(step)
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


def _prepare_trade(
    signal: pd.Series,
    entry_open: float,
    entry_time,
    asset_cfg: AssetConfig,
    current_net: float,
    risk_pct: float,
    risk_reward_ratio: float,
    exit_mode: str,
    asset: str,
    timeframe: str,
    strategy: str,
    entry_i: int,
    close_entry: bool,
    with_costs: bool,
) -> dict | None:
    """Validate a signal and build the trade record, or return None if it should be discarded."""
    side = Side(signal["side"])
    raw_entry = float(signal["entry"] if close_entry else entry_open)
    stop = float(signal["stop"])
    if not math.isfinite(raw_entry) or not math.isfinite(stop):
        return None
    stop = tick_price(stop, asset_cfg.tick_size, -1 if side == Side.LONG else 1)
    net_entry = adjusted_entry(raw_entry, side, asset_cfg, with_costs)
    if (side == Side.LONG and stop >= raw_entry) or (side == Side.SHORT and stop <= raw_entry):
        return None
    risk_points = abs(raw_entry - stop)
    if round(risk_points / asset_cfg.tick_size, 6) < asset_cfg.min_risk_ticks:
        return None
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
        "strategy": strategy,
        "asset": asset,
        "timeframe": timeframe,
        "risk_reward_ratio": risk_reward_ratio,
        "exit_mode": exit_mode,
        "signal_time": signal["time"],
        "entry_i": entry_i,
        "entry_time": entry_time,
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
    # Strategy-owned signal columns pass through for plot_indicators; engine-owned keys win.
    for column, value in signal.items():
        if column not in CORE_SIGNAL_COLUMNS and column not in trade and pd.notna(value):
            trade[column] = value
    return trade


@dataclass
class _OpenTrade:
    """A filled trade while it is open: remaining quantity, next target, current stop, exits and peak R."""

    trade: dict
    cfg: AssetConfig
    with_costs: bool
    target_fill: TargetFill = TargetFill.TICK_THROUGH
    stop: float = field(init=False)
    remaining_qty: float = field(init=False)
    direction: int = field(init=False)
    next_target: int = 0
    exits: list[dict] = field(default_factory=list)
    peak_r: float = 0.0

    def __post_init__(self) -> None:
        self.stop = self.trade["stop"]
        self.remaining_qty = self.trade["qty"]
        self.direction = 1 if self.trade["side"] == Side.LONG else -1

    @property
    def closed(self) -> bool:
        return self.remaining_qty <= 0

    @property
    def target(self) -> dict | None:
        targets = self.trade["targets"]
        return targets[self.next_target] if self.next_target < len(targets) else None

    def stop_gapped(self, opened: float) -> bool:
        return opened * self.direction <= self.stop * self.direction

    def _target_reached_at(self) -> float | None:
        """The price that reaches the next target: the target on a touch, one tick beyond it under tick-through."""
        target = self.target
        if target is None:
            return None
        if self.target_fill == TargetFill.TOUCH:
            return target["price"]
        # Targets are tick-aligned, so adding the tick in Decimal lands exactly on the next tick.
        return float(Decimal(str(target["price"])) + self.direction * Decimal(str(self.cfg.tick_size)))

    def target_gapped(self, opened: float) -> bool:
        reached_at = self._target_reached_at()
        return reached_at is not None and opened * self.direction >= reached_at * self.direction

    def target_gap_price(self, opened: float) -> float:
        """A gap beyond the target fills at the open on a touch, at the target price under tick-through."""
        return opened if self.target_fill == TargetFill.TOUCH else self.target["price"]

    def target_between(self, start: float, end: float) -> bool:
        reached_at = self._target_reached_at()
        return reached_at is not None and start * self.direction < reached_at * self.direction <= end * self.direction

    def stop_between(self, start: float, end: float) -> bool:
        return end * self.direction <= self.stop * self.direction <= start * self.direction

    def stop_and_target_reachable(self, opened: float, high: float, low: float) -> bool:
        """Whether a bar opening at opened can reach both the stop and the next target."""
        favor, adverse = (high, low) if self.direction > 0 else (low, high)
        return self.stop_between(opened, adverse) and self.target_between(opened, favor)

    def _fill(self, qty: float, raw_exit_price: float, exit_i: int, exit_time, reason: ExitReason, target_r, gap_exit: bool) -> None:
        trade, cfg = self.trade, self.cfg
        raw_exit = float(raw_exit_price)
        net_exit = adjusted_exit(raw_exit, trade["side"], cfg, self.with_costs)
        commission = cfg.commission_per_side * qty * 2 if self.with_costs else 0.0
        gross_pnl = (raw_exit - trade["raw_entry"]) * self.direction * qty * cfg.point_value
        net_pnl = (net_exit - trade["net_entry"]) * self.direction * qty * cfg.point_value - commission
        realized_r = (raw_exit - trade["raw_entry"]) * self.direction / trade["initial_risk"]
        self.exits.append(
            {
                "exit_i": exit_i,
                "exit_time": exit_time,
                "raw_exit": raw_exit,
                "net_exit": net_exit,
                "exit": raw_exit,
                "exit_reason": reason,
                "qty": qty,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "realized_r": realized_r,
                "target_r": target_r,
                "gap_exit": gap_exit,
            }
        )
        self.remaining_qty = max(0.0, round(self.remaining_qty - qty, 8))

    def fill_target(self, price: float, exit_i: int, exit_time, gap_exit: bool = False) -> None:
        target = self.target
        self._fill(min(self.remaining_qty, target["qty"]), price, exit_i, exit_time, ExitReason.TARGET, target["r"], gap_exit)
        self.next_target += 1

    def close(self, price: float, exit_i: int, exit_time, reason: ExitReason, gap_exit: bool = False) -> None:
        """Exit the whole remaining quantity at one price."""
        self._fill(self.remaining_qty, price, exit_i, exit_time, reason, None, gap_exit)

    def _realized_r(self) -> float:
        return sum(fill["qty"] / self.trade["qty"] * fill["realized_r"] for fill in self.exits)

    def update_peak(self, price: float) -> None:
        trade = self.trade
        open_r = (price - trade["raw_entry"]) * self.direction / trade["initial_risk"]
        self.peak_r = max(float(self.peak_r), self._realized_r() + self.remaining_qty / trade["qty"] * open_r)

    def trail(self, favor: float) -> None:
        """Move the trailing stop to 50% of the favorable excursion once 1R is reached."""
        trade = self.trade
        risk_points = trade["initial_risk"]
        if (favor - trade["raw_entry"]) * self.direction >= risk_points:
            next_stop = tick_price(favor - self.direction * risk_points * 0.5, self.cfg.tick_size, -self.direction)
            self.stop = max(self.stop, next_stop) if self.direction > 0 else min(self.stop, next_stop)

    def marked_equity(self, price: float, gross_cash: float, net_cash: float) -> tuple[float, float]:
        """Gross and net equity with realized fills and the remaining quantity marked at price."""
        trade, cfg = self.trade, self.cfg
        realized_gross = sum(fill["gross_pnl"] for fill in self.exits)
        realized_net = sum(fill["net_pnl"] for fill in self.exits)
        gross_mtm = (price - trade["raw_entry"]) * self.direction * self.remaining_qty * cfg.point_value
        net_exit = adjusted_exit(price, trade["side"], cfg, self.with_costs)
        net_mtm = (net_exit - trade["net_entry"]) * self.direction * self.remaining_qty * cfg.point_value
        if self.with_costs:
            net_mtm -= cfg.commission_per_side * self.remaining_qty * 2
        return gross_cash + realized_gross + gross_mtm, net_cash + realized_net + net_mtm

    def finalize(self) -> dict:
        trade, exits = self.trade, self.exits
        last = exits[-1]
        realized_r = self._realized_r()
        closed = trade.copy()
        closed.update({key: last[key] for key in ("exit_i", "exit_time", "raw_exit", "net_exit", "exit", "exit_reason")})
        gross_pnl = sum(fill["gross_pnl"] for fill in exits)
        net_pnl = sum(fill["net_pnl"] for fill in exits)
        risk_value = trade["initial_risk"] * trade["qty"] * trade["point_value"]
        net_realized_r = net_pnl / risk_value
        closed.update(
            {
                "mfe_r": self.peak_r,
                "exits": exits,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "realized_r": realized_r,
                "net_realized_r": net_realized_r,
                "outcome": classify_outcome(realized_r),
                "net_outcome": classify_outcome(net_realized_r),
                "giveback_r": max(0.0, float(self.peak_r) - max(realized_r, 0.0)),
                "holding_duration": pd.Timestamp(last["exit_time"]) - pd.Timestamp(trade["entry_time"]),
            }
        )
        return closed


def _bar_path(opened: float, high: float, low: float, close: float, side: Side) -> tuple[float, float, float, float]:
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
    strategy: str,
    risk_reward_ratio: float,
    exit_mode: str,
    operation: str,
    risk_pct: float,
    capital: float,
    with_costs: bool,
    asset_cfg: AssetConfig,
    low_timeframe: str | None = None,
    max_trades: int | None = None,
    days: tuple[int, ...] = (),
    months: tuple[int, ...] = (),
    session_exit: SessionSpec | None = None,
    oos_cutoff: int | None = None,
    target_fill: TargetFill = TargetFill.TICK_THROUGH,
    intrabar: IntrabarRule = IntrabarRule.PATH,
) -> tuple[dict, list[dict]]:
    if not math.isfinite(risk_reward_ratio) or risk_reward_ratio <= 0:
        raise ValueError("--risk_reward_ratio must be positive")
    if exit_mode not in {"fixed", "trailing", "partial"}:
        raise ValueError(f"Unknown exit mode: {exit_mode}")
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown operation: {operation}")
    if target_fill not in TARGET_FILLS:
        raise ValueError(f"--target_fill must be one of {', '.join(TARGET_FILLS)}; got: {target_fill}")
    if intrabar not in INTRABAR_RULES:
        raise ValueError(f"--intrabar must be one of {', '.join(INTRABAR_RULES)}; got: {intrabar}")
    target_fill, intrabar = TargetFill(target_fill), IntrabarRule(intrabar)
    if not math.isfinite(risk_pct) or risk_pct <= 0:
        raise ValueError("--risk must be finite and positive")
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("--capital must be finite and positive")
    if max_trades is not None and max_trades < 1:
        raise ValueError("--max_trades must be a positive integer")
    validate_calendar_filters(days, months)
    if data.empty:
        raise ValueError(f"No data for {asset} {timeframe}")
    if oos_cutoff is not None:
        if max_trades is not None:
            raise ValueError("--max_trades cannot be combined with a split")
        if oos_cutoff <= 0:
            raise ValueError("Split cutoff leaves the in-sample period with no bars")
        if oos_cutoff >= len(data):
            raise ValueError("Split cutoff leaves the out-of-sample period with no bars")
    missing_data = {"open", "high", "low", "close"} - set(data.columns)
    if missing_data:
        raise ValueError(f"Data missing columns: {', '.join(sorted(missing_data))}")
    opens, highs, lows, closes = (
        data[column].to_numpy(dtype=float, copy=False) for column in ("open", "high", "low", "close")
    )
    if not all(np.isfinite(values).all() for values in (opens, highs, lows, closes)):
        raise ValueError("Data prices must be finite")
    if any((highs < values).any() for values in (opens, lows, closes)) or any(
        (lows > values).any() for values in (opens, highs, closes)
    ):
        raise ValueError("Data contains invalid OHLC ranges")
    annualization_timeframe = low_timeframe or timeframe
    if annualization_timeframe not in asset_cfg.bars_per_year:
        raise ValueError(f"Missing bars_per_year for {asset} {annualization_timeframe}")
    if "1d" not in asset_cfg.bars_per_year:
        raise ValueError(f"Missing bars_per_year for {asset} 1d")
    if annualization_timeframe not in BAR_DURATIONS:
        raise ValueError(f"Unknown low timeframe for {asset}: {annualization_timeframe}")
    bar_duration = BAR_DURATIONS[annualization_timeframe]

    bar_count = len(data)
    gross_curve = np.full(bar_count + 1, capital, dtype=float)
    net_curve = np.full(bar_count + 1, capital, dtype=float) if with_costs else gross_curve
    bar_gross_equity = gross_curve[1:]
    bar_net_equity = net_curve[1:]
    current_gross = capital
    current_net = capital
    last_bar_idx = 0
    trades = []
    discarded_signals = 0
    busy_until = -1
    # Open of the first high-timeframe bar after the previous trade's exit bar; a later setup time before it is discarded.
    fresh_from = None
    high_bar = BAR_DURATIONS.get(timeframe)
    # With a session exit, open trades close at the close of their session window's last bar.
    if session_exit is not None:
        in_window, window_last_bar = session_exit_bars(data.index, session_exit)
    # A roll is a bar whose contract differs from the previous bar's; open trades close at the bar before it.
    roll_last_bar = np.zeros(bar_count, dtype=bool)
    if "contract" in data.columns:
        contracts = data["contract"].to_numpy()
        roll_last_bar[:-1] = contracts[1:] != contracts[:-1]
    roll_last_times = data.index[roll_last_bar]
    roll_times = data.index[1:][roll_last_bar[:-1]]
    day_filter = set(days)
    month_filter = set(months)
    try:
        signals = normalize_signals(signals)
    except ValueError as exc:
        raise ValueError(f"{asset} {timeframe}: {exc}") from exc
    if "setup_time" in signals.columns and high_bar is None:
        raise ValueError(f"Unknown high timeframe for setup_time: {timeframe}")
    if operation == "long_only":
        signals = signals[signals["side"] == Side.LONG]
    if operation == "short_only":
        signals = signals[signals["side"] == Side.SHORT]

    # Independent asset/timeframe runs; add portfolio state only when cross-asset sizing matters.
    for _, signal in signals.iterrows():
        signal_entry = signal.get("entry")
        close_entry = pd.notna(signal_entry)
        entry_i = data.index.searchsorted(signal["time"], side="left" if close_entry else "right")
        # A close entry needs a bar after its fill bar.
        if entry_i + int(close_entry) >= bar_count or entry_i <= busy_until:
            discarded_signals += 1
            continue
        setup_time = signal.get("setup_time")
        if fresh_from is not None and pd.notna(setup_time) and setup_time < fresh_from:
            discarded_signals += 1
            continue
        if close_entry and data.index[entry_i] != signal["time"]:
            discarded_signals += 1
            continue
        entry_time = pd.Timestamp(data.index[entry_i])
        if (day_filter and entry_time.weekday() not in day_filter) or (
            month_filter and entry_time.month not in month_filter
        ):
            discarded_signals += 1
            continue
        # A trade must open inside the window, before the window's last close.
        if session_exit is not None and (not in_window[entry_i] or (close_entry and window_last_bar[entry_i])):
            discarded_signals += 1
            continue
        # No fills in the 60 minutes before the last bar ahead of a roll; a close entry fills at its bar's close.
        next_roll = roll_last_times.searchsorted(entry_time)
        fill_time = entry_time + bar_duration if close_entry else entry_time
        if next_roll < len(roll_last_times) and roll_last_times[next_roll] - fill_time < ROLL_ENTRY_BLOCK:
            discarded_signals += 1
            continue
        # A signal from before a roll carries an old-contract stop, so it never fills on or after the roll bar.
        roll_after_signal = roll_times.searchsorted(signal["time"], side="right")
        if roll_after_signal < len(roll_times) and roll_times[roll_after_signal] <= entry_time:
            discarded_signals += 1
            continue

        # Fill flat equity up to the first simulated bar; a close entry's fill bar has no P&L yet.
        start_i = entry_i + int(close_entry)
        if last_bar_idx < start_i:
            bar_gross_equity[last_bar_idx:start_i] = current_gross
            bar_net_equity[last_bar_idx:start_i] = current_net
            last_bar_idx = start_i

        trade = _prepare_trade(
            signal, opens[entry_i], data.index[entry_i], asset_cfg, current_net, risk_pct, risk_reward_ratio, exit_mode,
            asset, timeframe, strategy, entry_i, close_entry, with_costs,
        )
        if trade is None:
            discarded_signals += 1
            continue

        open_trade = _OpenTrade(trade, asset_cfg, with_costs, target_fill)
        side = trade["side"]
        trailing = exit_mode == "trailing"
        for i in range(start_i, bar_count):
            path = _bar_path(opens[i], highs[i], lows[i], closes[i], side)
            opened = path[0]
            bar_time = data.index[i]

            # Orders crossed between the previous close and this open fill at the open.
            if open_trade.stop_gapped(opened):
                open_trade.close(opened, i, bar_time, ExitReason.STOP, gap_exit=True)
                break
            open_trade.update_peak(opened)
            while open_trade.target_gapped(opened):
                open_trade.fill_target(open_trade.target_gap_price(opened), i, bar_time, gap_exit=True)
            if open_trade.closed:
                break
            if trailing:
                open_trade.trail(opened)

            # Stop-first: a bar that can reach both the stop and the next target exits at the stop.
            if intrabar == IntrabarRule.STOP_FIRST and open_trade.stop_and_target_reachable(opened, highs[i], lows[i]):
                open_trade.close(open_trade.stop, i, bar_time, ExitReason.STOP)
                break

            for start, end in zip(path, path[1:]):
                if (end - start) * open_trade.direction > 0:
                    while open_trade.target_between(start, end):
                        target_price = open_trade.target["price"]
                        open_trade.update_peak(target_price)
                        open_trade.fill_target(target_price, i, bar_time)
                    if open_trade.closed:
                        break
                    open_trade.update_peak(end)
                    if trailing:
                        open_trade.trail(end)
                elif open_trade.stop_between(start, end):
                    open_trade.close(open_trade.stop, i, bar_time, ExitReason.STOP)
                    break

            if open_trade.closed:
                break
            if session_exit is not None and window_last_bar[i]:
                open_trade.close(path[-1], i, bar_time, ExitReason.SESSION)
                break
            if roll_last_bar[i]:
                open_trade.close(path[-1], i, bar_time, ExitReason.ROLL)
                break
            if i == bar_count - 1:
                open_trade.close(path[-1], i, bar_time, ExitReason.END)
                break
            bar_gross_equity[i], bar_net_equity[i] = open_trade.marked_equity(path[-1], current_gross, current_net)

        trade = open_trade.finalize()

        # Settle the completed trade into the bar-level equity curves.
        current_gross += trade["gross_pnl"]
        current_net += trade["net_pnl"]
        bar_gross_equity[trade["exit_i"]] = current_gross
        bar_net_equity[trade["exit_i"]] = current_net
        last_bar_idx = trade["exit_i"] + 1
        busy_until = trade["exit_i"]
        if high_bar is not None:
            # High-timeframe bars are clock-aligned, as resample_data aligns intraday rules.
            fresh_from = pd.Timestamp(data.index[busy_until]).floor(high_bar) + high_bar
        trades.append(trade)
        if max_trades is not None and len(trades) >= max_trades:
            break

    # Fill any remaining flat equity after the last completed trade.
    if last_bar_idx < bar_count:
        bar_gross_equity[last_bar_idx:bar_count] = current_gross
        bar_net_equity[last_bar_idx:bar_count] = current_net

    bars_per_year = asset_cfg.bars_per_year[annualization_timeframe]
    market_sessions_per_year = asset_cfg.bars_per_year["1d"]
    # Curve point i + 1 is the equity at the close of bar i; a market session closes at its last bar.
    labels = session_labels(data.index, asset_cfg.session_timezone, asset_cfg.session_start)
    market_session_closes = np.flatnonzero(np.append(labels[1:] != labels[:-1], True)) + 1
    if oos_cutoff is None:
        return make_metrics(
            asset, timeframe, strategy, gross_curve, net_curve, with_costs, market_session_closes, market_sessions_per_year,
            discarded_signals,
        ), trades
    # Curve point oos_cutoff is the equity at the close of the last in-sample bar; both slices include it.
    # The cutoff is a market session's first bar, so that point is also the last in-sample market session close.
    metrics = make_metrics(
        asset, timeframe, strategy, gross_curve[: oos_cutoff + 1], net_curve[: oos_cutoff + 1], with_costs,
        market_session_closes[market_session_closes <= oos_cutoff], market_sessions_per_year, discarded_signals,
    )
    oos = make_metrics(
        asset, timeframe, strategy, gross_curve[oos_cutoff:], net_curve[oos_cutoff:], with_costs,
        market_session_closes[market_session_closes > oos_cutoff] - oos_cutoff, market_sessions_per_year,
    )
    metrics.update({column: oos[source] for column, source in OOS_FINANCIAL_COLUMNS.items()})
    metrics["OOS Gross"] = oos["Gross"]
    metrics["OOS Net"] = oos["Net"]
    gross_wfe = walk_forward_efficiency(gross_curve, oos_cutoff, bars_per_year)
    metrics["WFE"] = (gross_wfe, walk_forward_efficiency(net_curve, oos_cutoff, bars_per_year)) if with_costs else gross_wfe
    metrics["OOS Trades"] = sum(1 for trade in trades if not entered_in_sample(trade, oos_cutoff))
    metrics["Spanning"] = sum(1 for trade in trades if entered_in_sample(trade, oos_cutoff) and trade["exit_i"] >= oos_cutoff)
    return metrics, trades


def validate_calendar_filters(days: tuple[int, ...], months: tuple[int, ...]) -> None:
    if not isinstance(days, tuple) or any(type(day) is not int or day < 0 or day > 6 for day in days):
        raise ValueError("--days must contain weekday values from Monday through Sunday")
    if not isinstance(months, tuple) or any(type(month) is not int or month < 1 or month > 12 for month in months):
        raise ValueError("--months must contain month values from January through December")


def _curve_metrics(equity_curve: np.ndarray, market_session_closes: np.ndarray, market_sessions_per_year: int) -> dict:
    """Return, Max DD and Return / DD use every curve point; Daily Sharpe uses the start and each market session close.

    `market_session_closes` are the curve positions of each market session's close equity, in order.
    """
    curve = np.asarray(equity_curve, dtype="float64")
    scratch = np.empty_like(curve)
    np.maximum.accumulate(curve, out=scratch)
    np.divide(curve, scratch, out=scratch)
    scratch -= 1
    max_dd = abs(float(np.nanmin(scratch)) * 100)
    total_return = (curve[-1] / curve[0] - 1) * 100
    sharpe = float("nan")
    daily = curve[np.r_[0, market_session_closes]]
    if curve.min() > 0 and len(daily) > 2:
        returns = daily[1:] / daily[:-1] - 1
        std = float(returns.std(ddof=1))
        if math.isfinite(std) and std > 0:
            sharpe = math.sqrt(market_sessions_per_year) * float(returns.mean() / std)
    return {
        "Return": round(float(total_return), 2),
        "Max DD": round(max_dd, 2),
        "Sharpe Ratio": round(sharpe, 2),
        "Return / DD": round(float(total_return) / max_dd, 2) if max_dd else float("nan"),
    }


def make_metrics(
    asset: str,
    timeframe: str,
    strategy: str,
    gross_equity_curve: np.ndarray,
    net_equity_curve: np.ndarray,
    with_costs: bool,
    market_session_closes: np.ndarray,
    market_sessions_per_year: int,
    discarded_signals: int = 0,
) -> dict:
    gross = _curve_metrics(gross_equity_curve, market_session_closes, market_sessions_per_year)
    net = _curve_metrics(net_equity_curve, market_session_closes, market_sessions_per_year) if with_costs else gross.copy()
    return {
        "Strategy": strategy,
        "Asset": asset,
        "TF": timeframe,
        "Discarded": discarded_signals,
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


def result_columns(operation: str, include_session: bool = False, split: bool = False) -> list[str]:
    columns = ["Strategy", "Asset", "TF"]
    if include_session:
        columns.append("Session")
    columns += ["RR", "Exit Mode", "Trades", "Discarded"]
    if operation == "all":
        columns += ["Long", "Short"]
    columns += ["W", "BE", "L", "Win Rate", "Expectancy R", "Avg Duration", "Return", "Max DD", "Sharpe Ratio", "Return / DD"]
    return columns + list(SPLIT_COLUMNS) if split else columns
