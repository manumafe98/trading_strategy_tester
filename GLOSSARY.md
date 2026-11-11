# Trading Strategy Tester

Vocabulary for backtesting strategies ported from TradingView reference scripts: the market structure they read, the strategies themselves, and the engine that simulates their trades.

## Market structure

**CHoCH (Change of Character)**:
The first break of an active swing in the direction opposite to the previous break. It describes a change in the direction of breaks; on its own it does not confirm a trend reversal.

**Support/resistance channel**:
A price band built from pivots. It is called support when it lies below the reference price, resistance when it lies above it, and an in-price channel when the price is inside the band.
_Avoid_: S/R zone, level

**Fair value gap (FVG)**:
A three-bar price gap where the first bar's high stays below the third bar's low (bullish), or the first bar's low stays above the third bar's high (bearish).
_Avoid_: imbalance, gap

## Strategies

**Opening Range Breakout (ORB)**:
A strategy family that marks the high and low of the opening range, the first signal timeframe of a session window, and trades the first close beyond it.
_Avoid_: ORB as a name for the range itself

## Time

**Market session**:
The configured trading day of an asset, starting at its local session start time; daily bars and futures contract selection follow it.
_Avoid_: session, trading day

**Session window**:
A named local time range that limits entries for a variant and, for ORB strategies, anchors the opening range.
_Avoid_: session, trading hours

**Signal timeframe**:
The bar size a strategy reads to produce signals, selected per variant.
_Avoid_: timeframe, interval

**Execution timeframe**:
The bar size the engine uses to simulate fills and exits; a strategy may request one different from its signal timeframe.
_Avoid_: timeframe, base timeframe

## Backtest engine

**Signal**:
A strategy's request to enter: a time, a side, and a stop.
_Avoid_: setup, order

**Close entry**:
A signal that fills at a given price on its own bar instead of at the next bar's open.

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

**Gap exit**:
An exit filled at a bar's open because the price gapped through the stop or a target.
_Avoid_: slippage exit

**Discarded signal**:
A signal that never became a trade, because of an invalid stop or entry, R below the asset's minimum risk, an open trade, a calendar filter, or a size below the minimum quantity.
_Avoid_: skipped signal, rejected signal

**Unresolved trade**:
A trade still open when the data ends; it is excluded from results.
_Avoid_: open trade

**Variant**:
One combination of strategy, asset, signal timeframe, RR, exit mode, and session window, backtested independently.
_Avoid_: run, scenario

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
An unadjusted futures price series that uses, for each market session, the contract with the highest volume in the previous market session.
_Avoid_: back-adjusted series, front month
