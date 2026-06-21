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
python -m pytest -q
```

## Run

```bash
python -m tester_framework --strategies ema50 --asset MGC --timeframe 1h --time_period 60d --operation all --risk_reward_ratio 1 --risk 1 --capital 10000 --with_costs
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

`--sessions` filters entries by market-local session windows for any strategy. Use presets like `asia`, `london`, `ny`, expand `all`, or pass subranges such as `ny=09:30-12:00` and `london=08:00-10:00`. Omit it, or use `--sessions none`, to keep the unfiltered framework behavior. ORB strategies override this and require a real session value.

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

Only fully resolved trades are counted. Setups still open at the end of the downloaded data—including any completed partial fills—are fully discarded and reported in `Unresolved`. Signals skipped due to invalid stops, bad entries, overlap, or minimum-quantity filters are reported in `Discarded`.

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
