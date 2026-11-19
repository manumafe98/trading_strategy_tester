# Pine parity check

How to check a Python port of a Pine strategy against TradingView, as done for `choch_sr` in #56. The check is layered: when trades differ, the layer below tells you why.

1. **Channels** (or whatever higher-timeframe context the strategy builds).
2. **Market structure**: the swing, BOS and CHoCH labels.
3. **Trades**: entry time, side, entry price, exit time and exit price.

Differences the check finds are either fixed or recorded in the README's "Differences From Pine", each pinned by a test in `tests/test_pine_differences.py`.

## Time window

TradingView bar replay sets the window. Open the symbol (for #56, `MGC1!` on 1m) and start replay at the end of the window (2026-06-03 02:59 UTC). The strategy then backtests over the 1m history TradingView loaded before that point (from 2026-05-10 22:00 UTC). Slice the local data to the same bars; the scripts below hard-code the #56 window, so edit `WINDOW_START`/`WINDOW_END` and the `.loc` slice for a new one.

## Exclusions

- **Warm-up.** Pivots, channels and swings need history. Ignore differences until both sides have built their first context, and compare structure labels on the tail both sides share.
- **Rolls.** A contract change resets `choch_sr`'s context; TradingView's continuous chart doesn't, so its channels keep old-contract prices for up to 290 high-timeframe bars after a roll. Ignore trades in that span, or pass `single-contract` to `compare_trades.py` to switch the reset off.

## Inputs from TradingView

Set the strategy inputs to the Pine defaults, then pull:

- the trade list (`data_get_trades`) into a JSON file, one object per trade:
  ```json
  {"side": "long", "entry_ms": 1778477580000, "entry": 4684.9, "exit_ms": 1778478180000, "exit": 4675.8}
  ```
- the last 500 structure labels (`data_get_pine_labels`) into a text file, one `TEXT:price` token per label in creation order (`C` = CHoCH, `B` = BOS).

Keep these files and any generated `.pine` outside the repo.

## Scripts

Run from the repo root with the venv Python. They are written for `choch_sr` on MGC; a new port copies and adapts them.

- **Layer 1:** `tools/parity/make_channels_debug_pine.py [OUT.pine]` writes the reference strategy with swing and BOS labels switched off and one invisible label per 15m bar: `time|reference close|top/bottom x6`. Paste it into TradingView on the same replay chart and pull its labels. There is no comparison script: compare the change points of those labels by hand with `build_channels(resample_data(window, "15min"), 0.1, 0.4, "%", pip_size)` on the same bars, without the roll reset.
- **Layer 2:** `tools/parity/compare_labels.py TV_LABELS_TXT` diffs our structure tokens against TradingView's and prints the match ratio and every non-equal run with its bar times.
- **Layer 3:** `tools/parity/compare_trades.py TV_TRADES_JSON [single-contract]` outer-joins our trades with TradingView's on entry time and prints both sides plus the match counts.
