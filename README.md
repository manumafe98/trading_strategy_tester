# trading_strategy_tester

Minimal Python backtesting runner.

Requires Python 3.11 or newer.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

## Checks

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m tester_framework --strategies ema50 --asset MGC --timeframe 1h --time_period 2021 --data_source local --operation all --risk_reward_ratio 1 --risk 1 --capital 10000 --with_costs --trade_html 0
```

The smoke run takes about 20 seconds and prints 138 trades and Return -5.18% / -14.42% (gross / net); a different result signals a behaviour change. It reads the local `data/` folder, which only exists on the machine that holds it, so skip it elsewhere.

## Run

```bash
.venv/Scripts/python.exe -m tester_framework --strategies ema50 --asset MGC --timeframe 1h --time_period 60d --operation all --risk_reward_ratio 1 --risk 1 --capital 10000 --with_costs
```

Each normal run clears `results/` and `trades/` first, then writes a result bundle with `index.html`, paginated variant pages, and trade charts. Move or share the result folder as a unit. Use `--trade_html N` to limit charts per variant, or `--trade_html 0` to disable them. Omitting the flag writes every chart.

When `--operation all` is used, results include total, long, and short trade counts. For `long_only` or `short_only`, only total trades is shown.

Asset aliases, tick/point values, trading-session boundaries, annualization factors, and cost assumptions live in `config/assets.json`. Every configured asset must provide every supported timeframe's `bars_per_year`; there are no cross-market fallbacks.

## Command Examples

Multiple values are comma-separated, without spaces:

```bash
python -m tester_framework --strategies ema50 --asset MGC,MNQ --timeframe 1h,30m --risk_reward_ratio 1,2
python -m tester_framework --strategies orb_candle,orb_fvg,orb_combined --asset MNQ --timeframe 5m --sessions ny=09:30-12:00 --risk_reward_ratio 2 --with_costs
```

`--strategies` accepts a comma-separated list of strategy modules. Each strategy runs independently with its own signal generation; results include a `Strategy` column for side-by-side comparison. Each asset/timeframe/RR/exit/strategy variant runs in parallel up to the machine's logical CPU count. Use `--workers 1` for a sequential comparison or a smaller positive value to limit CPU usage. The command prints total elapsed time after writing the result report.

For quick diagnostic samples, `--max_trades N` stops each variant after its first N closed trades and records the cap in the result report. These truncated metrics are not a substitute for a full strategy study. `--trade_html N` keeps the full backtest but writes only the first N trade charts per variant; `--trade_html 0` keeps the result report and trade ledger without chart files.

```bash
python -m tester_framework --strategies orb_candle,orb_fvg,orb_combined --asset MNQ --timeframe 5m --sessions ny=09:30-12:00 --time_period 5d --max_trades 3 --trade_html 0 --workers 1
```

Use all configured assets by omitting `--asset`; use the default timeframe list by omitting `--timeframe`:

```bash
python -m tester_framework --strategies ema50 --risk_reward_ratio 1
python -m tester_framework --strategies ema50 --risk_reward_ratio 2 --exit_mode trailing
python -m tester_framework --strategies ema50 --risk_reward_ratio 2,3 --exit_mode fixed,partial
python -m tester_framework --strategies ema50 --risk_reward_ratio 1,2,3 --exit_mode all
```

`--exit_mode all` runs fixed, trailing, and partial variants independently. Partial mode splits the position across whole-R targets and the configured final target; for example, 2.5R exits equal tranches at 1R and 2.5R. Partial mode is skipped below 2R, and trailing mode is skipped at or below 1R where it would duplicate fixed behavior.

Long-only or short-only runs hide the long/short split columns because the operation already explains the side:

```bash
python -m tester_framework --strategies ema50 --asset MGC --operation long_only
python -m tester_framework --strategies ema50 --asset MGC --operation short_only
```

Risk can be global or per asset:

```bash
python -m tester_framework --strategies ema50 --asset MGC,MNQ --risk 1
python -m tester_framework --strategies ema50 --asset MGC,MNQ --risk MGC=1,MNQ=0.5
```

Sizing is fixed-fractional: the risk percentage is applied to current net equity, so wins and losses compound. It measures entry-to-stop price risk; costs are reported separately and can make the net loss exceed the configured percentage. Only one position can be open per asset/timeframe, so overlapping signals are discarded.

Every asset in `config/assets.json` must define `min_risk_ticks` (4 for MNQ, MES and MGC; 1 for EURUSD, GBPUSD and BTCUSD); a missing value fails at config load. For every strategy, a signal whose entry-to-stop distance, after the stop is rounded to the tick, is below `min_risk_ticks` × tick size is discarded and counted in `Discarded`. A distance exactly at the minimum is traded. This stops one-tick stops from producing huge positions and absurd R.

`--sessions` filters entries by market-local session windows for any strategy. Use presets like `asia`, `london`, `ny`, expand `all`, or pass subranges such as `ny=09:30-12:00` and `london=08:00-10:00`. Omit it, or use `--sessions none`, to keep the unfiltered framework behavior. ORB strategies override this and require a real session value.

The `ny_bank` preset is America/New_York 08:00-17:00, for a true New York bank close. Subranges such as `ny_bank=09:30-17:00` must stay inside it, like every preset. `all` expands to `asia`, `london` and `ny` only; `ny_bank` overlaps `ny` and must be named.

`--session_exit` (off by default) closes a trade still open at the end of its variant's session window at the close of the last execution bar that starts inside the window, with exit reason `session`. It applies to every exit mode and closes whatever position remains after partial targets. The window end is exclusive, matching the session filter, and DST follows the window's timezone. If bars are missing at the window end, the exit happens on the last in-window bar present. Costs apply as for any exit. With the flag on, a signal whose fill would land outside the window, or a close entry on the window's last bar, is discarded, and a trade still open when the data ends inside a window stays unresolved. The flag requires `--sessions` with a session window and is rejected without it or with `--sessions none`. For example: `--sessions ny_bank=09:30-17:00 --session_exit`.

`--days` and `--months` filter actual filled entries by the normalized data timestamp. Values are case-insensitive full names or three-letter abbreviations, for example `--days monday,wed --months january,sep`. Omit either flag to allow every weekday or month; when both are present, an entry must match both.

`--time_period` accepts rolling periods such as `60d`, `6mo`, and `1y`, plus `max`. A single calendar year such as `2021` selects that full year, and `2020-2021` selects both years inclusively. Calendar years use the same boundaries for yfinance and local CSV data; Yahoo's intraday history limits still apply.

## Execution Model

Historical OHLC bars do not reveal their intrabar price order. The runner uses the same deterministic heuristic documented by TradingView's broker emulator:

- If the open is closer to the high, the path is open → high → low → close.
- If the open is closer to the low, the path is open → low → high → close.
- Equal-distance bars take the stop-first path.
- Orders crossed by an overnight/session gap fill at the current bar's open.

Stops, targets, and trailing stops are rounded conservatively to the asset's tick size. Trailing mode activates after 1R of favorable movement and stays 0.5R behind the favorable extreme. Partial targets and trailing changes are applied in path order.

Local futures CSVs containing multiple expiries are converted to an unadjusted continuous series. Each trading session uses the highest-volume available contract according to the previous observed session's total-volume ranking. Local daily OHLCV is grouped by the configured market session rather than UTC midnight.

## Metrics

- **Sharpe Ratio** is a bar-level, annualized Sharpe using a 0% risk-free rate. It uses the execution timeframe's asset-specific annualization factor.
- **Return**, **Max DD**, and **Return / DD** use the same bar-level equity curve.
- **MFE R** is the position-weighted peak trade value: banked partial fills plus the remaining position's favorable value.
- **Giveback R** is favorable peak profit not retained at exit; straight losses that were never profitable have zero giveback.

Undefined Sharpe and Return/DD values display as `N/A`. With `--with_costs`, outcome and financial columns show Gross / Net pairs.

Only fully resolved trades are counted. Setups still open at the end of the downloaded data—including any completed partial fills—are fully discarded and reported in `Unresolved`. Signals skipped due to invalid stops, bad entries, minimum risk, overlap, or minimum-quantity filters are reported in `Discarded`.

## Strategy API

Put local strategies in `strategy/<name>.py`:

```python
def generate_signals(df, asset, timeframe, params):
    return signals  # columns: time, side, stop
```

`side` must be `long` or `short`. The strategy owns entries and stops; the framework owns fixed, trailing, and partial exits.

`params["tick_size"]` contains the configured price tick for the current asset. `params["session"]` is either `None` or the parsed session mapping selected by `--sessions`.

The input `df` is shared across timeframes; strategies **must not mutate** it. Copy inside the strategy if needed.

A strategy can request a different execution timeframe than the signal timeframe by setting a module-level constant:

```python
EXECUTION_TIMEFRAME = "5m"  # data loaded at 5m, signals generated per --timeframe
```

Strategies can add indicators to trade charts with an optional hook:

```python
def plot_indicators(fig, data, view, asset, timeframe, params):
    ...
```

The framework calls this hook for each trade HTML. Keep indicator plotting in the strategy because only the strategy knows which lines matter.

Any extra signal column beyond `time`, `side`, `stop`, and `entry` (for example `vwap`) is copied onto the trade passed as `params["trade"]` when its value is not NA. Engine-owned trade keys such as `target` or `qty` win over same-named signal columns. `plot_start_time` is parsed as a datetime and moves the chart start back to it.

Strategies can also add scalar metrics to each expanded result variant:

```python
def calculate_metrics(data, signals, trades, asset, timeframe, params):
    return {"Strategy metric": value}
```

`params` contains the effective RR, exit mode, operation, risk percentage, capital, costs flag, time period, data source, and asset tick size. The hook is called even when a variant has no completed trades.

## ORB Strategies

Three Opening Range Breakout strategies share common logic in `strategy/utils/`:

- **`orb_candle`** — SL from the breakout candle's extreme (low - tick for long, high + tick for short). Always trades on breakout.
- **`orb_fvg`** — SL from a Fair Value Gap boundary (FVG bottom - tick for long, FVG top + tick for short). Only trades when a directional FVG exists at breakout time.
- **`orb_combined`** — SL from the FVG extension line (ext price - tick for long, ext price + tick for short). Only trades when a directional FVG with an extension line exists.

All three use 1-minute execution data (`EXECUTION_TIMEFRAME = "1m"`). For these strategies, `--timeframe` is the opening-range duration rather than the execution-bar size; every configured intraday duration from 1 minute through 1 hour is supported. They require `--sessions` and build the ORB from that session's local start time, then look for breakouts until the session window ends. FVG detection and mitigation tracking use the same session window. The SL offset uses the asset's configured tick size.

```bash
python -m tester_framework --strategies orb_candle,orb_fvg,orb_combined --asset MNQ --timeframe 5m --sessions ny=09:30-12:00 --risk_reward_ratio 2 --with_costs
```

### Differences From Pine

The Pine references in `pine_scripts_examples/` differ from these strategies on purpose. Each difference is pinned by a test in `tests/test_pine_differences.py`.

- **Day boundary.** Pine calls `strategy.close_all` on the first bar of a new New York calendar date and fills at that bar's close, so on 24-hour futures data an open trade is held through the evening session until the 00:00 New York bar. By default we hold a trade until its stop, its target, or the end of the data; only with `--session_exit` do we never hold past the session window. The closest equivalent is `--sessions ny_bank=09:30-17:00 --session_exit`, which exits at the close of the last bar starting before 17:00 New York, not at the date change.
- **Sizing.** Pine trades at least one contract, `max(1, floor(risk / R))`, and caps the size at what equity can margin at 5% of notional. We discard a trade whose risk-based quantity is below the asset's `min_qty` and apply no margin cap.
- **FVG bars.** Pine checks `high[2] < low` on every bar in its FVG window, so on the window's first two bars an FVG can use bars from before the window. We only use bars inside the session window, so the first two window bars cannot complete an FVG.
- **FVG cap.** Pine keeps at most `Max Tracked FVGs` (default 100) and drops the oldest. We keep every active FVG.
- **FVG window.** Pine detects FVGs in a separate `FVG Session` input that defaults to 09:30-12:00 New York, and its trade window is hardcoded to 09:30-11:59. Both of ours follow the `--sessions` window, so a wider window detects and trades FVGs outside 09:30-12:00.
