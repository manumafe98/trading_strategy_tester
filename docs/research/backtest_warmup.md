# Warm-up history before the backtest start

Question: how do established engines load indicator and strategy history from before the backtest start
date, keep those warm-up bars out of trades, equity and metrics, and decide how much to load? Which approach
fits this repo, and how much warm-up does `choch_sr` need? Engine claims come from official docs or source
code. Code is cited at a pinned commit with file and line numbers. Doc text was read through a fetch tool, so
code lines are preferred where both exist. "Unverified" means I could not trace the claim to a primary
source.

**The problem.** A run starts with empty context on the first bar of `--time_period`, because
`filter_local_period` cuts the data before any strategy sees it. `choch_sr` builds 15m support/resistance
channels from pivots and 1-minute market-structure swings, so its signals depend on how much history comes
before the period. A 2026-only run built one channel on Jan 5. A run with history from 2025-12-07 built four,
matching TradingView. A roll resets the same context inside the period (`contract_segments` in
`strategy/choch_sr.py`).

## Summary

- **Three ways to size warm-up:**
  - **Strategy-declared bar count:** freqtrade's `startup_candle_count`, LEAN's `set_warm_up(bars)` and
    `WarmUpPeriod`, backtrader's minimum period.
  - **Engine-wide span:** MT5 loads from the start of the previous calendar year and needs at least 100
    bars. LEAN's `set_warm_up(timedelta)` is also a span.
  - **Load everything and gate entries:** TradingView regular mode, the Pine date-filter pattern, zipline's
    bundle history.
- **Keeping warm-up out of results:**
  - **Trimming before the simulation:** freqtrade trims the startup candles from the analysed dataframe.
    vectorbt's demo slices signals before simulating. zipline truncates the pipeline's extra rows.
  - **Blocking at the engine:** LEAN rejects orders while `IsWarmingUp` and skips chart samples. backtrader
    calls `prenext` instead of `next`.
- **TradingView does not warm up for you.** Regular mode runs from the chart's first bar, which depends on
  the plan's bar limit. Deep Backtesting with a custom range starts its calculations at the range start, so
  results depend on the start in both modes.
- **Count bars in the data, not seconds.** freqtrade turns `N` candles into `N × timeframe seconds`. Its rows
  agree with that only because it fills missing candles by default, which suits 24/7 crypto. LEAN resolves a
  bar count through exchange hours. Here the daily break, weekends and holidays mean seconds and bars differ,
  and flat filler bars would distort pivots, so this repo should count the bars it has.
- **Missing history moves the start forward** in freqtrade and MT5. zipline raises an error instead.
- **Recommendation:**
  - **Declare per strategy.** An optional strategy hook returns its warm-up in high-timeframe bars from
    `params` and the high timeframe.
  - **Load and trim in the engine.** The engine loads the largest declared warm-up per asset and low
    timeframe, counting bars back in the loaded index. It gives the strategy the extended frame, drops
    signals before the period start, and gives `run_backtest`, the split and all metrics only the period's
    bars.
- **`choch_sr` needs about 622 high-timeframe bars.** That is 2 × 311 bars, and 311 is the channels' exact
  lookback. On local data 311 bars was enough in two of three windows, and every warm-up of 450 bars or more
  matched the longest-history reference. That rests on three windows at default parameters. At 15m that is about 7 MES market sessions, or 6.5 days of EURUSD and
  BTCUSD bars. Rolls inside the period still restart the context; warm-up before the start cannot fix that.

## Engines

### TradingView Pine

- **Execution start.** By default a script runs once per historical bar "starting with the first bar", or
  from `calc_bars_count` bars before the latest bar when that is set
  ([execution model](https://www.tradingview.com/pine-script-docs/language/execution-model/), "Executions on
  historical bars").
- **How far back that is.** Chart history is a bar count by plan, at least 5,000 bars and up to 40,000 for
  Ultimate ([limitations](https://www.tradingview.com/pine-script-docs/writing/limitations/), "Chart bars";
  [intraday limits](https://www.tradingview.com/support/solutions/43000480679-historical-intraday-data-bars-and-limits-explained/)).
  So in regular mode the warm-up is whatever history the chart happens to hold.
- **`max_bars_back`** sizes the historical buffer that `[]` references can reach: 5,000 bars for most series
  by default, and a runtime error beyond it ([limitations](https://www.tradingview.com/pine-script-docs/writing/limitations/),
  "Maximum bars back"). It does not move where execution starts. The reference text for `max_bars_back()`
  and `calc_bars_count` did not load (the reference is rendered client-side): **unverified** beyond the
  limitations page.
- **Date-range pattern.** The strategies FAQ gates `strategy.entry` with a time-range condition. Its RSI is
  still computed on every bar, so the bars before the range act as warm-up
  ([FAQ: strategies](https://www.tradingview.com/pine-script-docs/faq/strategies/)).
- **Deep Backtesting.** It runs on all of the symbol's history and lets the user pick a date range
  ([what is Deep Backtesting](https://www.tradingview.com/support/solutions/43000666199-what-is-deep-backtesting/)).
  With a custom range, "script calculations will start at the beginning of the date range". Regular mode
  begins on the chart's first bar, so EMA/RMA values and trades can differ between the two
  ([why regular and Deep Backtesting differ](https://www.tradingview.com/support/solutions/43000666266-why-do-the-data-of-the-regular-mode-and-deep-backtesting-not-match/)).
  So TradingView has **no warm-up concept**. A custom range behaves like this repo does today.
- **Bar replay.** Replay depth is a time span by plan
  ([replay data](https://www.tradingview.com/support/solutions/43000692816-how-much-data-is-available-for-bar-replay/)).
  The help page does not say whether a strategy computes on history left of the replay start: **unverified**
  from TradingView sources. The repo's `docs/agents/pine-parity.md` records, from local observation, that
  the strategy backtests over the 1m history TradingView loaded before the replay point.

### Backtrader

At commit `b853d7c` of [mementum/backtrader](https://github.com/mementum/backtrader):

- **Minimum period propagation.**
  - **Indicators take the largest minimum period of their inputs.** An indicator starts from the largest
    minimum period of its inputs (`lineiterator.py:120-121`) and then of its own lines (`:134-135`).
  - **Stacking adds the period minus one.** Feeding a period adds `period - 1` (`lineroot.py:349-353`), so
    an SMA(5) of an SMA(10) needs 14 bars.
  - **The strategy takes the largest of its indicators.** Its minimum period is the maximum over its
    indicators (`strategy.py:212-215`).
- **The three phases.** `_next` calls `prenext` while the minimum period is not met, then `nextstart` once,
  then `next` (`lineiterator.py:259-284`). The default `prenext` is empty and `nextstart` calls `next`
  ([strategy docs](https://www.backtrader.com/docu/strategy/)).
- **Warm-up is the feed's first bars.** In the files read, warm-up is simply the first `minperiod - 1` bars of
  the data. I did not find a backtest start date separate from the data feed's own range: **unverified**.
- **Analyzers see warm-up bars.** An analyzer's default `prenext` calls its `next` (`analyzer.py:223-229`).
- I found no check that blocks orders during `prenext`. The engine only does not call `next`:
  **unverified** beyond that.

### QuantConnect LEAN

At commit `32a302a` of [QuantConnect/Lean](https://github.com/QuantConnect/Lean):

- **`SetWarmUp(int barCount)`** resolves a start date that sends that many bars. It uses the smallest
  resolution among the securities unless a resolution is passed (`Algorithm/QCAlgorithm.History.cs:105-152`).
  With securities added, the start comes from `CreateBarCountHistoryRequests` and the earliest one is taken
  (`:184-191`). That helper computes each start with `GetStartTimeAlgoTz` from the security's exchange hours
  (`:1144-1163`). So a bar count respects market hours.
- **`SetWarmUp(TimeSpan)`** subtracts the span from the start, `Time - _warmupTimeSpan` (`:219-221`).
- **Warm-up ends when algorithm time reaches the start date** (`Algorithm/QCAlgorithm.cs:1328-1331`).
- **Orders are rejected while warming up.** `PreOrderChecksImpl` returns `OrderResponse.WarmingUp`
  (`Algorithm/QCAlgorithm.Trading.cs:844-846`). The docs say "You can't place trades during the warm-up
  period" ([warm-up periods](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods)).
- **Chart samples are skipped** while warming up: "Sampling during warming up period skews statistics"
  (`Engine/Results/BacktestingResultHandler.cs:659-663`).
- **Indicator warm-up.**
  - **Declared period:** an indicator declares `WarmUpPeriod` through `IIndicatorWarmUpPeriodProvider`.
  - **History request:** `WarmUpIndicator` requests that many bars of history, scaled for consolidated
    periods (`Algorithm/QCAlgorithm.Indicators.cs:3600-3630`). Other indicators get a warning
    (`:3633-3639`).
  - **Automatic:** `EnableAutomaticIndicatorWarmUp` is obsolete in favour of
    `Settings.AutomaticIndicatorWarmUp` (`:67-77`). The docs say it "doesn't work for all indicators", and a
    history request "may not return enough data"
    ([automatic indicators](https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/automatic-indicators)).

### Zipline (zipline-reloaded)

At commit `5dd9fef` of [stefan-jansen/zipline-reloaded](https://github.com/stefan-jansen/zipline-reloaded):

- **Pipeline lookback.** Each term asks for `extra_rows` before the start date (`src/zipline/pipeline/term.py:328-355`).
  The engine builds its asset mask from `extra_rows` sessions before the start (`pipeline/engine.py:483-488`),
  and raises `NoFurtherDataError` when the calendar has fewer sessions (`:472-478`). It truncates the extra
  rows from the outputs (`:704-708`).
- **`data.history` windows** can reach before the simulation start. They fail only before the bundle's first
  trading day (`src/zipline/data/data_portal.py:751-756`, with `first_trading_day` taken from the bundle's
  minute bar reader in `utils/run_algo.py:161-166`). The error says to "start the backtest on or after"
  a suggested day (`errors.py:731-735`).
- So zipline warms up from whatever the bundle holds before the start, sized by each window's length. That
  the simulation, orders and metrics begin at the start date is how `run_algorithm` is used, but I did not
  trace it in the code: **unverified**.

### NautilusTrader

At commit `80059a7` (develop) of [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader),
Rust engine:

- **Data before the run start is skipped and never delivered:** "Skip data before start_ns"
  (`crates/backtest/src/engine.rs:914-919`).
- **Indicators expose `initialized()`** (`crates/indicators/src/indicator.rs:33`). For an SMA it becomes
  true once `count >= period` (`crates/indicators/src/average/sma.rs:163`).
- **Warm-up is the strategy's job.** The strategies guide requests history in `on_start` "to hydrate the
  indicators". Registered indicators receive the response bars before `on_historical_bars()`, and the
  strategy should check `indicators_initialized()` before acting
  ([strategies](https://nautilustrader.io/docs/latest/concepts/strategies/), "Handlers").
- Whether such a request in a backtest can return bars from before the run start: **unverified**.

### vectorbt

At commit `9ac1b69` of [polakowo/vectorbt](https://github.com/polakowo/vectorbt):

- **Warm-up shows as NaN.** Built-in moving averages use `minp=window`, so the first `window - 1` values are
  NaN (`vectorbt/indicators/nb.py:24-28`, `generic/nb.py:783-788`). That crossovers on NaN are false, so the
  warm-up gives no signals, follows NumPy comparison rules. I did not check it in `crossed_above`:
  **unverified**.
- **A portfolio cannot be sliced in time:** "Changing index (time axis) is not supported"
  (`vectorbt/portfolio/base.py:411-412`). Trimming has to happen before the simulation.
- **The pattern in a vendor demo.** The [vectorbt.pro](https://vectorbt.pro/) homepage has an agent demo,
  not reference docs. It extends the close back by `slow + 1` bars, computes the averages, slices the
  signals with `.iloc[warmup:]`, and simulates only on the requested range.

### MetaTrader 5 strategy tester

From [How the Tester Downloads Historical Data](https://www.metatrader5.com/en/terminal/help/algotrading/test_preparation):

- **Pre-start buffer.** The tester always loads a "pre-start history buffer". For D1 and below it reaches
  "from the beginning of the previous calendar year", at least one year. The tester also loads extra data
  so that indicators calculate from the first bar.
- **At least 100 bars** are required on the selected timeframe before modelling begins.
- **The start moves forward when history is short.** The tester logs, for example, "start time changed to
  ... to provide data at beginning".
- This is an engine-wide calendar span. The EA does not declare a need.

### freqtrade

At commit `a025e7f` of [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade), and the
[strategy customization docs](https://www.freqtrade.io/en/stable/strategy-customization/), "Strategy startup
period":

- **Declaration.** A strategy sets `startup_candle_count` to "the maximum number of candles that the strategy
  requires to calculate stable indicators". The docs' example is 400 for an EMA100 on 5m.
- **Several strategies.** Backtesting takes the maximum over the strategy list
  (`freqtrade/optimize/backtesting.py:227`).
- **Loading and trimming.**
  - **Loading is by seconds:** it extends the start by `timeframe_seconds × startup_candles`
    (`freqtrade/data/history/datahandlers/idatahandler.py:431-432`).
  - **Trimming is by rows:** it drops the first `startup_candles` rows after the indicators are computed
    (`freqtrade/data/converter/converter.py:190-192`, called at `backtesting.py:546-547`).
  - **Why they agree.** `load_data` fills missing candles with "No action" candles by default
    (`fill_up_missing: bool = True`, `freqtrade/data/history/history_utils.py:93`), and backtesting's main
    load does not override it (`backtesting.py:364-373`). The seconds and the rows therefore cover the same
    span. Without filling, data with gaps would have its first period candles trimmed.
- **Missing startup data.** If the data starts at or after the requested start, the start moves forward by
  `startup_candles` candles (`freqtrade/configuration/timerange.py:137-155`).
- **Limit.** The count should stay below `ohlcv_candle_limit × 5`, because live trading only has that much
  history (docs).

## Comparison

| Engine | How the size is decided | How warm-up stays out of results | Missing history |
|---|---|---|---|
| TradingView regular | Chart history (plan bar limit) | Entries gated by script logic only | n/a |
| TradingView Deep (custom range) | None: starts at range start | n/a | n/a |
| Backtrader | Strategy minperiod = max of indicators | `prenext` instead of `next`; analyzers still run | First bars are the warm-up |
| LEAN | `SetWarmUp` bars (market-hours aware) or span; indicator `WarmUpPeriod` | Orders rejected; chart samples skipped | History may be short (docs) |
| Zipline | Each window's length / pipeline `extra_rows` | Simulation starts at start date; extra rows truncated | Error |
| NautilusTrader | Strategy's own history request | Data before start not delivered; check `initialized()` | Unverified |
| vectorbt | User code | Slice before simulating | NaN |
| MT5 | Engine: previous calendar year, ≥ 100 bars | Test starts at start date | Start moved forward |
| freqtrade | Strategy `startup_candle_count`, max over strategies | Trimmed before backtesting | Start moved forward |

**Trade-offs:**

- **Fixed bar count** (freqtrade, LEAN bars):
  - It is exact for indicators whose lookback is a bar count.
  - It must be counted in the data's own bars, not converted to seconds.
- **Time span** (MT5, LEAN timedelta):
  - It is simple and needs no declaration.
  - It is too short for slow timeframes and wasteful for fast ones.
  - On session data its bar yield varies with weekends and holidays.
- **Indicator-declared** (backtrader, LEAN `WarmUpPeriod`):
  - It is automatic when every indicator is a library object.
  - The repo's strategies are plain pandas and loops, so there is nothing to introspect.
- **Load everything and trim** (Pine regular, zipline bundle):
  - It is the most faithful for state with unbounded memory.
  - The cost grows with history, and results still depend on where the data happens to begin.

## What fits this repo

### Recommendation

1. **Declare per strategy, in high-timeframe bars.**
   - **The hook.** An optional module-level `warmup_bars(params, timeframe)` returns how many high-timeframe
     bars the strategy needs. A missing hook means 0, which keeps today's behaviour. The ORB strategies use
     only session window bars. `ema50` was not examined here, but its `ewm(adjust=False)` also starts cold
     on the first bar, so it may want a hook too.
   - **Why a hook and not a constant.** The need depends on `--high_timeframe` and on swept parameters such
     as `swing_length`.
   - **Why high-timeframe bars.** They are the unit of `choch_sr`'s longest lookback. A strategy converts any
     low-timeframe need itself.
   - This is freqtrade's model, with the unit fixed to what the strategy computes on.
2. **Load once per asset and low timeframe, at the largest need.** `load_data` runs once per asset and low
   timeframe (`tester_framework/__main__.py:334`). It should load the maximum over that group's strategies,
   high timeframes and parameter combinations, as freqtrade does across its strategy list. Extra warm-up is
   harmless.
3. **Count bars in the loaded data.**
   - **Local data.** `read_local_csv` already holds the whole file. Find the period start in the index,
     floor the earlier bars to the high timeframe as `segment_signals` does, and step back N distinct
     high-timeframe bars. Then cut from there.
   - **Why count.** This handles the daily break, weekends and holidays exactly, which `N × 15 minutes` does
     not. It is LEAN's market-hours-aware count without the calendar.
   - **Trim by timestamp, not by row count.** That avoids freqtrade's mismatch.
4. **Keep two views of one frame.**
   - **Extended frame.** It holds warm-up plus period and goes to `generate_signals` and `plot_indicators`
     only.
   - **Period frame.** `data.loc[period_start:]` goes to `run_backtest`, `calculate_metrics`, `oos_cutoff` and
     `Split.from_index`.
   - **Cache.** The cached frame in `runner.py:139-160` can stay the extended one if the task carries
     `period_start`.

### Keeping warm-up bars out of signals, fills, equity and metrics

- **Signals.** Drop every signal whose `time` is before the period start, after `filter_signals`. This is the
  Pine date-filter pattern and LEAN's order block, done once in the engine.
  - **A close entry on the last warm-up bar** would fill before the start, so it is dropped.
  - **A next-open entry signalled on the last warm-up bar** is dropped too, even though it would fill on the
    first period bar. Its signal bar is outside the period, and LEAN would have rejected the order then.
  - **A stop entry** carries its fill bar as `time`, so it is kept when that bar is in the period.
  - Dropped warm-up signals are not counted in `Discarded`.
- **Setup time.** A `setup_time` before the period start is allowed. A touch during the warm-up is real
  context, exactly what TradingView sees. The post-exit setup-time rule is unchanged.
- **Fills, equity, Sharpe.** Because `run_backtest` gets only the period frame:
  - The equity curve starts at the initial capital on the first period bar.
  - The Daily Sharpe counts only the period's market sessions.
  - Return/DD, WFE's bar counts and `--oos_fraction`'s `ceil((1 - F) × bars)` use the period's bars.
  - The result header's dates stay the period's.
- **Trade charts.** `write_trade_html` slices the `data` it is given from `plot_start_time`
  (`tester_framework/reports.py:67-76`). To show a channel built before the start, the chart would need the
  extended frame, which would be a separate choice. Given the period frame, the chart starts no earlier than
  the period.
- **Header.** The result header should show the warm-up actually loaded per asset, for example as the first
  bar date, next to the period dates.

### Missing history

- **When it happens.** The data starts after the warm-up start: a period at the file's first bar, `max`,
  yfinance's intraday limits, or a rolling `60d` near the file start.
- **What to do.** Follow freqtrade and MT5: move the effective start forward to the first bar with N
  high-timeframe bars before it, and show it in the header.
- **Why not stop with an error.** An error (zipline) would make `max` unusable with any warm-up.
- **yfinance periods.** `period=60d` has no start to extend, so it would need converting to explicit
  `start`/`end` or warming up from inside the period.

### `--oos_start` and `--oos_fraction`

- **No warm-up at the cutoff.** Each variant is one continuous run across the cutoff (README, "Out-of-sample
  split"), so the out-of-sample period inherits the in-sample state.
- **Only the in-sample start needs warm-up.** The cutoff must be computed on the period frame, or
  `--oos_fraction` would count warm-up bars.

### Futures rolls

- **A roll clears the context.** `choch_sr` clears pivots, channels, touches and swings at every contract
  change (`contract_segments`). Engine warm-up therefore helps only if the period start is far enough after
  the last roll.
- **Example.** The local MES file rolled at 2025-12-15 23:00 UTC. A 2026 run with 622 bars of warm-up starts
  after that roll, so it is fully warmed. A period starting within about 7 market sessions after a roll
  cannot be fully warmed.
- **Rolls inside the period restart the context.** The next MES roll is 2026-03-16 22:00 UTC, and after it
  the context starts cold for another ~311-622 bars. This is the existing "Futures roll" difference in the
  README and is not solved by warm-up (see open questions).

### TradingView parity

- **TradingView's own start varies.** In regular mode its results depend on the chart's first bar, and with
  a custom Deep Backtesting range on the range start. Neither has a fixed warm-up.
- **What a parity run needs.** It should start the local history at TradingView's first bar, as
  `pine-parity.md` already does by slicing. It should compare only after both sides have built context.
- **Keep the warm-up separate from the comparison window.** The new warm-up mechanism allows a compare
  window that starts later than the history. That is also the honest way to read a TradingView match.

### How much warm-up `choch_sr` needs

**Channels (exact lookback), from `strategy/utils/sr_channels.py`:**

- **Confirming a pivot takes 21 bars.** A pivot at bar `c` is confirmed at `q = c + 10` and needs bars
  `c - 10 .. c + 10` (`PIVOT_PERIOD = 10`, window `2 × 10 + 1`). So confirmation at `q` reads `q - 20 .. q`.
- **Pivot life.** A recompute at pivot bar `p` keeps pivots with `p - q ≤ 290` (`LOOPBACK`).
- **Touch count.** It reads bars `p - 290 .. p`.
- **The earliest bar read is `p - 290 - 20 = p - 310`.** So a recompute reads **311 high-timeframe bars**.
- **Channels only recompute on pivot bars.** They match an infinite-history run only from the first pivot
  bar at least 311 bars into the data, plus the one-bar shift to the next high-timeframe bar.

**Everything else has no fixed lookback:**

- **Labels.** The `HH`/`LH` and `HL`/`LL` labels compare each swing with the previous one.
- **CHoCH.** It needs `previous_break_direction`.
- **Default stop.** `last_ll`/`last_hh` (the default `last_ll_hh` stop) can be arbitrarily old.
- **Touches** live as long as a channel matches them.
- **What `swing_length` bounds.** It only bounds swing confirmation, which takes `2 × swing_length + 1` =
  41 one-minute bars at the default, under three 15m bars. These states converge in practice but cannot be
  bounded.

**Measured on local data.** `generate_signals` with default `PARAMS` on 15m was run on slices starting k
high-timeframe bars before a period start, for k in 0, 150, 311, 450, 600, 900, 1,200 and 2,500. The
signals at or after the start were compared over 28 days against the k = 2,500 run, on every
`OUTPUT_COLUMNS` column. Counts are rows in the symmetric difference, so a changed signal counts twice. One
run per window:

| Window (28 days from) | k = 0 | 150 | 311 | 450 | 600 and up |
|---|---|---|---|---|---|
| MES 2026-02-02 | 25, last 2026-02-09 | 9 | 0 | 0 | 0 |
| EURUSD 2026-02-02 | 14, last 2026-02-05 | 5 | 0 | 0 | 0 |
| MES 2025-10-01 | 6 | 2 | 4, on 2025-10-01 | 0 | 0 |

**Reference lengths.** In the MES February window the 2,500 bars start 2025-12-22, after the 2025-12-15
roll. EURUSD has no rolls. In the MES October window, MES rolled at 2025-09-15 22:00 UTC, and
`contract_segments` cuts the k = 1,200 and 2,500 runs at that roll. Their effective history is the roughly
11 market sessions (about 1,000 bars) since then. The k = 900 slice starts after the roll and still matches.

**What the runs show:**

- **311 bars is not always enough.** It covers the channels, but in the October window touches or swings
  still differed.
- **The cold start lasts days.** At k = 0 differences lasted a week on MES. The 2026-only run had only 154
  15m MES bars between 2026-01-01 and 2026-01-05 14:30 UTC (09:30 New York), half of 311, because of the New
  Year holiday. That fits the single channel on Jan 5.
- **Proposed value.** `warmup_bars` = `2 × (LOOPBACK + 2 × PIVOT_PERIOD + 1)` = **622 high-timeframe bars**,
  plus `ceil(2 × (2 × swing_length + 1) × low / high)` for very large `swing_length`. At the default that
  term is `ceil(2 × 41 / 15)` = 6 bars. The value rests on three windows at default parameters: it is a
  floor with margin, not a bound.

**Bars to calendar time.** A bar count's calendar span depends on the asset:

| Asset (local file) | 15m bars per market session or day | 311 bars | 622 bars |
|---|---|---|---|
| MES (CME, 23 h; measured on MES only, MNQ and MGC assumed the same) | 92 | 3.4 market sessions | 6.8 market sessions, about 9-10 calendar days over a weekend |
| BTCUSD (24/7) | 96 | 3.2 days | 6.5 days |
| EURUSD (local file) | 96, weekends included | 3.2 days | 6.5 days |

- **Holiday example.** Before 2026-01-01, 311 MES bars reach back to 2025-12-26 13:15 UTC and 600 bars to
  2025-12-22 06:15 UTC, because Christmas and New Year sessions are short.
- **The local EURUSD file is odd.** It has 96 15m bars on Saturdays, Sundays and Dec 25 in December 2025.
  That is an observation about the file, not a claim about the forex market. It means its bar counts equal
  calendar time.
- **Larger high timeframes.** At 1h, 622 bars is about 27 CME market sessions, five to six weeks.

## Open questions

- **Rolls inside the period.** The CSV holds every expiry, and `_continuous_futures` drops the rows of
  contracts not chosen. So the incoming contract's own bars before the roll exist and could warm it up.
  - **It would fix the restart.** That would remove the cold start after each roll.
  - **It would not match TradingView.** TradingView keeps old-contract channels instead.
  - **It needs its own decision.** That makes it a design decision for an ADR next to ADR 0002, not part of
    warm-up.
- **Should warm-up be overridable?** A `--warmup` flag, as bars or a date, would let parity runs set the
  history start to TradingView's first bar. It would also let a sweep test sensitivity to warm-up.
- **Smoke tests.** `tests/test_smoke.py` pins a `choch_sr` MGC 15m run. Adding warm-up will change its known
  results. That change is intended and needs an edit to the test.
- **Larger check.** Repeat the k-sweep on more windows and assets before fixing 622, including one just
  after a roll and one on a 1h high timeframe.

## Sources

- TradingView Pine docs: execution model https://www.tradingview.com/pine-script-docs/language/execution-model/ ; limitations https://www.tradingview.com/pine-script-docs/writing/limitations/ ; FAQ strategies https://www.tradingview.com/pine-script-docs/faq/strategies/ ; strategies https://www.tradingview.com/pine-script-docs/concepts/strategies/
- TradingView help: Deep Backtesting https://www.tradingview.com/support/solutions/43000666199-what-is-deep-backtesting/ ; regular vs Deep https://www.tradingview.com/support/solutions/43000666266-why-do-the-data-of-the-regular-mode-and-deep-backtesting-not-match/ ; intraday bar limits https://www.tradingview.com/support/solutions/43000480679-historical-intraday-data-bars-and-limits-explained/ ; replay data https://www.tradingview.com/support/solutions/43000692816-how-much-data-is-available-for-bar-replay/ ; bar replay https://www.tradingview.com/support/solutions/43000712747-bar-replay-how-and-why-to-test-a-strategy-in-the-past/
- Backtrader `b853d7c`: https://github.com/mementum/backtrader/tree/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader (`lineiterator.py`, `lineroot.py`, `strategy.py`, `analyzer.py`); docs https://www.backtrader.com/docu/strategy/
- LEAN `32a302a`: https://github.com/QuantConnect/Lean/tree/32a302a9a0fde64c0435798c0c4cb2c2309873ed (`Algorithm/QCAlgorithm.History.cs`, `Algorithm/QCAlgorithm.cs`, `Algorithm/QCAlgorithm.Trading.cs`, `Algorithm/QCAlgorithm.Indicators.cs`, `Engine/Results/BacktestingResultHandler.cs`); docs https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/warm-up-periods , https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/automatic-indicators
- zipline-reloaded `5dd9fef`: https://github.com/stefan-jansen/zipline-reloaded/tree/5dd9fef5d42c2d084021b5ae10454de166cc5541/src/zipline (`pipeline/engine.py`, `pipeline/term.py`, `data/data_portal.py`, `utils/run_algo.py`, `errors.py`)
- NautilusTrader `80059a7`: https://github.com/nautechsystems/nautilus_trader/tree/80059a7d2892d33c4180e8180ae83a89097c0d5d/crates (`backtest/src/engine.rs`, `indicators/src/indicator.rs`, `indicators/src/average/sma.rs`); docs https://nautilustrader.io/docs/latest/concepts/strategies/
- vectorbt `9ac1b69`: https://github.com/polakowo/vectorbt/tree/9ac1b697166b00911def494cf95937274218aa54/vectorbt (`portfolio/base.py`, `indicators/nb.py`, `generic/nb.py`); vectorbt.pro homepage demo https://vectorbt.pro/
- MetaTrader 5 help: https://www.metatrader5.com/en/terminal/help/algotrading/test_preparation
- freqtrade `a025e7f`: https://github.com/freqtrade/freqtrade/tree/a025e7fb3b3acf0e90fd86a9f597aea5b528e1b7/freqtrade (`optimize/backtesting.py`, `data/history/datahandlers/idatahandler.py`, `data/converter/converter.py`, `configuration/timerange.py`); docs https://www.freqtrade.io/en/stable/strategy-customization/
- Repo: `tester_framework/data.py`, `tester_framework/__main__.py`, `tester_framework/runner.py`, `strategy/choch_sr.py`, `strategy/utils/sr_channels.py`, `strategy/utils/market_structure.py`, `strategy/utils/pivots.py`, `docs/agents/pine-parity.md`, README "Execution Model", "Out-of-sample split", "Differences From Pine"
