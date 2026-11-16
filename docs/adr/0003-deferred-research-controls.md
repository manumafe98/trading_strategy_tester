# Deferred research controls from the price action lab

The price action lab comparison (`docs/research/pa_lab_comparison.md`) showed four features we chose not to build yet:

- **Matched random-trade baseline per variant**: deferred until the cheaper realism fixes land.
- **Sealed holdout with a logged unlock**: deferred because it only pays off with large parameter searches, which we don't run.
- **Limit entries**: deferred because no current strategy needs them.
- **1-minute fills for higher signal timeframes**: deferred because strategies receive execution-timeframe bars, so a flag would silently change the strategy. Doing it properly means the framework building signal bars from execution bars and filling after each signal bar closes, a strategy contract change with look-ahead risk.

For the last item, running a variant with both the optimistic fill options (touch targets, path rule) and the conservative ones (tick-through targets, stop-first) brackets the intrabar error. A wide gap between the two is the signal to build it.
