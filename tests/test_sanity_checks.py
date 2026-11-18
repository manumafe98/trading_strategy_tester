"""Engine sanity checks on a seeded random walk: no strategy looks ahead, random entries lose about the costs, a cheat wins."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tester_framework.backtest import run_backtest
from tester_framework.models import AssetConfig
from tester_framework.sessions import parse_sessions
from tester_framework.settings import STRATEGY_DIR, TIMEFRAMES
from tester_framework.strategy_loader import load_strategy


TICK = 0.25
BAR_SIZES = {"1m": "1min", "2m": "2min", "5m": "5min", "10m": "10min", "15m": "15min", "30m": "30min", "1h": "1h", "1d": "1D"}
STRATEGIES = sorted(path.stem for path in STRATEGY_DIR.glob("*.py") if path.stem != "__init__")
CUTS = 5
PEEK_BARS = 60
STOP_TICKS = 10
RR = 2.0
RANDOM_ENTRY_SEEDS = (1, 2, 3, 4, 5)
# The only high timeframes a strategy may refuse, with the error it must raise; any other error fails the test.
UNSUPPORTED_SIGNAL_TIMEFRAMES = {
    (name, "1d"): "ORB strategies support intraday timeframes only" for name in ("orb_candle", "orb_combined", "orb_fvg")
}
SANITY_ASSET = AssetConfig(
    ticker="TEST",
    point_value=1.0,
    tick_size=TICK,
    qty_step=1.0,
    min_qty=1.0,
    min_risk_ticks=1,
    spread_points=0.25,
    slippage_points=0.0,
    commission_per_side=0.125,
    session_timezone="UTC",
    session_start="00:00",
    bars_per_year={"1h": 8760, "1d": 252},
)


def random_walk(bars: int, bar_size: str, seed: int = 0) -> pd.DataFrame:
    """Tick-aligned OHLC bars whose open is the previous close, so there are no gaps."""
    rng = np.random.default_rng(seed)
    close_ticks = 20_000 + np.cumsum(rng.integers(-4, 5, bars))
    open_ticks = np.concatenate(([20_000], close_ticks[:-1]))
    high_ticks = np.maximum(open_ticks, close_ticks) + rng.integers(0, 3, bars)
    low_ticks = np.minimum(open_ticks, close_ticks) - rng.integers(0, 3, bars)
    return pd.DataFrame(
        {
            "open": open_ticks * TICK,
            "high": high_ticks * TICK,
            "low": low_ticks * TICK,
            "close": close_ticks * TICK,
            "volume": np.zeros(bars),
        },
        index=pd.date_range("2025-01-06", periods=bars, freq=bar_size),
    )


def strategy_params(strategy) -> dict:
    """The params the runner passes, with the ny session window for strategies that require --sessions."""
    needs_session = "sessions" in getattr(strategy, "REQUIRED_FLAGS", {})
    return {"tick_size": TICK, "session": parse_sessions("ny")[0] if needs_session else None}


def assert_no_lookahead(generate_signals, df: pd.DataFrame, timeframe: str, params: dict, seed: int = 0) -> None:
    """Signals up to each cut bar must equal the signals of the uncut series up to that bar, column for column.

    Cuts land on CUTS random bars and on CUTS random signal bars, where a one-bar peek shows.
    """
    full = generate_signals(df, "TEST", timeframe, params).reset_index(drop=True)
    assert not full.empty, f"No signals on the synthetic series for high timeframe {timeframe}; the check would be vacuous"
    signal_bars = np.unique(df.index.searchsorted(pd.to_datetime(full["time"])))
    signal_bars = signal_bars[signal_bars < len(df) - 1]
    rng = np.random.default_rng(seed)
    random_bars = rng.choice(np.arange(signal_bars[0], len(df) - 1), size=CUTS, replace=False)
    signal_cuts = rng.choice(signal_bars, size=min(CUTS, len(signal_bars)), replace=False)
    for cut in sorted({*random_bars, *signal_cuts}):
        cut_time = df.index[cut]
        expected = full[pd.to_datetime(full["time"]) <= cut_time].reset_index(drop=True)
        actual = generate_signals(df.iloc[: cut + 1], "TEST", timeframe, params).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, expected, obj=f"signals cut at {cut_time} (high timeframe {timeframe})")


def peek_signals(df, asset, timeframe, params):
    """A cheat: picks the side from the close PEEK_BARS bars ahead."""
    future = df["close"].shift(-PEEK_BARS)
    rows = future.notna() & (future != df["close"])
    long = (future > df["close"])[rows]
    stop = df["close"][rows] - np.where(long, 1, -1) * STOP_TICKS * TICK
    return pd.DataFrame({"time": long.index, "side": np.where(long, "long", "short"), "stop": stop.to_numpy()})


def test_every_strategy_module_is_covered():
    assert STRATEGIES, f"No strategies found in {STRATEGY_DIR}"


@pytest.mark.parametrize("name", STRATEGIES)
def test_strategy_signals_do_not_look_ahead(name):
    strategy = load_strategy(name)
    params = strategy_params(strategy)
    low_timeframe = getattr(strategy, "LOW_TIMEFRAME", None)
    for timeframe in TIMEFRAMES:
        df = random_walk(20_000, BAR_SIZES[low_timeframe or timeframe])
        if (name, timeframe) in UNSUPPORTED_SIGNAL_TIMEFRAMES:
            with pytest.raises(ValueError, match=UNSUPPORTED_SIGNAL_TIMEFRAMES[name, timeframe]):
                strategy.generate_signals(df, "TEST", timeframe, params)
            continue
        assert_no_lookahead(strategy.generate_signals, df, timeframe, params)


def test_lookahead_check_catches_a_cheat():
    with pytest.raises(AssertionError):
        assert_no_lookahead(peek_signals, random_walk(2_000, "1h"), "1h", {"tick_size": TICK})


def backtest(signals: pd.DataFrame, data: pd.DataFrame):
    # Touch fills keep target and stop symmetric on a random walk, so random entries break even gross.
    return run_backtest(
        data,
        signals,
        asset="TEST",
        timeframe="1h",
        strategy="sanity",
        risk_reward_ratio=RR,
        exit_mode="fixed",
        operation="all",
        risk_pct=0.1,
        capital=100_000.0,
        with_costs=True,
        asset_cfg=SANITY_ASSET,
        target_fill="touch",
    )


def random_entry_signals(data: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    bars = np.sort(rng.choice(len(data) - 1, size=len(data) // 10, replace=False))
    long = rng.random(len(bars)) < 0.5
    closes = data["close"].to_numpy()[bars]
    return pd.DataFrame(
        {
            "time": data.index[bars],
            "side": np.where(long, "long", "short"),
            "stop": closes - np.where(long, 1, -1) * STOP_TICKS * TICK,
        }
    )


@pytest.fixture(scope="module")
def walk() -> pd.DataFrame:
    return random_walk(20_000, "1h")


@pytest.fixture(scope="module")
def random_entry_net_returns(walk) -> dict[int, float]:
    return {seed: backtest(random_entry_signals(walk, seed), walk)[0]["Net"]["Return"] for seed in RANDOM_ENTRY_SEEDS}


@pytest.mark.parametrize("seed", RANDOM_ENTRY_SEEDS)
def test_random_entries_break_even_gross_and_lose_net(walk, seed):
    _, trades = backtest(random_entry_signals(walk, seed), walk)
    assert len(trades) >= 200
    # P&L in R, so fixed-fractional sizing does not weight trades by equity.
    gross = np.array([trade["realized_r"] for trade in trades])
    net = np.array([trade["net_realized_r"] for trade in trades])
    standard_error = gross.std(ddof=1) / np.sqrt(len(gross))
    assert abs(gross.mean()) < 3 * standard_error
    assert net.mean() < 0


def test_peek_cheat_wins_and_beats_random_entries(walk, random_entry_net_returns):
    metrics, _ = backtest(peek_signals(walk, "TEST", "1h", {}), walk)
    assert metrics["Net"]["Return"] > 0
    assert metrics["Net"]["Return"] > max(random_entry_net_returns.values())
