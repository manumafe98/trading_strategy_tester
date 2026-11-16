# Price action lab vs trading_strategy_tester

Input for a `/grill-with-docs` session. The lab is the five-prompt research lab from the YouTube video,
rebuilt at `E:\repos\pa_lab_replica` (throwaway; phases 3 and 4 ran at a reduced 400 versions). Lab
claims below come from its phase reports; our claims come from `tester_framework/` and `README.md`.

## The one-line difference

Our tester answers "how did this strategy do?". The lab answers "is this idea better than chance,
and would I have been fooled?". The lab is a research protocol wrapped around a stricter engine; we are
a flexible single-strategy engine with a light out-of-sample split.

## Engine (fills, costs, data)

| Topic | Lab | Ours | Who is more realistic |
|---|---|---|---|
| Fill resolution | Always 1m bars, whatever the strategy chart | The execution timeframe (1h for `ema50`; 1m only for ORB) | Lab |
| Ambiguous bar (stop and target both inside) | Stop wins | TradingView path heuristic (open nearer high: O-H-L-C) | Lab is conservative; ours matches TradingView |
| Targets | Need a 1-tick trade-through | Fill on touch (`target_between`: `target <= end`) | Lab. Ours is optimistic, most so on round targets and range strategies |
| Limit entries | Supported; fill needs a tick through, never better on a gap, window and cancel | Not supported: next-bar open or close entry only | Lab (feature gap) |
| Stop slippage | 1 tick, or open minus 1 tick on a gap | Spread/2 + slippage on every fill, stops fill at the stop or the gap open | Similar size; lab models gap slippage, ours charges limits and targets too |
| Costs | $4 RT + 1 tick on market entries and stops; none on limits, targets, time and roll exits | Per asset: spread/2 + slippage per side + commission, on every exit | Lab is order-type aware; ours is simpler and slightly harsher on targets |
| Continuous futures | Volume roll, **back-adjusted**, rolls file | Volume roll per market session, **unadjusted** | Lab for indicators and P&L across rolls |
| Trades across a roll | Forced exit at the last bar before a roll, no entries in the last 60 min | **Not handled.** A trade open at the switch sees the contract spread as a price gap | Lab. On MNQ discovery the mean roll spread is 77 pts (max 273); on MES 22 pts (max 68). That is a fake gap exit or a fake win, and a jump in every indicator (EMA50) once a quarter |
| Holidays / early closes | Dropped sessions treated like rolls (final test only) | Carried through | Lab |
| Sizing | 1 contract fixed (half on news) | Fixed-fractional, compounding, `min_risk_ticks` floor | Different purposes: ours is closer to live; lab is cleaner for comparing versions |
| Sharpe | Daily P&L incl. zero days, x sqrt(252) | Bar-level equity returns x sqrt(bars_per_year) | Lab's is the industry convention and comparable across timeframes; bar-level Sharpe on mostly flat bars is not |
| Open trades at end | Closed by time, roll or session rules | Dropped as `Unresolved` | Lab; dropping can hide a large open loser |
| Speed | 0.26-0.63 s per version on 1m data, numba, 6 processes | Per-signal Python loop, parallel per variant | Lab, by design (100k versions) |

## Research protocol (where the lab is in another league)

| Control | Lab | Ours |
|---|---|---|
| Look-ahead proof | Every detector and setup tested: run cut at a random bar must equal the full run up to the cut | Only per-strategy unit tests; no generic cut test |
| Higher-timeframe use | A higher bar is usable only from its close; ATR excludes the current bar; swings known k bars late | Left to each strategy |
| Out-of-sample | Sealed window, guarded reader, env-var unlock, access log, run once, hashes checked before unlock, rehearsal on shifted prices | `--oos_start` / `--oos_fraction` split computed in the same run; nothing stops repeat peeking |
| Decisions | `decisions.md`, numbered amendments written before results, frozen funnel spec, one master seed | None by design |
| Null model | Per-trade matched random twins (same side, time-of-day slot, stop/target in ATR, holding time, costs), 10,000 sets, p-value | None |
| Replication | Second market (ES) and two eras must agree | Multi-asset runs exist but nothing requires agreement |
| Multiple testing | Counts what passed against what was tried; judges the setup as the equal-weight average of all its versions | Ranks variants and marks the top N `Selected`, which is exactly the selection bias the lab guards against; `WFE` partly measures the decay |
| Confidence | 2,000-resample session bootstrap, 95% intervals | None |
| Sanity checks | 20,000 random entries must lose about the costs; a 60-minute peek cheat must win | None |
| Reporting | Every number in the dashboard traced to a file and re-read by a second reader | Report prints from memory |

## What the lab found that matters to us

- Random entries lost $15.24/trade on NQ (video: $9.80): costs alone are $12.47. Any 1m strategy starts
  that far behind.
- Only 3 of 19 price action claims beat chance, all by a few points: levels, fair value gaps, zones.
  Trend lines did worse than random lines; sweeps and break-of-structure follow-through failed.
  FVG holds is relevant to `orb_fvg` / `orb_combined`.
- The one version that passed every discovery gate (+$127k, Sharpe 0.84, beat its twins, held on ES)
  lost $123k on NQ in the sealed window. Passing filters on one window predicted nothing; this is what our
  `Selected` + OOS mode can show but does not protect against.

## Where we are better or simply different

- Strategy contract (`generate_signals` + optional hooks) is far easier to extend than the lab's dial
  grid; the lab cannot express a strategy outside its rulebook.
- Exit modes (trailing, partial), session windows, `--session_exit`, calendar filters, trade HTML charts,
  MFE/giveback, Pine parity tests and mutation tests. The lab has none of these.
- Our intrabar heuristic matches TradingView, which is what we port from; the lab's stop-first rule will
  not reconcile with TradingView results.
- Compounding risk sizing reflects how an account trades; the lab's one-contract P&L does not.

## Candidate improvements (for the grilling, roughly by value / effort)

1. **Roll handling (bug-class).** Either back-adjust the continuous series or force-exit before the
   contract switch and block entries in the last N minutes. Today quarterly rolls inject 22-273 pt
   fake gaps into trades and indicators.
2. **Tick-through targets** (option or default), plus a stop-first option for ambiguous bars, so we can
   bracket results between optimistic (TradingView) and conservative.
3. **Daily Sharpe** with zero-filled days alongside or instead of bar-level Sharpe.
4. **Generic look-ahead test**: for every strategy, signals on data cut at random bars must equal the
   full run up to the cut. Cheap, catches the most dangerous class of bug.
5. **Random-entry and peek-cheat sanity checks** as tests: random entries should lose about the costs;
   a cheat must win.
6. **1m fill resolution** for higher-timeframe strategies (`EXECUTION_TIMEFRAME` exists; making 1m the
   default for futures would remove most path-heuristic error).
7. **Null model for a variant**: matched random twins and a p-value next to Return / Sharpe.
8. **Selection honesty in split mode**: report how many variants were tried, the share that held up
   OOS, and the equal-weight average of all variants, not only the top N.
9. **Sealed holdout workflow**: a holdout date that the CLI refuses to read unless unlocked, with a log.
   Heavier; only worth it if we start large parameter searches.
10. **Limit entries** with tick-through fills, if strategies need them.
11. **Unresolved trades**: mark-to-market or close at the end instead of dropping them.

Things to probably not copy: the dial rulebook and 100k-version funnel (huge compute, weak fit for
hand-written strategies), full-size economics on micro prices, and the provenance-checked dashboard.

## Outcome of the grilling

Adopted, in this order (one GitHub issue each):

1. Look-ahead and sanity-check tests (items 4 and 5).
2. Roll exit (item 1), see ADR 0002.
3. Fill rules: tick-through targets by default, gap-to-target, stop-first option (item 2), see ADR 0001.
4. End exits replacing unresolved trades (item 11).
5. Daily Sharpe (item 3).
6. Honest selection in split mode (item 8).

Deferred, see ADR 0003: the matched random-trade baseline (item 7), the sealed holdout (item 9), limit
entries (item 10) and 1-minute fills for higher signal timeframes (item 6).
