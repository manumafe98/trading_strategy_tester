# trading_strategy_tester

Minimal Python backtesting runner.

Requires Python 3.11 or newer.

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

## Checks

```bash
PY="$(git rev-parse --path-format=absolute --git-common-dir)/../.venv/Scripts/python.exe"
"$PY" -m pytest -q
```

The local smoke run and its expected result are documented in `AGENTS.md`.

## Run

`PY` is defined in Checks.

```bash
"$PY" -m tester_framework --strategies ema50 --asset MGC --high_timeframe 1h --time_period 60d --operation all --risk_reward_ratio 1 --risk 1 --capital 10000 --with_costs
```

Each normal run clears `results/` and `trades/` first, then writes a result bundle with `index.html`, paginated variant pages, and trade charts. Move or share the result folder as a unit. Use `--trade_html N` to limit charts per variant, or `--trade_html 0` to disable them. Omitting the flag writes every chart.

When `--operation all` is used, results include total, long, and short trade counts. For `long_only` or `short_only`, only total trades is shown.

Asset aliases, tick/point values, trading-session boundaries, annualization factors, and cost assumptions live in `config/assets.json`. Every configured asset must provide every supported timeframe's `bars_per_year`; there are no cross-market fallbacks.

## Command Examples

Multiple values are comma-separated, without spaces:

```bash
"$PY" -m tester_framework --strategies ema50 --asset MGC,MNQ --high_timeframe 1h,30m --risk_reward_ratio 1,2
"$PY" -m tester_framework --strategies orb_candle,orb_fvg,orb_combined --asset MNQ --high_timeframe 5m --sessions ny=09:30-12:00 --risk_reward_ratio 2 --with_costs
```

`--strategies` accepts a comma-separated list of strategy modules. Each strategy runs independently with its own signal generation; results include a `Strategy` column for side-by-side comparison. Each asset/timeframe/RR/exit/strategy variant runs in parallel up to the machine's logical CPU count. Use `--workers 1` for a sequential comparison or a smaller positive value to limit CPU usage. The command prints total elapsed time after writing the result report.

For quick diagnostic samples, `--max_trades N` stops each variant after its first N closed trades and records the cap in the result report. These truncated metrics are not a substitute for a full strategy study. `--trade_html N` keeps the full backtest but writes only the first N trade charts per variant; `--trade_html 0` keeps the result report and trade ledger without chart files.

```bash
"$PY" -m tester_framework --strategies orb_candle,orb_fvg,orb_combined --asset MNQ --high_timeframe 5m --sessions ny=09:30-12:00 --time_period 5d --max_trades 3 --trade_html 0 --workers 1
```

Use all configured assets by omitting `--asset`; use the default timeframe list by omitting `--high_timeframe`:

```bash
"$PY" -m tester_framework --strategies ema50 --risk_reward_ratio 1
"$PY" -m tester_framework --strategies ema50 --risk_reward_ratio 2 --exit_mode trailing
"$PY" -m tester_framework --strategies ema50 --risk_reward_ratio 2,3 --exit_mode fixed,partial
"$PY" -m tester_framework --strategies ema50 --risk_reward_ratio 1,2,3 --exit_mode all
```

`--exit_mode all` runs fixed, trailing, and partial variants independently. Partial mode splits the position across whole-R targets and the configured final target; for example, 2.5R exits equal tranches at 1R and 2.5R. Partial mode is skipped below 2R, and trailing mode is skipped at or below 1R where it would duplicate fixed behavior.

Long-only or short-only runs hide the long/short split columns because the operation already explains the side:

```bash
"$PY" -m tester_framework --strategies ema50 --asset MGC --operation long_only
"$PY" -m tester_framework --strategies ema50 --asset MGC --operation short_only
```

Risk can be global or per asset:

```bash
"$PY" -m tester_framework --strategies ema50 --asset MGC,MNQ --risk 1
"$PY" -m tester_framework --strategies ema50 --asset MGC,MNQ --risk MGC=1,MNQ=0.5
```

Sizing is fixed-fractional: the risk percentage is applied to current net equity, so wins and losses compound. It measures entry-to-stop price risk; costs are reported separately and can make the net loss exceed the configured percentage. Only one position can be open per asset/timeframe, so overlapping signals are discarded.

Every asset in `config/assets.json` must define `min_risk_ticks` (4 for MNQ, MES and MGC; 1 for EURUSD, GBPUSD and BTCUSD); a missing value fails at config load. For every strategy, a signal whose entry-to-stop distance, after the stop is rounded to the tick, is below `min_risk_ticks` × tick size is discarded and counted in `Discarded`. A distance exactly at the minimum is traded. This stops one-tick stops from producing huge positions and absurd R.

`--sessions` filters entries by market-local session windows for any strategy. Use presets like `asia`, `london`, `ny`, expand `all`, or pass subranges such as `ny=09:30-12:00` and `london=08:00-10:00`. Omit it, or use `--sessions none`, to keep the unfiltered framework behavior. ORB strategies override this and require a real session value.

The `ny_bank` preset is America/New_York 08:00-17:00, for a true New York bank close. Subranges such as `ny_bank=09:30-17:00` must stay inside it, like every preset. `all` expands to `asia`, `london` and `ny` only; `ny_bank` overlaps `ny` and must be named.

`--session_exit` (off by default) closes a trade still open at the end of its variant's session window at the close of the last execution bar that starts inside the window, with exit reason `session`. It applies to every exit mode and closes whatever position remains after partial targets. The window end is exclusive, matching the session filter, and DST follows the window's timezone. If bars are missing at the window end, the exit happens on the last in-window bar present. Costs apply as for any exit. With the flag on, a signal whose fill would land outside the window, or a close entry on the window's last bar, is discarded; a trade still open when the data ends inside a window takes an end exit, as below. The flag requires `--sessions` with a session window and is rejected without it or with `--sessions none`. For example: `--sessions ny_bank=09:30-17:00 --session_exit`.

`--target_fill` sets the target fill. The default `tick_through` fills a target only when price trades at least one tick beyond it, and a bar that opens beyond the target fills at the target price, never better. `touch` fills a target when price reaches it and fills a gap at the open, as TradingView does. Both apply to every target, including partial targets (see `docs/adr/0001-tick-through-targets-by-default.md`).

`--intrabar` sets the intrabar rule. The default `path` follows the TradingView path described under Execution Model. `stop_first` exits at the stop on any bar that can reach both the stop and the next target, whatever its open; a bar that can reach only one of them still follows the path. A target gap at the open is filled before this rule applies, so it still fills the target. The result header (console and results page) shows either flag when it is not the default. `--target_fill touch --intrabar path` reproduces TradingView's fills.

`--days` and `--months` filter actual filled entries by the normalized data timestamp. Values are case-insensitive full names or three-letter abbreviations, for example `--days monday,wed --months january,sep`. Omit either flag to allow every weekday or month; when both are present, an entry must match both.

`--time_period` accepts rolling periods such as `60d`, `6mo`, and `1y`, plus `max`. A single calendar year such as `2021` selects that full year, and `2020-2021` selects both years inclusively. Calendar years use the same boundaries for yfinance and local CSV data; Yahoo's intraday history limits still apply.

## Execution Model

Historical OHLC bars do not reveal their intrabar price order. The runner uses the same deterministic heuristic documented by TradingView's broker emulator:

- If the open is closer to the high, the path is open → high → low → close.
- If the open is closer to the low, the path is open → low → high → close.
- Equal-distance bars take the stop-first path.
- Stops crossed by an overnight/session gap fill at the current bar's open. Targets do too with `--target_fill touch`; by default a target gap fills at the target price.
- With `--intrabar stop_first`, a bar that can reach both the stop and the next target exits at the stop instead of following the path.

A signal with a `stop_entry` price is a stop entry, like a resting stop order that lives for its own bar. Its `time` must be a bar's start. A long fills at `max(open, stop_entry)` once the bar's high reaches the level, a short at `min(open, stop_entry)` once the low does; spread and slippage apply as for any entry. A bar that never reaches the level discards the signal, and so does a stop on the wrong side of the level (a long needs `stop < stop_entry`, a short `stop > stop_entry`). The rest of the fill bar is then checked for the stop and targets. A bar that opens beyond the level fills at the open and follows its whole path. Otherwise the fill is the first point on the path that reaches the level, and the remainder is the rest of that leg from the fill price plus every later leg: on an open → high → low → close bar a long fills on the open → high leg and continues fill → high → low → close, a short fills on the high → low leg and continues fill → low → close. Prices before the fill never trigger an exit. `--intrabar stop_first` looks only at the remainder's extremes, and a trailing stop starts tracking from the fill price. The fill time used for duration, the session window and the roll checks is the bar's start, as for a next-open entry. The level must be knowable before the bar opens, i.e. computed only from earlier bars; the engine cannot check this. A signal that sets both `entry` and `stop_entry` is an error.

Stops, targets, and trailing stops are rounded conservatively to the asset's tick size. Trailing mode activates after 1R of favorable movement and stays 0.5R behind the favorable extreme. Partial targets and trailing changes are applied in path order.

Local futures CSVs containing multiple expiries are converted to an unadjusted continuous series. Each trading session uses the highest-volume available contract according to the previous observed session's total-volume ranking. Local daily OHLCV is grouped by the configured market session rather than UTC midnight.

The series is not back-adjusted, so prices match TradingView's default continuous chart, and the price difference between contracts shows as a jump at each roll: the first bar of a market session whose contract differs from the previous bar's (see `docs/adr/0002-roll-exit-not-back-adjustment.md`). A trade still open on the last bar before a roll closes at that bar's close with exit reason `roll`; costs apply as for any exit. Three kinds of signal are discarded and counted in `Discarded`. The first is one that fills less than 60 minutes before the last bar before the roll starts, where a next-open entry and a stop entry fill at their fill bar's start and a close entry at its signal bar's close: the 60 minutes are measured from that last pre-roll bar, not from the roll bar, so the CME daily break between them does not count towards them. On a regular bar grid this is a fill in the last 60 minutes of trading, measured to that bar's close. The second is a signal produced before a roll whose fill would land on or after the roll bar, whatever the bar spacing, because its stop is in the old contract's prices. The third is a stop entry on the roll bar itself, because its level comes from old-contract bars. Indicators still see the jump, and the chosen contract reaches strategies as a numeric `contract` column. Roll handling needs a local CSV with a `symbol` column; yfinance futures such as `MNQ=F` do not expose their roll dates, so they keep hidden roll gaps.

## Metrics

- **Sharpe Ratio** is the Daily Sharpe, using a 0% risk-free rate: one point per market session in the loaded period, including market sessions without trades. Each point is the return of the equity (net for the Net value, gross for the Gross value) from one market session's close to the next, starting from the initial capital, with open trades marked at the close. It is annualized with the asset's `bars_per_year["1d"]` (252 for futures; the configured value for crypto and forex), so the same trades give the same Sharpe on any low timeframe. A period needs at least two market sessions for a Sharpe.
- **Return**, **Max DD**, and **Return / DD** use the bar-level equity curve.
- **MFE R** is the position-weighted peak trade value: banked partial fills plus the remaining position's favorable value.
- **Giveback R** is favorable peak profit not retained at exit; straight losses that were never profitable have zero giveback.

Undefined Sharpe and Return/DD values display as `N/A`. With `--with_costs`, outcome and financial columns show Gross / Net pairs.

### Out-of-sample split

`--oos_start YYYY-MM-DD` or `--oos_fraction F` (one of them, `0 < F < 1`) cuts the loaded `--time_period` into an in-sample period and an out-of-sample period. The cutoff is the first bar of a market session, in the asset's session timezone: the first market session starting on or after the date, or the bar at `ceil((1 - F) x bars)` moved forward to the next market session start. It is computed per asset and low timeframe, and the result header lists both date ranges. Each variant is still backtested once over the whole period, with no capital reset at the cutoff; its equity curve is then split so the out-of-sample period starts from the equity at the close of the last in-sample bar. Trades belong to the period of their entry bar. The usual trade-count and financial columns describe the in-sample period, followed by `OOS Trades`, `OOS Return`, `OOS Max DD`, `OOS Sharpe` (the Daily Sharpe of the out-of-sample market sessions; the in-sample `Sharpe Ratio` uses only in-sample ones, and the cutoff keeps every market session in one period), `OOS Return / DD` and `Spanning` (trades entered in sample that exit at or after the cutoff; their P&L lands in the out-of-sample curve). `Discarded` is the exception: it stays a whole-period count, not an in-sample one. `--max_trades` cannot be combined with a split.

In split mode the variants of each strategy and asset are ranked on an in-sample metric, `--select_by return_dd|sharpe|return` (default `return_dd`), using Net values with `--with_costs` and Gross without. Higher is better, a missing (N/A) value ranks last and ties keep the table order. The top `--select_top N` (default 5) are marked `Selected`, the table sorts by `IS Rank` within each strategy and asset, and `OOS Rank` ranks the same metric on the out-of-sample period. `WFE` is the out-of-sample annualized return divided by the in-sample annualized return, as a percentage, where annualized return is `(1 + Return) ^ (bars_per_year / bars in the period) - 1` for the low timeframe; it is blank when the in-sample annualized return is zero or negative. Both selection flags require `--oos_start` or `--oos_fraction`.

The result header (console and results page) also shows, for each strategy and asset, how the whole population of variants did out of sample, so the selected variants can be judged against it. `Tried` is the number of variants. `OOS profitable` is the share of variants with an OOS Return above 0. `Equal-weight OOS` is the out-of-sample return of the average of every variant's out-of-sample equity curve, each first rescaled to start at 1, which equals the mean OOS Return. Both use Net with `--with_costs` and Gross without, from the OOS Return as shown (rounded to 2 decimals). A variant with no `OOS Trades` counts as not profitable and contributes a flat curve, even when a spanning trade gives it a non-zero OOS Return.

```bash
"$PY" -m tester_framework --strategies ema50 --asset MGC --high_timeframe 1h --time_period 2021 --data_source local --oos_start 2021-07-01 --with_costs
```

A trade still open on the last bar of the loaded data closes at that bar's close with exit reason `end`, after any partial fills it already banked; costs apply as for any exit, and it counts like any other trade. Signals skipped due to invalid stops, bad entries, a close entry on the last bar, a stop entry whose bar never reaches its level, minimum risk, overlap, a setup time before the first high-timeframe bar after the previous exit, a fill in the 60 minutes before a roll or across one, or minimum-quantity filters are reported in `Discarded`.

## Strategy API

Put local strategies in `strategy/<name>.py`:

```python
def generate_signals(df, asset, timeframe, params):
    return signals  # columns: time, side, stop
```

`side` must be `long` or `short`. The strategy owns entries and stops; the framework owns fixed, trailing, and partial exits.

`params["tick_size"]` contains the configured price tick for the current asset. `params["pip_size"]` contains its pip, the unit of `pips` settings: the optional `pip_size` asset field (0.0001 for EURUSD and GBPUSD), defaulting to `tick_size`. `params["session"]` is either `None` or the parsed session mapping selected by `--sessions`.

The input `df` is shared across timeframes; strategies **must not mutate** it. Copy inside the strategy if needed.

`timeframe` is the high timeframe. A strategy can read a smaller low timeframe by setting a module-level constant; `df` then holds low-timeframe bars, which the engine also fills on:

```python
LOW_TIMEFRAME = "5m"  # data loaded at 5m, signals generated per --high_timeframe
```

`LOW_TIMEFRAME` is the default; `--low_timeframe 1m,5m` runs a declaring strategy on each listed low timeframe as its own variants. Each listed value, and the `LOW_TIMEFRAME` default, must be a configured timeframe with `bars_per_year` for every asset, no larger than each high timeframe, and divide it evenly. A low timeframe equal to the high timeframe is a single-timeframe run. A strategy without `LOW_TIMEFRAME` rejects the flag. The results table shows `High TF` and `Low TF`, and variant labels, page titles and trade chart file names name both.

Strategies can add indicators to trade charts with an optional hook:

```python
def plot_indicators(fig, data, view, asset, timeframe, params):
    ...
```

The framework calls this hook for each trade HTML. Keep indicator plotting in the strategy because only the strategy knows which lines matter.

A signal may carry a `setup_time`: the start of the latest bar its setup depends on, a UTC-naive timestamp no later than `time`. After a trade's final exit, a signal whose `setup_time` is before the open of the first high-timeframe (`--high_timeframe`) bar starting after the exit bar's start is discarded and counted in `Discarded`. Intraday high-timeframe bars are clock-aligned and daily ones open at the asset's market session start, and each RR and exit mode uses its own exits. A signal without `setup_time`, or with NA, is not checked.

Any extra signal column beyond `time`, `side`, `stop`, `entry`, `stop_entry`, and `setup_time` (for example `vwap`) is copied onto the trade passed as `params["trade"]` when its value is not NA. Engine-owned trade keys such as `target` or `qty` win over same-named signal columns. `plot_start_time` is parsed as a datetime and moves the chart start back to it.

Strategies can also add scalar metrics to each expanded result variant:

```python
def calculate_metrics(data, signals, trades, asset, timeframe, params):
    return {"Strategy metric": value}
```

`params` contains the strategy parameters, the session window, the effective RR, exit mode, operation, risk percentage, capital, costs flag, time period, data source, and the asset's tick size and pip size. The hook is called even when a variant has no completed trades.

### Strategy parameters

A strategy declares tunable settings as module-level `PARAMS`, a dict of name to default. A `None` default can name its type in `PARAM_TYPES`:

```python
PARAMS = {"swing_length": 20, "entry_mode": "close", "retest": True, "max_touch_minutes": None}
PARAM_TYPES = {"max_touch_minutes": int}

def validate_params(params):  # optional
    if params["swing_length"] < 1:
        raise ValueError("swing_length must be at least 1")
```

Defaults must be int, float, bool, str or `None`, and `PARAM_TYPES` types one of int, float, bool or str. The names the engine puts in `params` are reserved: `tick_size`, `pip_size`, `session`, `trade` and the `calculate_metrics` keys `risk_reward_ratio`, `exit_mode`, `operation`, `risk_pct`, `capital`, `with_costs`, `time_period` and `data_source`. Declaring one fails when the strategy loads.

`--strategy_params` sets parameter values as `;`-separated `name=value` items. A `,`-separated list runs every value:

```bash
"$PY" -m tester_framework --strategies my_strategy --strategy_params "swing_length=10;entry_mode=break;max_touch_minutes=none"
"$PY" -m tester_framework --strategies my_strategy --strategy_params "swing_length=10,20;max_touch_minutes=15,30"
```

Each combination of the listed values, the cartesian product in the order the names were given, is a separate set of variants; the second command runs four. A value listed twice runs once. Signals are generated once per combination and shared across its RR and exit-mode variants.

Values are parsed to the default's type: whole numbers for int, finite numbers for float, `true`/`false` for bool, and text for str. `none` is accepted only for a `None` default. With a `PARAM_TYPES` entry, any other value must parse to that type; without one, it stays text. String values must not contain `,` or `;`. An undeclared name, a value that does not parse, a name given twice, or a name some strategy in `--strategies` does not declare fails before data loads. `validate_params` then receives every declared parameter of each combination, defaults filled in, also when the flag is omitted; a `ValueError` it raises is reported with the strategy name before data loads.

The run prints its variant count, RR and exit modes included, to stderr before data loads, and refuses more than 2,000 variants unless `--max_variants N` raises the cap. The cap is checked on the product of the value counts, before any combination is built or passed to `validate_params`.

`params` in `generate_signals`, `plot_indicators` and `calculate_metrics` holds every declared parameter with defaults filled in. Each variant page lists every parameter value. The results table's Params column and the progress display's variant labels show the values that differ from their defaults; the column is hidden when no variant has one. In split mode, parameter combinations are ranked with the other variants of their strategy and asset. A strategy without `PARAMS` behaves as before.

## ORB Strategies

Three Opening Range Breakout strategies share common logic in `strategy/utils/`:

- **`orb_candle`** — SL from the breakout candle's extreme (low - tick for long, high + tick for short). Always trades on breakout.
- **`orb_fvg`** — SL from a Fair Value Gap boundary (FVG bottom - tick for long, FVG top + tick for short). Only trades when a directional FVG exists at breakout time.
- **`orb_combined`** — SL from the FVG extension line (ext price - tick for long, ext price + tick for short). Only trades when a directional FVG with an extension line exists.

All three use a 1-minute low timeframe (`LOW_TIMEFRAME = "1m"`). For these strategies, `--high_timeframe` is the opening-range duration rather than the bar size they trade on; every configured intraday duration from 1 minute through 1 hour is supported. `--low_timeframe 1m,5m` compares 1-minute and 5-minute triggers and fills. They require `--sessions` and build the ORB from that session's local start time, then look for breakouts until the session window ends. FVG detection and mitigation tracking use the same session window. The SL offset uses the asset's configured tick size.

```bash
"$PY" -m tester_framework --strategies orb_candle,orb_fvg,orb_combined --asset MNQ --high_timeframe 5m --sessions ny=09:30-12:00 --risk_reward_ratio 2 --with_costs
```

### Differences From Pine

The Pine references in `pine_scripts_examples/` differ from these strategies on purpose. Each difference is pinned by a test in `tests/test_pine_differences.py`.

- **Day boundary.** Pine calls `strategy.close_all` on the first bar of a new New York calendar date and fills at that bar's close, so on 24-hour futures data an open trade is held through the evening session until the 00:00 New York bar. By default we hold a trade until its stop, its target, or the end of the data; only with `--session_exit` do we never hold past the session window. The closest equivalent is `--sessions ny_bank=09:30-17:00 --session_exit`, which exits at the close of the last bar starting before 17:00 New York, not at the date change.
- **Target fills.** TradingView fills a target on a touch and fills a gap beyond it at the better open. By default we need a trade one tick beyond the target and fill a gap at the target price (`--target_fill tick_through`); `--target_fill touch` restores TradingView's rule, and the other tests in `tests/test_pine_differences.py` pin it.
- **Sizing.** Pine trades at least one contract, `max(1, floor(risk / R))`, and caps the size at what equity can margin at 5% of notional. We discard a trade whose risk-based quantity is below the asset's `min_qty` and apply no margin cap.
- **FVG bars.** Pine checks `high[2] < low` on every bar in its FVG window, so on the window's first two bars an FVG can use bars from before the window. We only use bars inside the session window, so the first two window bars cannot complete an FVG.
- **FVG cap.** Pine keeps at most `Max Tracked FVGs` (default 100) and drops the oldest. We keep every active FVG.
- **CHoCH double wick break.** `choch_sr` with `entry_mode=break` has no Pine equivalent: breaks are confirmed by the wick and the trade enters with a stop entry one tick beyond the broken swing. The Pine's wick confirmation gives no trade on a bar that breaks both active swings (`ambiguousWickBreak`), and processes the high break before the low. Skipping the bar would need its full range, which a resting order cannot know. We rest at most one order per bar: at the swing whose break is a CHoCH as the structure stood when the bar opened, i.e. after the previous bar's break direction. It fills whenever the bar reaches its level, whatever else the bar breaks. A break that becomes a CHoCH only because of an earlier break inside the same bar never fills. Both breaks still enter the structure history in intrabar path order, so the later one sets the break direction: an open at or beyond a swing's stop entry level breaks that swing first; otherwise an open strictly nearer the low breaks the low first, and any other open the high.
- **CHoCH stop swing.** `choch_sr` with `stop_source=last_swing` has no Pine equivalent: the Pine always places the stop one tick beyond the last marked LL (long) or HH (short), which is our default `last_ll_hh`. `last_swing` places it beyond the most recent confirmed swing of any label instead.
- **FVG window.** Pine detects FVGs in a separate `FVG Session` input that defaults to 09:30-12:00 New York, and its trade window is hardcoded to 09:30-11:59. Both of ours follow the `--sessions` window, so a wider window detects and trades FVGs outside 09:30-12:00.

## CHoCH + SR Strategy

`choch_sr` ports `pine_scripts_examples/choch_sr_mtf_strategy.pine`: a touch on a high-timeframe support or resistance channel, then the first CHoCH the opposite way on 1-minute bars.

Every trade carries `touch_to_choch_minutes`, the minutes from the close of the bar where the touch's current uninterrupted contact started to the close of the CHoCH bar. A high-timeframe bar that opens clear of the channel interrupts the contact, and the next contact starts the count again.

- **`max_touch_minutes`** (default `none`, which matches the Pine) keeps a touch from giving a signal once its contact started more than that many minutes before the bar's close. While price stays in the channel the touch stays too old; it can give a signal again only after the contact is interrupted and restarts. So no trade's `touch_to_choch_minutes` exceeds the limit.
- **`entry_mode`** (default `close`, which matches the Pine) is recorded on every trade. `close` enters at the CHoCH bar's close. `break` confirms every swing break by the wick, a high or low that reaches the stop entry level, and enters with a stop entry one tick beyond the broken swing on the CHoCH bar. The level, the touch and the stop swing all come from earlier bars, so the order could rest before the CHoCH bar opens: the stop swing is the one known on the bar before the CHoCH, not a swing the CHoCH bar itself confirms. The stop entry level is the first tick-grid price at least one tick beyond the swing, so on data off the tick grid (such as yfinance) a wick less than that beyond the swing is not a break. The `stop_min`–`stop_max` filter measures from the price the trade fills at: the level, or the CHoCH bar's open when it opens beyond the level.
- **`stop_source`** (default `last_ll_hh`, which matches the Pine) picks the swing the stop sits one tick beyond and is recorded on every trade. `last_ll_hh` uses the last marked LL for a long and the last marked HH for a short. `last_swing` uses the most recent confirmed swing low for a long and swing high for a short, whatever its label and even after it has been broken. The stop distance limits apply to either.
- **`touch_buckets`** (default `0-5|5-15|15-30|30-60|60+`) sets the time bands for the strategy metrics: `Trades <band>`, `Win rate <band>` (the percentage of trades with R > 0) and `Avg R <band>`, with net R under `--with_costs` and gross R otherwise. Bands are `[low, high)` minutes, start at 0, leave no gaps or overlaps, and only the last may be open-ended. A band with no trades shows 0 trades and blank win rate and Avg R. Band names add `m`, as in `Win rate 5-15m` and `Avg R 60+m`. Bands are separated by `|`, since `--strategy_params` splits values on `,`: `--strategy_params "touch_buckets=0-10|10+"` sets two bands, and `touch_buckets=0-10|10+,0-30|30+` sweeps two band sets.

**Known overstatement.** When contact continues across a trade's exit, the engine only accepts a retest from the first high-timeframe bar after the exit, but the strategy does not know that bar. `touch_to_choch_minutes` and the `max_touch_minutes` age still count from the earlier start of the contact, so they overstate the time for those trades.
