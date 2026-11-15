# Tick-through targets by default

Targets fill only when price trades one tick through them, and a bar that opens beyond a target fills at the target price, never better. This deliberately diverges from TradingView, which fills targets on a touch and at a better open on a gap, because touch fills overstate results, most on round targets and range strategies (see `docs/research/pa_lab_comparison.md`). Touch fills stay available as an option, and the Pine-parity tests pin it, so ported strategies can still be reconciled with TradingView.

## Considered Options

- **Touch by default** (TradingView parity): rejected because the default should be the realistic result, not the reconciliation one.
- **Always stop-first in an ambiguous bar** (the lab's rule): kept as an option, not the default; the TradingView path rule stays the default so the two options together bracket the intrabar guess.
