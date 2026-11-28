# A directional bias filter on the high timeframe

Question: what do traders and quants mean by a directional "bias", how do they decide whether a bias filter
helps a strategy, which backtest pitfalls come with higher-timeframe data, and how should an on/off bias
flag tied to the high timeframe be designed and evaluated in this repo? Engine claims come from official docs
or source code at a pinned commit. Statistical claims come from the original papers. Doc pages were read
through a fetch tool and paper PDFs through `pdftotext`, so code lines are preferred where both exist.
"Abstract only" marks a paper read through its Crossref or publisher abstract. "Practitioner convention"
marks ideas that live only in trading literature. "Citation only" marks a paper whose DOI I resolved to the
title but whose text I did not read. "Unverified" means I could not trace the claim to a primary source.

**The problem.** No strategy here has a directional filter today, and none of the Pine references in
`pine_scripts_examples/` has one either. `choch_sr`'s Pine computes MA 1 and MA 2 on 15m bars, but only to
plot them (`choch_sr_mtf_strategy.pine:115-118,264-267`). The user wants a variant to say whether a bias from the
high timeframe was applied, so that one run shows whether the filter helps.

## Summary

- **"Bias" means a direction taken from a larger bar size, used to allow only longs or only shorts.** The
  common definitions are:
  - **Academic:** a close above or below a moving average, and time-series momentum, the sign of the
    past return.
  - **Practitioner conventions:** higher-high/higher-low market structure, the last candle's colour, the
    previous market session's high and low, and VWAP.
- **The useful evaluation is paired and small:**
  - **Pair the variants.** Run two variants that differ only in the filter, and compare Trades,
    Win Rate, Expectancy R, Max DD and Sharpe.
  - **Look at the trades the filter removes.** If they are not net losers, the filter only shrinks the
    sample.
  - **Test the gap.** Use a permutation or bootstrap on per-trade R, and confirm it out of sample.
- **Filter-on trades are not a subset of filter-off trades.** A signal is discarded while a trade is open,
  and the setup-time rule also applies. So removing one signal can let a later one through. Diffing two
  variants does not show which trades were removed. Tag every signal with its bias and split the baseline's
  trades instead.
- **Every filter toggle is another trial.**
  - **The papers:** White's Reality Check, Hansen's SPA, the Deflated Sharpe Ratio and PBO all correct a
    "best of N" for N.
  - **The cheap part here:** keep the flag inside the existing split and count both settings in `Tried`.
    A multiple-testing correction is not worth building for one flag.
- **Lookahead is the main high-timeframe pitfall.**
  - **Pine:** `request.security` with `lookahead_on` and no `[1]` offset leaks future data on historical
    bars.
  - **This repo:** `resample_data` stamps bars at their start, so a forward-fill of high-timeframe values
    onto low-timeframe bars would leak the close in the same way.
  - **The safe pattern already exists:** `build_channels` writes each result on the next row, and
    `choch_sr` looks it up by `floor(rule)`.
- **"The configured high timeframe" means different things per strategy.**
  - **`choch_sr`:** the high timeframe is larger than the low timeframe, 15m channels over 1m triggers.
  - **ORB:** it is the opening-range duration.
  - **`ema50`:** it equals the low timeframe, the bars it trades on.
  - A bias "on the high timeframe" is therefore only a higher-timeframe bias for `choch_sr` today. This is
    the main open design question.
- **Recommendation:**
  - **Strategy parameter:** a strategy-owned bool `PARAMS` entry, default `False`, computed by a shared
    helper in `strategy/utils/`.
  - **Sweep both settings:** `--strategy_params "bias=false,true"` gives paired variants in one run.
  - **Tag every signal:** an extra signal column records the bias whether or not the filter is on.
  - **Lookahead rule:** the bias comes from the previous closed high-timeframe bar.
  - **Pine and tests:** a "Differences From Pine" entry, and a truncation test for lookahead.

## 1. What "bias" means

A directional or higher-timeframe bias is a rule that reads a larger bar size and permits only one side on
the bar size the strategy trades. The definitions in use:

| Definition | Rule (long allowed when ...) | Evidence |
|---|---|---|
| Moving-average trend | the higher-timeframe close is above its SMA/EMA | On the daily Dow Jones Index from 1897 to 1986, moving-average buy signals earned more than sell signals, with less volatility, in [Brock, Lakonishok & LeBaron (1992)](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x) (abstract only). [Sullivan, Timmermann & White (1999)](https://doi.org/10.1111/0022-1082.00163) re-tested an expanded set of those rules on 100 years of daily DJIA data with White's Reality Check (abstract only; the abstract text I read stops before its conclusion, so the outcome is **unverified** here). Faber's 10-month SMA timing filter is often cited as SSRN 962461; I could not open that record: **unverified**. |
| Time-series momentum | the past return over a lookback is positive | [Moskowitz, Ooi & Pedersen (2012)](https://econpapers.repec.org/RePEc:eee:jfinec:v:104:y:2012:i:2:p:228-250), JFE 104(2), found it in each of 58 liquid futures across equity indices, currencies, commodities and bonds, persisting about 1-12 months (abstract only). |
| Market structure | the higher timeframe makes higher highs and higher lows | Practitioner convention, from Dow theory. The repo's own swing labels and CHoCH (`pine_scripts_examples/indicators/market_structure.pine`, `GLOSSARY.md`) are this idea on one bar size. No academic test found: **unverified**. |
| Candle direction | the last closed higher-timeframe bar closed above its open | Practitioner convention. No primary source found: **unverified**. |
| Previous market session levels | price is above the previous market session's high (or mid) | Practitioner convention. `sr_zone_methods.md` covers these levels as channels, not as a bias. |
| VWAP | price is above VWAP anchored at the market session start | Practitioner convention. The TradingView [VWAP help](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/) defines the indicator but not a bias rule. It needs volume, which forex lacks (`sr_zone_methods.md`). |
| Multi-screen | a long-term trend screen, then an intermediate one, then an entry | Elder's Triple Screen, practitioner convention (described in his book *Trading for a Living*; book and year not checked: **unverified**). |

Two cheap rules cover most of the table: **close versus a moving average** and **candle direction**. The
moving average and time-series momentum are the only definitions with academic evidence, and both are
trend-following. That evidence is on daily and monthly data, not on intraday bars like this repo's high
timeframes, so it says nothing direct about a 15m bias.

## 2. How to judge whether a filter helps

### A/B on the same data

The standard design is two backtests that differ only in the filter, on the same data, costs and exits.
Compare them side by side:

- **Trade count.** A filter always removes trades, so a better ratio from fewer trades must clear a noisier
  bar.
- **Expectancy R and Win Rate.** These say whether the average trade improved.
- **Profit factor.** Gross profit over gross loss. The repo does not report it today, so it would be a new
  metric.
- **Max DD, Daily Sharpe and Return / DD.** These say whether the path improved, not only the mean.
- **The removed trades.** The filter helps only if the trades it drops have negative expectancy. If they are
  break-even, the filter just shrinks the sample.

That last check needs care in an engine with one open trade at a time. Dropping a signal frees the engine,
so a later signal that was discarded may now fill. The on variant's trades are therefore not the off
variant's trades minus the removed ones. Two practical answers:

- **Tag, don't drop.** Run with the filter off, but record on each trade whether it agreed with the bias.
  Then split those trades into with-bias and against-bias. This isolates the signal's quality.
- **The real filtered variant.** Run with the filter on. This gives the path-dependent result, the one that
  would actually be traded.

### Is the difference real?

- **Bootstrap.** Resample per-trade R with replacement and read a confidence interval for the difference in
  mean R between with-bias and against-bias trades
  ([Efron 1979](https://doi.org/10.1214/aos/1176344552), citation only). Trades that cluster in time break
  the i.i.d. assumption. A block bootstrap keeps that dependence by resampling contiguous blocks, for example
  by market session; the stationary bootstrap is one form
  ([Politis & Romano 1994](https://doi.org/10.1080/01621459.1994.10476870), citation only).
- **Random-filter permutation.** Draw many random subsets of the baseline trades of the same size as the
  with-bias set, and take the share whose mean R is at least the with-bias mean as a p-value. This asks
  whether the bias does better than dropping the same number of trades at random. Practitioner convention:
  I found no paper that owns this exact form.
- **Out of sample.** Choose filter on or off in sample, then check the choice out of sample. Walk-forward
  repeats this over rolling cutoffs.
- **Minimum sample.**
  - **The papers' answer:** Bailey and López de Prado relate the sample length needed to trust a Sharpe to
    the number of trials and the returns' shape. Their terms are minimum track record length and minimum
    backtest length (keywords of the [DSR paper](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
    and the [PBO paper](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)).
  - **What I did not find:** a primary source for a fixed minimum trade count such as "30 trades". Treat any
    such number as practitioner convention.
  - **The cheap guard:** report the trade count and the standard error of mean R, `sd / sqrt(n)`, next to
    Expectancy R.

### Many toggles are many trials

Trying one filter, then another, and keeping the best is a specification search. The p-value of the winner
must account for every variant tried:

- **White's Reality Check** bootstraps the best of N rules against a benchmark, so the test reflects the
  whole search. Its null is that the best model of a specification search is no better than the benchmark
  ([White 2000](https://doi.org/10.1111/1468-0262.00152), Econometrica 68(5), 1097-1126, abstract read from
  the [PDF](https://users.ssc.wisc.edu/~bhansen/718/White2000.pdf)).
- **Hansen's SPA test** refines the Reality Check. Adding many bad rules to the search weakens it less,
  and it rejects more often when a rule really is better
  ([Hansen 2005](https://doi.org/10.1198/073500105000000063), JBES 23(4), 365-380; title and journal from
  Crossref, content from a search summary of the abstract, so **unverified** beyond that).
- **Deflated Sharpe Ratio.** The DSR is a Probabilistic Sharpe Ratio whose threshold rises with the number of
  independent trials. Its inputs are:
  - the number of independent trials and the variance of their Sharpe ratios;
  - the sample length;
  - the skewness and kurtosis of the returns.

  Source: Bailey & López de Prado 2014, *Journal of Portfolio Management*, from the
  [PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf). The paper also stresses that N must
  count independent trials, not every run.
- **Probability of Backtest Overfitting (PBO).** PBO is the probability that the configuration that is best
  in sample ranks below the median out of sample. CSCV cuts the time-ordered performance matrix into S
  blocks and uses every combination of S/2 blocks as in-sample, with the rest as out-of-sample. Source: Bailey, Borwein, López de Prado
  & Zhu, revised February 2015, from the [PDF](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
  The journal and year of final publication are not in the PDF I read: **unverified**.
- **Harvey & Liu** turn a Sharpe into a t-ratio and adjust its p-value for the number of tests with
  Bonferroni, Holm or BHY. The result is a "haircut" Sharpe. They call the common flat 50% discount
  "only a rule of thumb" and derive the haircut instead ("Backtesting", *Journal of Portfolio Management*, Fall 2015,
  [PDF](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF)).

For one bias flag the correction is small: two settings, not hundreds. It grows when the bias comes with a
choice of method, length and timeframe, or when several filters are swept together.

## 3. Higher-timeframe pitfalls

### Lookahead and repainting

- **TradingView.** The TradingView pages below were read through a fetch tool, so they are paraphrased.
  - **When values change:** `request.security` with `lookahead_off`, the default, gives a historical
    higher-timeframe value at the end of each higher-timeframe period. With `lookahead_on` it gives the
    value at the start of the period, so it can show values that were not yet known
    ([other timeframes and data](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)).
  - **Realtime bars:** both settings show the unconfirmed developing value, and that value can change after
    a restart, which is repainting
    ([repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/)).
  - **The recommended pattern:** request `expression[1]` with `lookahead_on`, which always uses the last
    confirmed higher-timeframe value. The repainting page says `lookahead_on` without the offset leaks future
    data on historical bars, and that such publications are moderated.
  - **In the repo's reference:** `choch_sr_mtf_strategy.pine:113,264` uses exactly this pattern.
- **backtrader.**
  - **Resampling:** `Resampler.__call__` pushes the stored bar onto the stack only when it is over: on a
    boundary, or after `_checkbarover`. It then restarts the bar
    (`backtrader/resamplerfilter.py:525-553` at commit
    [`b853d7c`](https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader/resamplerfilter.py)).
    With the default `rightedge=True`, the bar is stamped with its ending boundary
    ([resampling docs](https://www.backtrader.com/docu/data-resampling/data-resampling/)).
  - **Replay:** `replaydata` delivers the developing bar on every update instead, while `len()` stays the
    same until a new bar starts ([replay docs](https://www.backtrader.com/docu/data-replay/data-replay/)).
    Replay is the backtest form of an unclosed bar.
- **QuantConnect LEAN.**
  - **Timestamps:** bars carry `Time` and `EndTime = Time + period`, and are delivered at their end time, so
    that an algorithm does not see a bar before it was available
    ([periods](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/time-modeling/periods)).
  - **Consolidators:** a time-based consolidator fires when data arrives at or past the working bar's start
    plus the period, or on a scan at that time
    (`Common/Data/Consolidators/PeriodCountConsolidatorBase.cs:204-206,262-268` at commit
    [`32a302a`](https://github.com/QuantConnect/Lean/blob/32a302a9a0fde64c0435798c0c4cb2c2309873ed/Common/Data/Consolidators/PeriodCountConsolidatorBase.cs)).
- **vectorbt.**
  - **Open-source vectorbt:** it documents `resample_apply`, but I found no alignment guidance there
    ([generic accessors](https://vectorbt.dev/api/generic/accessors/)).
  - **vectorbt PRO:** it realigns by when a value became known. `realign_closing` is for values built from
    close, high, low or volume, and `realign_opening` for values built from the open. The vendor pages warn
    that a plain `reindex(..., method="ffill")` exposes a daily close about a day early
    ([multi-timeframe analysis](https://vectorbt.pro/features/data/multi-timeframe-analysis/), vendor
    feature page, not reference docs).
- **Zipline** was not checked for this note.

The common rule: **a low-timeframe bar may use a higher-timeframe value only from bars that closed at or
before the low-timeframe bar's start.** Bars stamped at their start, as pandas `resample` does by default,
must be shifted one bar before they are joined.

### Other biases, briefly

- **Survivorship bias.** Only instruments that survived are in the data
  ([Brown, Goetzmann, Ibbotson & Ross 1992](https://doi.org/10.1093/rfs/5.4.553), citation only). It matters
  little here: the assets are a fixed list of futures, forex and crypto. Unadjusted futures still show a
  jump at each roll, and a moving average sees it (README "Execution Model").
- **Selection bias and overfitting.** Reporting the best of many variants is covered by section 2.

## 4. How platforms expose a filter toggle

- **Pine.** `input.bool` makes a checkbox in the script's Settings/Inputs tab. Changing any input
  re-executes the script over all available data
  ([inputs](https://www.tradingview.com/pine-script-docs/concepts/inputs/)). A Pine bias filter is
  usually written as `if bias_on and not up: skip long`. That is practitioner usage, with no docs page that
  owns it.
- **TradingView Strategy Tester.** It reports one set of inputs at a time, so an A/B means toggling the
  checkbox and reading the report twice. I found no built-in paired comparison: **unverified**.
- **backtrader.**
  - **Declaring:** a strategy declares `params = dict(...)` and reads them as `self.p.name`.
    `addstrategy(..., name=value)` overrides a default ([concepts](https://www.backtrader.com/docu/concepts/)).
  - **Sweeping:** `optstrategy` takes iterables and runs the `itertools.product` of all of them
    (`backtrader/cerebro.py:859-905` at
    [`b853d7c`](https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader/cerebro.py)),
    so `use_bias=(False, True)` gives a paired sweep.
- **QuantConnect LEAN.** `get_parameter("name", default)` reads a project parameter. Values are typed by
  the default (`str`, `int`, `double`), and the optimizer steps through ranges
  ([parameters](https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/parameters)). A bool
  has to go through an int or a string (from the types listed; not checked in source).
- **This repo.** `PARAMS` with a bool default, swept as `--strategy_params "name=false,true"` (README
  "Strategy parameters"), is the same mechanism as backtrader's `optstrategy`.

## 5. Implications for this repo

### Who owns the bias

There are two candidate homes:

- **Strategy parameter.** AGENTS.md gives strategies the indicators and stops. A bias is an indicator, and
  each strategy knows which bars are its context. A bool in `PARAMS`, with defaults, `PARAM_TYPES` and
  `validate_params`, already gives paired variants with a Params column. Signals are generated once per
  combination, so `bias=false,true` costs one extra signal pass per combination.
- **Framework flag.** There is precedent: `--operation long_only|short_only` filters signals by side in the
  engine (`tester_framework/backtest.py:480-483`). But a framework bias would have to own an indicator and a
  definition of the high timeframe for every strategy. That breaks the split in AGENTS.md.

**Recommendation: a strategy parameter backed by a shared helper.**

- **The helper.** `strategy/utils/bias.py`, for example, takes high-timeframe bars and a method and returns
  `+1`, `-1` or `0` per high-timeframe bar.
- **The strategy.** Each strategy decides whether to expose it. The first is `choch_sr`, whose high
  timeframe is larger than its low timeframe.
- **Mixed sweeps.** `--strategy_params` fails when any strategy in `--strategies` does not declare a name
  (README "Strategy parameters"). So `bias=false,true` works only in runs where every listed strategy
  declares `bias`.
- **`Discarded`.** A filtered signal never reaches the engine, so it is not counted in `Discarded`, which
  counts only engine discards. With the framework option, a new discard reason would be needed.

### Avoiding lookahead

- **The rule.** For a low-timeframe bar starting at `t`, use the bias of the last high-timeframe bar that
  closed at or before `t`.
- **Why a shift is needed.** `resample_data` uses pandas' default left labels for intraday rules
  (`tester_framework/data.py:95-109`), so a high-timeframe row is stamped with its start.
- **The pattern to copy.** `build_channels` writes each bar's result on row `bar + 1`
  (`strategy/utils/sr_channels.py:52-72`), and `choch_sr` indexes high-timeframe rows by
  `df.index.floor(rule)` (`strategy/choch_sr.py:194-200`). The README records that this matches Pine's
  `[1]` with `lookahead_on` ("Differences From Pine", "High-timeframe bars").
- **The test.** Truncate the data at a random bar inside a high-timeframe bar. The bias of every earlier
  low-timeframe bar must not change. This is the generic cut test `pa_lab_comparison.md` lists as missing.
- **Warm-up.** A 200-bar moving average on 15m bars needs about two CME market sessions of history before
  its first value. It adds to the strategy's warm-up (`backtest_warmup.md`).
- **Rolls.** `choch_sr` already clears context at each roll through `contract_segments`. A bias computed per
  segment would restart there too. Across a roll, a moving average sees the price jump between contracts.

### What to report side by side

The existing table already holds most of it once `bias=false,true` is swept:

- **Already there:** Trades, Win Rate, Expectancy R, Return, Max DD, Sharpe Ratio, Return / DD, and in split
  mode the OOS columns, `Tried` and `OOS profitable`.
- **Tag every signal.** An extra signal column such as `bias` (`long`, `short` or `flat`) is copied onto
  the trade dict when not NA (`tester_framework/backtest.py:213-215`), and those trades reach
  `calculate_metrics`. It is emitted with the filter off too.
- **Split the trades.** `calculate_metrics` can then return Expectancy R and trade counts for the
  with-bias and against-bias trades of the same variant. `choch_sr` already does this for
  `touch_to_choch_minutes` buckets (`strategy/choch_sr.py:377-382`). `trade_stats(trades, label)` in
  `tester_framework/analytics.py:50` already computes Group stats for a list of trades. This shows whether
  the trades the filter would remove are losers, without the knock-on effect of the one-open-trade rule.
- **Optional new metric.** Profit factor, if it is wanted; nothing in the repo computes it today.

### The cheap statistical check

- **The permutation.** Inside `calculate_metrics` of the off variant, draw 10,000 random subsets of the
  trades, each the size of the with-bias set, and report the share whose mean R is at least the with-bias
  mean. That is 10,000 means over at most a few hundred trades per variant, so it is cheap (not timed
  here). Use a fixed seed so the smoke tests stay deterministic.
- **Optionally a confidence interval.** Add the bootstrap 95% interval of the difference in mean R,
  resampled by market session.
- **How it relates to ADR 0003.** This is not the matched random-trade baseline that ADR 0003 deferred. That
  baseline asks whether a variant's trades beat random trades. This check asks only whether the bias
  subset beats random subsets of the same variant's own trades. It needs no new data or simulation.
- **What to leave out.** Skip DSR, SPA and PBO for one flag. Instead:
  - Run the flag in split mode, choose on or off in sample, and read the OOS columns.
  - Remember that `Tried` doubles when the flag is swept.
  - DSR becomes worth adding once bias methods and lengths are swept too. Its N is the number of
    independent trials. `Tried` also counts RR and exit-mode variants, which are strongly correlated, so it
    is an upper bound on N, not N itself.

### Pine and docs

- **Default off.** No Pine reference has a bias filter, so the flag defaults off and gets a "Differences
  From Pine" entry, like `stop_source=last_swing`.
- **Glossary term.** "Bias" collides with "lookahead bias" and "selection bias". Propose a `GLOSSARY.md`
  term such as **Direction filter** or **Trend filter**, defined on the high timeframe, before code uses the
  word.

## Open questions

- **Which timeframe?** "The configured high timeframe" gives a real higher-timeframe bias only for
  `choch_sr`. For ORB it is the opening-range duration. For `ema50` it is the bar size it trades on. The
  options:
  - **High timeframe only:** limit the flag to strategies with a `LOW_TIMEFRAME`.
  - **A separate bias timeframe:** add a strategy parameter such as `bias_timeframe`. `TIMEFRAMES` stops at
    1h and 1d, and a daily bias by market session needs the asset's session settings, which strategies do
    not receive today.
- **Which method?** Close versus an SMA/EMA is the defensible default, though its evidence is on daily and
  monthly data, not intraday bars. Candle direction and market structure
  are cheap alternatives. Each extra method is another trial.
- **What does a flat bias do?** With a `0` bias, for example a close exactly on the average or no value
  during warm-up, should a signal be allowed or blocked?
- **Should the cut test be generic?** The lookahead test for the bias could become the generic cut test
  that `pa_lab_comparison.md` recommends for every strategy.

## Sources

- TradingView Pine docs: other timeframes and data https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/ ; repainting https://www.tradingview.com/pine-script-docs/concepts/repainting/ ; inputs https://www.tradingview.com/pine-script-docs/concepts/inputs/ ; VWAP help https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/
- Backtrader `b853d7c`: https://github.com/mementum/backtrader/tree/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader (`resamplerfilter.py`, `cerebro.py`); docs https://www.backtrader.com/docu/data-resampling/data-resampling/ , https://www.backtrader.com/docu/data-replay/data-replay/ , https://www.backtrader.com/docu/concepts/
- LEAN `32a302a`: https://github.com/QuantConnect/Lean/tree/32a302a9a0fde64c0435798c0c4cb2c2309873ed (`Common/Data/Consolidators/PeriodCountConsolidatorBase.cs`); docs https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/time-modeling/periods , https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/parameters
- vectorbt: https://vectorbt.dev/api/generic/accessors/ ; vectorbt PRO feature page https://vectorbt.pro/features/data/multi-timeframe-analysis/
- White, H. (2000). A reality check for data snooping. *Econometrica* 68(5), 1097-1126. https://doi.org/10.1111/1468-0262.00152
- Hansen, P. R. (2005). A test for superior predictive ability. *JBES* 23(4), 365-380. https://doi.org/10.1198/073500105000000063
- Bailey, D. H. & López de Prado, M. (2014). The deflated Sharpe ratio. *J. Portfolio Management*. https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- Bailey, D. H., Borwein, J. M., López de Prado, M. & Zhu, Q. J. The probability of backtest overfitting (revised Feb 2015). https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
- Harvey, C. R. & Liu, Y. (2015). Backtesting. *J. Portfolio Management*, Fall 2015. https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF
- Brock, W., Lakonishok, J. & LeBaron, B. (1992). *J. Finance* 47(5). https://doi.org/10.1111/j.1540-6261.1992.tb04681.x
- Sullivan, R., Timmermann, A. & White, H. (1999). *J. Finance* 54(5). https://doi.org/10.1111/0022-1082.00163
- Moskowitz, T., Ooi, Y. H. & Pedersen, L. H. (2012). Time series momentum. *JFE* 104(2), 228-250. https://econpapers.repec.org/RePEc:eee:jfinec:v:104:y:2012:i:2:p:228-250
- White (2000) PDF: https://users.ssc.wisc.edu/~bhansen/718/White2000.pdf
- Citation only: Efron (1979) https://doi.org/10.1214/aos/1176344552 ; Politis & Romano (1994) https://doi.org/10.1080/01621459.1994.10476870 ; Brown, Goetzmann, Ibbotson & Ross (1992) https://doi.org/10.1093/rfs/5.4.553
- Repo: `tester_framework/data.py`, `tester_framework/backtest.py`, `tester_framework/analytics.py`, `tester_framework/split.py`, `strategy/choch_sr.py`, `strategy/utils/sr_channels.py`, `strategy/ema50.py`, `pine_scripts_examples/choch_sr_mtf_strategy.pine`, `docs/adr/0003-deferred-research-controls.md`, `docs/research/pa_lab_comparison.md`, `docs/research/backtest_warmup.md`, README "Execution Model", "Strategy API", "Strategy parameters", "Differences From Pine"
