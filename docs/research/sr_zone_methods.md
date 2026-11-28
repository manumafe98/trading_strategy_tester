# Support/resistance channel methods for `choch_sr`

Input for porting `pine_scripts_examples/choch_sr_mtf_strategy.pine`. Questions: which algorithmic methods
find support/resistance channels, which are common, what the evidence says, and which fit "price touches a
higher-timeframe channel, then a contrary CHoCH on the lower timeframe triggers entry". Pine claims come from
the scripts in `pine_scripts_examples/`. Paper claims come from the papers themselves unless marked
"abstract only" or "secondary". "Unverified" means I could not trace the claim to a primary source.

The glossary defines a **support/resistance channel** as a band built from pivots. Several methods below
are not built from pivots. Below, "channel" means any band the strategy could test for touches, and the
glossary entry would need widening if a non-pivot method is added.

## Summary

- **The Pine method** is greedy pivot clustering. Pivot highs and lows (`ta.pivothigh`/`ta.pivotlow`,
  period 10) from the last 290 signal-timeframe bars go into one array. Each pivot seeds a band. Strength is
  20 per pivot in the band plus the number of bars whose high or low lies inside it. The strongest bands are
  kept greedily, up to 10, and the strategy reads the first 6.
- **The repo's Pine is not the published original.** LonesomeTheBlue's script caps the width at 5% of the
  300-bar high-low range. The repo version caps it at 0.4% of price and adds a 0.1% minimum width.
- **Evidence for bounces exists but is small.** Prices stop at support/resistance more often than chance:
  60.8% vs 56.2% for arbitrary levels in [Osler 2000](https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html),
  and conditional bounce probability rises with prior bounces in
  [Garzarelli et al. 2014](https://doi.org/10.1038/srep04487) and
  [Chung & Bellotti 2021](https://arxiv.org/abs/2101.07410). None of the three tests a trading rule after
  costs.
- **Strength scores have weak support.** Firms' own strength rankings did not predict bounces (Osler
  2000). Prior-bounce counts did (Garzarelli; Chung & Bellotti), but the Pine's touch count is not a bounce
  count.
- **Breakout rules failed out of sample.** Of Brock, Lakonishok & LeBaron's rules and their extended
  universe, no rule (trading-range break included) was significant in 1987-1996
  ([Sullivan, Timmermann & White 1999](https://doi.org/10.1111/0022-1082.00163)). That is evidence about
  breaking through a level, not bouncing off it.
- **No peer-reviewed evidence found** for volume profile, order blocks, fair value gaps as channels,
  k-means/DBSCAN/KDE channels, Camarilla, or VWAP bands.
- **Recommendation.** Keep pivot clustering as the default for Pine parity; it is a reasonable
  local-extremum method. Its main costs are lag (a pivot is usable 10 signal bars after its bar closes,
  2h30m at 15m), widths that ignore volatility, and an ad hoc score. If a pluggable channel method is added later,
  candidates in order: (1) prior market session high/low, (2) KDE over the same pivots, (3) market session
  volume profile for futures only.

## Methods

Lag is measured at the strategy's read point, the last closed signal-timeframe bar. "Repaint" means a
value that changes after the bar it is stamped on has closed. Cost is the effort for a pandas port.

| Method | How it is computed | Main parameters | Volume | Lag / repaint risk | pandas cost |
|---|---|---|---|---|---|
| Pivot clustering (LonesomeTheBlue, repo variant) | Confirmed pivots in a loopback are grouped greedily into bands under a width cap, scored by pivots and touches, and the top non-overlapping bands kept (details below) | period, loopback, min/max width, max channels, min strength | No | Pivot known `period` bars late; no repaint once confirmed; channels change at every new pivot | Medium: nested loops over the pivots in the loopback (pivots of one kind are over 10 bars apart, so roughly 50 at most), recomputed only on pivot bars |
| Rolling extremes ([Chung & Bellotti](https://arxiv.org/abs/2101.07410); BLL trading range) | Support = window min ± γ, resistance = window max ± γ; γ = mean absolute price change | window, γ | No | None at the close | Trivial (`rolling().min/max`) |
| Price histogram / KDE | Density of closes, or of pivot prices, over a window; peaks are channels, width from bandwidth ([scikit-learn KernelDensity](https://scikit-learn.org/stable/modules/density.html)) | window, bandwidth, peak prominence | Optional (weights) | Price KDE: none; pivot KDE: same as pivots | Low-medium (`scipy.stats.gaussian_kde`, `find_peaks`) |
| k-means / DBSCAN / agglomerative on pivots | Cluster 1-D pivot prices ([scikit-learn clustering](https://scikit-learn.org/stable/modules/clustering.html)); cluster min/max is the band | k or eps/min_samples, window | No | Same as pivots; k-means labels jump between runs | Low with scikit-learn |
| Volume profile: POC, value area, HVN/LVN | Volume binned by price over a market session or range. POC = row with most volume; value area grows from the POC until 70% of volume ([TradingView help](https://www.tradingview.com/support/solutions/43000502040-volume-profile)) | rows/bin size, VA %, anchor | **Yes**; TradingView uses tick volume for forex | Developing POC/VA change during the session; a completed market session's profile is fixed | Medium; spreading each bar's volume across its range is an approximation |
| Supply/demand zones, order blocks (ICT/SMC) | Practitioner convention: the last opposite candle (or base) before an impulsive move that breaks structure, removed when "mitigated". [LuxAlgo SMC](https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-LUX/) does not publish a definition on its page. **Unverified** | impulse size, base length, mitigation rule | Optional | Only known after the impulse or break, so stamping it on the base candle is lookahead | Medium |
| Fair value gaps as channels | Glossary definition: 3-bar gap between bar 1 and bar 3; the gap is the channel (see `orb_fvg.pine`) | min gap size, fill rule | No | Known at bar 3's close | Low |
| Prior day / week / market session high-low | Previous period's high and low | period, band width | No | None for completed periods | Trivial (resample per market session) |
| Opening range | High/low of the first bars of a session window (ORB in the glossary) | range length, session window | No | None after the range closes | Trivial; exists in `orb_*` |
| Round numbers | Prices ending in 00/50 (or pip/point equivalents) | step, band width | No | None | Trivial |
| Floor / Fibonacci / Camarilla pivots | From the prior period's H, L, C: Traditional P = (H+L+C)/3, R1 = 2P−L, S1 = 2P−H...; Fibonacci P ± 0.382/0.618/1.0·(H−L); Camarilla C ± 1.1·(H−L)/12, /6, /4, /2 ([TradingView help](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/)) | type, period | No | None (prior period) | Trivial |
| VWAP bands | Cumulative(typical price × volume) / cumulative volume since the anchor; bands are standard deviation or percentage multiples ([TradingView help](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/)) | anchor, multipliers | **Yes** | Moves every bar (a line, not a fixed channel) | Low |

Point methods (round numbers, floor pivots, prior high/low, POC) give a single price. To use one as a
channel it needs a band, for example ± k·ATR or ± ticks, which adds one parameter.

### The Pine method in detail

Both `support_and_resistance.pine` and `f_sr_state` in the strategy work as follows:

- **Pivots.** `ta.pivothigh/pivotlow(src, 10, 10)` on High/Low (or max/min of open and close). Highs and
  lows go into **one** array; pivots older than `loopback` (290) bars are dropped.
- **Recompute only on pivot bars.** Between pivots the channels stay as they are.
- **Band building.** Each pivot seeds a band, and the code walks every pivot newest-first, widening the
  band while it stays within `maxWidth`. In % mode the cap is `min(lo, pivot) × 0.4%`. Each accepted pivot
  adds 20, including the seed. The result depends on walk order (greedy, not optimal).
- **Touch strength.** The code adds the number of bars in `0..loopback` whose high **or** low lies inside
  the band. A bar that spans the whole band is not counted, and a bar that breaks through counts the same
  as a bounce.
- **Minimum width.** Bands narrower than `lo × 0.1%` are rejected. At the default Minimum Strength (1,
  threshold 20) a single pivot already passes, so this width filter is what in practice removes
  single-pivot bands. The tooltip's "at least 2 pivots" is wrong for the strength input itself.
- **Selection.** Candidates are taken in descending strength. A candidate is dropped if its top or bottom
  lies inside an accepted channel, so one that fully **encloses** an accepted channel survives and
  overlaps it. At most 10 are kept, and the strategy reads `srMaxChannels` (6).
- **Timing.** The pivot is confirmed at the close of the 10th bar after it (the script itself draws it
  with `offset = -prd` / `time[pivotPeriod]`). The strategy reads the last closed 15m bar's channels
  (`[1]`-shifted inside `request.security(..., lookahead_on)`), so the pivot is usable from that close:
  2h30m after the pivot bar closes. No extra bar is added.
  Role (support/resistance) is set against the last closed 15m close.

The [original script](https://www.tradingview.com/script/Ej53t8Wv-Support-Resistance-Channels/)
(published 8 Apr 2021, Pine v6 since July 2025) describes the width as a percentage of the highest-lowest
range over 300 bars. A mirror of its source
([cnblogs, secondary](https://www.cnblogs.com/lsgxeva/p/19063136)) shows `cwidth = (highest(300) −
lowest(300)) × ChannelW / 100` with `ChannelW` = 5 and no minimum width. The repo's %-of-price and pip
widths are a local change; a port should match the repo Pine, and the README's "Differences From Pine"
should say which one is the reference.

## Most common in practice

- **TradingView built-ins:**
  - Pivot Points Standard (Traditional, Fibonacci, Woodie, Classic, DM, Camarilla)
    ([help](https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/))
  - Volume profile family: session, visible range, fixed range, periodic, auto anchored
    ([help](https://www.tradingview.com/support/solutions/43000502040-volume-profile))
  - VWAP and auto-anchored VWAP with bands
    ([help](https://www.tradingview.com/support/solutions/43000652199-vwap-auto-anchored/))
  - "Pivot Points High Low" is widely described as a built-in, but I did not find its help page.
    **Unverified**.
- **Community scripts.** Pivot-cluster channels (LonesomeTheBlue) and SMC suites with order blocks,
  fair value gaps, BOS/CHoCH and previous highs/lows (LuxAlgo) are among the visible open-source scripts.
  The pages show engagement counts, but the labels did not survive the fetch, so I make **no ranking claim**.
- **Professional practice.** Most levels published by FX firms ended in round numbers: over 70% ended in 0,
  and 96% in 0 or 5 ([Osler 2000](https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html)).
- **Quant literature.** It uses rolling extremes or local extrema: BLL trading range,
  [Garzarelli](https://doi.org/10.1038/srep04487), [Chung & Bellotti](https://arxiv.org/abs/2101.07410).
  None of these papers uses pivot clustering or volume profile.

## Evidence

**[Osler (2000)](https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html), FRBNY *Economic Policy
Review* 6(2)** ([PDF](https://resources.newyorkfed.org/medialibrary/media/research/epr/00v06n2/0007osle.pdf)).

- **Data.** Levels published daily by six firms for USD/DEM, USD/JPY and USD/GBP, January 1996 to March
  1998, tested on 1-minute quotes from 9:00 to 16:00 New York time.
- **Hit and bounce.** A hit is a cross into the level plus 0.01%. A bounce means the rate is still on the
  original side 15 minutes later.
- **Null.** 10,000 sets of arbitrary levels.
- **Result.** Bounce frequency was 60.8% vs 56.2% for arbitrary levels. The edge was 4.0-5.6 points per
  currency, and the results were significant at 5% for all but three of sixteen firm-currency pairs.
  Predictive power lasted at least five business days.
- **Strength rankings.** The firms' own strength rankings were not useful: the significant differences
  were negative.
- **Limits.** These are human-published levels, not an algorithm. A bounce is a 15-minute direction test,
  not a trade, and the paper reports no trading returns.

**[Osler (2003)](https://doi.org/10.1111/1540-6261.00588), *Journal of Finance* 58(5).** Only the
precursor [Staff Report 125](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.pdf)
was read.

- **Data.** Stop-loss and take-profit orders at one large FX dealing bank.
- **Round numbers.** The orders' requested prices cluster strongly at round numbers.
- **Mechanism (SR 125 body).** Take-profit orders cluster more strongly at round numbers, which explains
  reversals there. Stop-loss buys cluster just above and stop-loss sells just below round numbers, which
  explains acceleration once a level is crossed.

**[Kavajecz & Odders-White (2004)](https://doi.org/10.1093/rfs/hhg057), *RFS* 17(4).** Abstract only.
Support/resistance levels coincide with peaks in limit-order-book depth. The authors read this as
technical rules locating liquidity that is already there.

**[Garzarelli et al. (2014)](https://doi.org/10.1038/srep04487), *Scientific Reports* 4:4487.**

- **Data.** Tick data for 9 London Stock Exchange stocks in 2002.
- **Result.** Candidate levels are local maxima/minima of prices sampled at 45-180 second time scales.
  The probability of a bounce given the number of prior bounces rises with that number. In
  shuffled-return series it stays near ½.

**[Chung & Bellotti (2021)](https://arxiv.org/abs/2101.07410).** An arXiv preprint; I found no
peer-reviewed version.

- **Data.** 1-minute EURUSD, Lloyds and Brent, 2018.
- **Levels.** Rolling-window min/max ± γ, where γ is the mean absolute price change.
- **Result.** More prior bounces meant a higher bounce probability than in 1,000 shuffled series, and the
  effect decays over time. Their LLOY results above 5 prior bounces were too thin to conclude.
- **Limits.** Only direction and significance are reported (results are charts), and the paper tests no
  trading rule.

**[Brock, Lakonishok & LeBaron (1992)](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x) and
[Sullivan, Timmermann & White (1999)](https://doi.org/10.1111/0022-1082.00163).**

- **BLL.** Read only through STW's description and the abstract. The trading range break **buys a close
  above the n-day high** and sells below the n-day low. Over DJIA 1897-1986, buy signals beat sell signals.
- **STW.** In-sample the best rule survives data-snooping correction. Out of sample (1987-1996, and S&P 500
  futures) no rule is significant: the best rule's probability of not beating the benchmark is about
  12%.
- **Relevance.** This is evidence on breakouts, and it is weak out of sample.

**Volume profile / market profile.** The searches found only vendor and practitioner material. Figures
quoted in trading guides could not be traced to a paper and are not repeated here.

**Order blocks, fair value gaps, ML clustering.** I found no peer-reviewed tests. Internal and **not
peer-reviewed**: the price action lab replica (`docs/research/pa_lab_comparison.md`) found levels, fair
value gaps and zones beat matched random entries "by a few points" on NQ, out of 19 claims tested; in
the same run, sweeps and break-of-structure follow-through **failed**, which bears on the CHoCH trigger.

**Together:** levels where orders rest stop price slightly more often than chance (a few points of
15-minute direction); prior bounces help, declared strength does not; nothing here is shown to survive
costs, so the CHoCH trigger, stop and target must supply the rest.

## Fit with `choch_sr`

**What the strategy needs from a channel method:**

- bands with a top and bottom
- known at the last closed signal bar
- a role set against the reference close
- stable enough to be followed by midpoint while a touch is pending

**The four tests.** Each method is judged on: no confirmation lag, no need for real volume (EURUSD and
GBPUSD), few free parameters, and some evidence.

**Is pivot clustering a reasonable representation?** Yes, as the parity default. It finds local
extrema, the same raw material the bounce studies use. It also passes the volume test, which matters for
forex. Its weaknesses:

1. **Lag.** A swing enters the channel set 10 signal bars after its bar closes (2h30m at 15m). Channels built from the
   swing that just happened are missing exactly when the price retests it.
2. **Fixed % width.** 0.1-0.4% of price ignores volatility: the same band is wide for EURUSD on a quiet
   day and narrow for MNQ on a news day. An ATR-scaled width would be the boring fix. The original's
   %-of-range cap was closer to this.
3. **Greedy, order-dependent clustering.** Mixed highs and lows and a loose overlap rule (enclosing bands
   survive) mean small parameter changes reshuffle the channels. This is why the strategy follows pending
   touches by midpoint.
4. **Scoring.** The 20-per-pivot weight is arbitrary. Touches count breaks the same as bounces, and old
   touches (up to 290 bars, about 3 days at 15m) count the same as new ones. The evidence favours
   prior-bounce counts with decay.
5. **Repaint.** None once confirmed, given the `[1]` shift and `lookahead_on`. The port must keep the
   one-bar shift and must not mutate the shared `df`.

**Alternatives worth a later pluggable channel method:**

1. **Prior market session high/low.** It has zero lag, needs no volume, and has one parameter (band
   width). It works on every asset and the engine already has market sessions. Optionally add
   round-number bands, the only method backed by order data (Osler 2000/2003). It is the cleanest null
   for asking whether pivots add anything.
2. **KDE over the same pivots.** It replaces the greedy walk with density peaks: bandwidth in ATR, peak
   height as strength, optional exponential age decay. It has the same lag and volume needs as today but
   is order-independent and has fewer knobs. It is a direct test of the scoring weakness.
3. **Market session volume profile (POC / value area edges / HVN), futures only.** It uses real volume on
   MGC, MNQ and MES, and a completed market session's profile is fixed. It has no academic evidence. On forex it
   would run on tick volume or nothing, so it should refuse or warn. BTCUSD depends on the data source.

**Not recommended as channel methods here:**

- **Order blocks and supply/demand zones.** No agreed definition, lookahead-prone stamping, no evidence.
- **VWAP bands.** A moving line, not a fixed channel, and volume-dependent.
- **Floor/Camarilla pivots.** Fine but arbitrary multipliers; prior market session high/low covers the same
  idea with fewer assumptions.
- **k-means.** Requires choosing k and its labels are unstable.

## Open questions

- **Which Pine is the reference?** The repo variant with %-of-price widths, or the original with
  %-of-300-bar-range widths? Record the choice in "Differences From Pine".
- **Is there an edge over random channels?** Any channel method should be compared against random
  channels with the same width and count, like Osler's 10,000 arbitrary sets. Without that, the CHoCH
  trigger cannot be credited or blamed.
- **Should touches be scored as bounces?** That would mean counting only bars that enter a band and leave
  on the same side, with decay, rather than every bar whose high or low is inside.
- **Channels across contract rolls?** Unadjusted futures pivots straddle rolls; ADR 0002 covers exits,
  not channels built across a roll.

## Sources

- Osler, C. (2000). Support for resistance. *FRBNY Economic Policy Review* 6(2). https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html
- Osler, C. (2003). Currency orders and exchange rate dynamics. *J. Finance* 58(5). https://doi.org/10.1111/1540-6261.00588; precursor SR 125: https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr125.pdf
- Kavajecz, K. & Odders-White, E. (2004). Technical analysis and liquidity provision. *RFS* 17(4). https://doi.org/10.1093/rfs/hhg057 (abstract only)
- Garzarelli, F. et al. (2014). Memory effects in stock price dynamics. *Sci. Rep.* 4:4487. https://doi.org/10.1038/srep04487
- Chung, K. & Bellotti, A. (2021). Evidence and behaviour of support and resistance levels in financial time series. arXiv:2101.07410. https://arxiv.org/abs/2101.07410
- Brock, W., Lakonishok, J. & LeBaron, B. (1992). *J. Finance* 47(5). https://doi.org/10.1111/j.1540-6261.1992.tb04681.x (via abstract and STW)
- Sullivan, R., Timmermann, A. & White, H. (1999). Data-snooping, technical trading rule performance, and the bootstrap. *J. Finance* 54(5). https://doi.org/10.1111/0022-1082.00163
- TradingView help: Pivot Points Standard https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/ ; Volume profile https://www.tradingview.com/support/solutions/43000502040-volume-profile ; VWAP https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/ ; VWAP Auto Anchored https://www.tradingview.com/support/solutions/43000652199-vwap-auto-anchored/
- LonesomeTheBlue, Support Resistance Channels: https://www.tradingview.com/script/Ej53t8Wv-Support-Resistance-Channels/ ; source mirror (secondary): https://www.cnblogs.com/lsgxeva/p/19063136
- LuxAlgo, Smart Money Concepts: https://www.tradingview.com/script/CnB3fSph-Smart-Money-Concepts-LUX/
- scikit-learn: density estimation https://scikit-learn.org/stable/modules/density.html ; clustering https://scikit-learn.org/stable/modules/clustering.html
- Repo: `pine_scripts_examples/indicators/support_and_resistance.pine`, `pine_scripts_examples/choch_sr_mtf_strategy.pine`, `GLOSSARY.md`, `docs/research/pa_lab_comparison.md`
