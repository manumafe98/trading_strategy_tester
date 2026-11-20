# Trading Strategy Tester

Vocabulary for backtesting strategies ported from TradingView reference scripts: the market structure they read, the strategies themselves, and the engine that simulates their trades.

## Market structure

**CHoCH (Change of Character)**:
The first break of an active swing in the direction opposite to the previous break. It describes a change in the direction of breaks; on its own it does not confirm a trend reversal.

**Support/resistance channel**:
A price band built from pivots. It is called support when it lies below the reference price, resistance when it lies above it, and an in-price channel when the price is inside the band.
_Avoid_: S/R zone, level

**Touch**:
A low-timeframe price range overlapping a support/resistance channel: the developing high-timeframe bar's range, assembled from closed low-timeframe bars, reaching into a support or resistance channel. A pending touch keeps the first and latest bar of its current contact; a high-timeframe bar that opens clear of the channel interrupts the contact, and the next contact starts a new one.

**Fair value gap (FVG)**:
A three-bar price gap where the first bar's high stays below the third bar's low (bullish), or the first bar's low stays above the third bar's high (bearish).
_Avoid_: imbalance, gap

## Strategies

**Opening Range Breakout (ORB)**:
A strategy family that marks the high and low of the opening range, the first high-timeframe bar of a session window, and trades the first close beyond it.
_Avoid_: ORB as a name for the range itself

**`choch_sr`**:
A strategy that trades a setup: long after a touch on a support channel, short after a touch on a resistance channel.

**Setup**:
A touch followed by the first CHoCH in the opposite direction: a bullish CHoCH after a support touch, a bearish CHoCH after a resistance touch.

## Time

**Market session**:
The configured trading day of an asset, starting at its local session start time; daily bars and futures contract selection follow it.
_Avoid_: session, trading day

**Session window**:
A named local time range that limits entries for a variant and, for ORB strategies, anchors the opening range; it may cross midnight, and its window day starts at its own start time.
_Avoid_: session, trading hours

**Window day**:
The day a session window instance starts on; a bar after midnight in a window that crosses midnight belongs to the window day before.
_Avoid_: session date, trading day

**Daily break**:
The span around the 17:00 New York close when FX spreads widen until liquidity returns, taken as 16:45-18:15 New York time.
_Avoid_: rollover, spread hours

**High timeframe**:
The bar size a strategy builds its context on, selected per variant.
_Avoid_: signal timeframe, timeframe, interval

**Low timeframe**:
The bar size the strategy reads for its triggers and the engine uses to simulate fills and exits; it equals the high timeframe unless the strategy declares `LOW_TIMEFRAME`.
_Avoid_: execution timeframe, timeframe, base timeframe

**Cutoff**:
The first bar of the out-of-sample period, always the first bar of a market session; it is set per asset and low timeframe by `--oos_start` or `--oos_fraction`.
_Avoid_: split date, holdout start

**In-sample period**:
The bars of the loaded time period before the cutoff; variants are judged on it.
_Avoid_: training period, training set, IS data

**Out-of-sample period**:
The bars from the cutoff to the end of the loaded time period, kept back to check whether in-sample results hold.
_Avoid_: test period, test set, holdout, forward period

**Spanning trade**:
A trade that enters in the in-sample period and exits in the out-of-sample period; it counts as in-sample, while its P&L lands in the out-of-sample equity curve.
_Avoid_: straddling trade, crossover trade

## Backtest engine

**Signal**:
A strategy's request to enter: a time, a side, and a stop.
_Avoid_: order

**Close entry**:
A signal that fills at a given price on its own bar instead of at the next bar's open.

**Stop entry**:
A signal that fills when price reaches a given level during its own bar, or at the open if the bar opens beyond it.
_Avoid_: break entry, intrabar entry

**Setup time**:
The start of the latest bar a signal's setup depends on; a signal whose setup time is before the first high-timeframe bar after the previous trade's exit is discarded.

**Trade**:
A filled signal, from entry to its final exit.
_Avoid_: position, deal

**R**:
The entry-to-stop price distance of a trade, used as the unit for targets and results.
_Avoid_: risk unit

**RR (risk/reward ratio)**:
The final target distance expressed as a multiple of R.

**Exit mode**:
How the engine takes profit: fixed (one target), trailing (a stop that follows the favorable extreme), or partial (equal tranches at whole-R targets and the final target).

**Session exit**:
An exit rule that closes an open trade at the end of its variant's session window.
_Avoid_: EOD exit, end-of-day exit

**Roll**:
The first bar of a market session where the chosen contract of a continuous series changes.
_Avoid_: rollover

**Roll exit**:
The forced close of a trade at the close of the last bar before a roll.

**End exit**:
The close of a trade still open on the last bar of the data, at that bar's close.

**Target fill**:
The rule for when a target counts as reached: touch (price reaches the target) or tick-through (price trades at least one tick beyond it, the default).

**Intrabar rule**:
The rule for ordering prices inside a bar: path (TradingView's open-high-low-close order, the default) or stop-first (a bar that can reach both the stop and a target exits at the stop).

**Gap exit**:
An exit filled at a bar's open because the price gapped through the stop or a target; under tick-through, a target gap fills at the target price instead.
_Avoid_: slippage exit

**Discarded signal**:
A signal that never became a trade, because of an invalid stop or entry, R below the asset's minimum risk, an open trade, a close entry on the last bar, a stop entry whose bar never reaches its level, a calendar filter, a fill in the 60 minutes before a roll or across one, a setup time before the first high-timeframe bar after the previous trade's exit, or a size below the minimum quantity.
_Avoid_: skipped signal, rejected signal

**Variant**:
One combination of strategy, asset, high timeframe, low timeframe, RR, exit mode, session window, and strategy parameter values, backtested independently.
_Avoid_: run, scenario

**Strategy parameter**:
A setting a strategy declares with a default and the framework can sweep per variant.

**Selected variant**:
One of the top N variants of a strategy and asset, ranked on an in-sample metric (`--select_by`, `--select_top`) in split mode.
_Avoid_: winner, best variant, optimized variant

**Walk-forward efficiency (WFE)**:
A variant's out-of-sample annualized return as a percentage of its in-sample annualized return; blank when the in-sample annualized return is zero or negative.
_Avoid_: efficiency ratio, robustness score

**Daily Sharpe**:
The annualized mean over standard deviation of per-market-session equity returns, counting market sessions without trades.

**Result bundle**:
The output folder of one run, holding the results page, variant pages, and trade charts.
_Avoid_: report

**Gross / Net**:
A value before and after trading costs (spread, slippage, commission).

**MFE R**:
The peak favorable value a trade reached, in R, including banked partial fills.
_Avoid_: max run-up

**Giveback R**:
The part of a trade's MFE R not kept at exit.

**Continuous series**:
An unadjusted futures price series that uses, for each market session, the contract with the highest volume in the previous market session. Roll exits keep the price difference between contracts out of trades; indicators still see it.
_Avoid_: back-adjusted series, front month
