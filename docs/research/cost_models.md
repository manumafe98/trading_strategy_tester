# Cost models: established engines vs trading_strategy_tester

Input for a `/grill-with-docs` session about the cost model. Engine claims come from official docs or source at
the pinned commit in Sources; venue numbers come from the venue's own page as fetched on 2026-11-24. "Not found"
means not found in the named file or tree at that commit, not proof of absence. Hummingbot is skipped: it is a
live market-making framework and adds nothing a backtest cost model needs here.

## Our engine today

| Topic | Ours (`config/assets.json`, `tester_framework/backtest.py`) |
|---|---|
| Spread | `spread_points / 2` added against the trade on every fill: entry, target, stop, session, roll, end (`adjusted_entry` / `adjusted_exit`, lines 122-129) |
| Slippage | `slippage_points` on every fill, same lines; fixed price units, limits and targets included |
| Commission | `commission_per_side * qty * 2` per exit fill (line 279), i.e. fixed cash per unit, charged entry + exit |
| PnL | `price diff * qty * point_value` (lines 280-281), `point_value` constant, assumed USD |
| Financing | None |
| Time-varying cost | None; the Daily break (16:45-18:15 New York, `GLOSSARY.md`) is a term but not a cost input |
| Data | 1m OHLCV; FX and BTC CSVs have `timestamp,open,high,low,close,volume`, no bid/ask or spread column; venue and price side (bid, mid, last) unknown |

Round trip from `assets.json` (`spread + 2 * slippage` in points, times `point_value`, plus `2 * commission_per_side`):

| Asset | Points RT | Spread+slip $ RT per unit | Commission $ RT | Total RT | As share of notional |
|---|---|---|---|---|---|
| MNQ | 0.75 (3 ticks) | 1.50 | 2.50 | $4.00 / contract | not computed (price level varies) |
| MES | 0.75 (3 ticks) | 3.75 | 2.50 | $6.25 / contract | not computed |
| MGC | 0.30 (3 ticks) | 3.00 | 2.50 | $5.50 / contract | not computed |
| EURUSD / GBPUSD | 0.0002 (2 pips) | $20 per 100k | 0 | $20 per 100k | ~1.8 bp at 1.10; ~1.5 bp at 1.30 |
| BTCUSD | 7.0 | $7 per BTC | 0 | $7 / BTC | 12.3 bp at $5.7k; 1.1 bp at $65k |

## 1. Per-engine summary

| Engine | Commission | Spread | Slippage | Financing | Currency conversion |
|---|---|---|---|---|---|
| TradingView `strategy()` | `commission_type`: `percent` (default type, of transaction value), `cash_per_contract` (per contract/lot/share/unit), `cash_per_order`; `commission_value` default 0; charged on entries and exits [1][2] | Not modelled; docs say slippage "can be used to account for the spread" [2][3] | `slippage` in ticks, fixed, on market and stop fills only, not limits [1][2]; limits instead get `backtest_fill_limits_assumption` ticks of trade-through [1] | None found in docs [1][4] | `currency` sets account currency; values converted at the previous daily rate of the pair, via a spread-based cross when no direct pair [4] |
| NautilusTrader | `MakerTakerFeeModel(maker_rate, taker_rate, overrides)` (rate of notional), `FixedFeeModel(commission, charge_commission_once)`, `PerContractFeeModel(commission)` [5] | From data: L1 quotes give bid/ask; docs say bars "cannot establish ... spread" [6]; `MarketHoursFillModel` gives a normal or one-tick-wider synthetic spread, but from Python it stays in normal mode (state setters not exposed) [7] | `prob_slippage`: chance of a one-tick adverse move per fill on L1 books; limit fills at the limit never slip; tiered/size/volume-aware synthetic-book fill models [7]; `price_protection_points` caps market and stop-market fills at `ask + points * increment` [8] | `FXRolloverInterestModule`: OECD short-rate differential / 365, at 17:00 New York, Wed and Fri tripled [9]; CFD swap module [10]; perp funding from `FundingRateUpdate` data settled by `SimulatedExchange` at funding boundaries, positive rate debits longs [11][12] | Multi-currency or single-currency accounts; adjustments converted to base at the cached mid rate [10][11] |
| Backtrader | `CommInfoBase`: `COMM_PERC` (`size * commission * price`) or `COMM_FIXED` (`size * commission`); futures-like via `margin` + `mult` [13] | Not found in `comminfo.py` / `bbroker.py` [13][14] | Broker `slip_perc` (precedence) or `slip_fixed`, options `slip_open`, `slip_match` (cap at high/low), `slip_limit`, `slip_out` [14]; `fillers.py` exists for volume-limited size (not read) [15] | `interest` yearly rate on shorts (`interest_long` for longs), `days * price * abs(size) * interest / 365` [13] | No currency param in `CommInfoBase` params [13] |
| vectorbt | `fees` (fraction of order value) and `fixed_fees` (per order), both broadcastable arrays [16] | Not found; a per-bar `slippage` array can stand in | `slippage` as fraction of price, broadcastable per bar/column [16] | Not found in `portfolio/base.py` [16] | Not found [16] |
| QuantConnect LEAN | Per-brokerage fee models, e.g. IB: US futures per-contract table (MES/MNQ/MGC $0.25 IB + $0.30 exchange + $0.02 reg per side = $0.57), FX 0.20 bp with $2 minimum, tiered by monthly volume [17]; Binance spot 0.1%/0.1%, Binance USDT futures 0.02%/0.04% maker/taker [18][19] | From data: market fills use ask for buys and bid for sells from quote ticks or `QuoteBar`, fall back to trade price [20] | `NullSlippageModel` default [21]; `ConstantSlippageModel` (percent), `VolumeShareSlippageModel`, `MarketImpactSlippageModel` (Almgren et al.) [22] | Crypto perp funding via `BinanceFutureMarginInterestRateModel` (rate * position value at funding times) [23]; equity short interest model; no FX swap model found in tree [24] | `CashBook`: each `Cash` has a `ConversionRate` into account currency, fed by an auto-added FX/crypto subscription [25] |
| Zipline (reloaded) | Equities `PerShare` $0.001 default, `PerTrade`, `PerDollar`; futures `PerContract` $0.85 + per-symbol flat `exchange_fee` per trade (ES $1.18) [26][27] | `FixedSlippage(spread)`: buy at `close + spread/2`, sell at `close - spread/2` [28] | Defaults: equities `FixedBasisPointsSlippage(5 bp, volume_limit 0.1)`, futures `VolatilityVolumeShare` (`eta * sigma * sqrt(volume share)`, 5% bar limit); `VolumeShareSlippage` [28][29] | Not found (no FX or crypto asset types in finance models) [26][28] | Not found |
| MetaTrader 5 tester | Broker symbol settings: per lot or per deal, in money, percentage or points, multi-level tiers, charged per deal or end of day/month [30] | Real ticks: spread varies tick by tick; "Every tick": `Ask = Bid + fixed spread of the minute bar`; bars built on Bid [31] | No slippage term; execution delay modes (none, random, fixed) and requotes beyond deviation [30] | Swap per symbol: points, money (several currencies), interest on current or open price (360-day), reopen modes; `SYMBOL_SWAP_ROLLOVER3DAYS` triple day [32] | Deposit currency selectable; profit/margin converted via Forex cross rates that must exist; profit formulas per calc mode (Forex: `diff * contract size * lots`, Futures: `diff * tick value / tick size * lots`) [30][32] |
| Freqtrade | One fee ratio applied on entry and exit; default from exchange market info, override `--fee` [33] | Not mentioned in backtesting docs [33] | None: fills at requested price inside high/low; stoploss exits at stop price [33] | Futures: funding from exchange funding-rate and mark candles, `fee = open_fund * open_mark * amount` per period; `futures_funding_rate` fallback [34][35] | PnL in `stake_currency`; `fiat_display_currency` is display only [36] |

## 2. Common patterns per asset class

| Asset class | Standard model | Typical venue magnitude |
|---|---|---|
| Futures | Fixed cash per contract per side (TradingView `cash_per_contract`, Nautilus `PerContractFeeModel`, Backtrader `COMM_FIXED`, Zipline `PerContract`, LEAN IB table). Spread from quotes when available, else ticks of slippage. No financing; rolls are a data/position issue, not a fee | NinjaTrader all-in per side: MES/MNQ $0.64-$0.94 (exch+NFA $0.36, clearing $0.19, commission $0.09-$0.39); MGC $0.99-$1.29 (exch+NFA $0.71) [37]. LEAN's IB table: $0.57 per side for all three [17]. CME's own schedule PDF not fetched (blocked), so exchange share is from the broker page |
| FX | Spread is the main cost: from bid/ask data (LEAN, Nautilus, MT5) or a per-bar spread value (MT5 "Every tick"). Commission per notional (IB bp of value, OANDA cash per 10k units per leg). Daily swap at 17:00 New York with a triple day (Nautilus, MT5, brokers) | OANDA core pricing: 0.70 USD per 10,000 units per leg, spreads "as low as 0.0", "fluctuate with market liquidity" [38]. OANDA trading halt 16:59-17:05 New York (inside our Daily break), "spreads typically widen before the markets close" [39]. Financing `position value * rate / 365`, Wednesday triple, admin fee 1.00% for "other pairs" (majors fall here); example long 100k EUR/USD at -3.00% costs about $9.52/day [40] |
| Crypto spot | Percentage of notional, maker/taker (LEAN `BinanceFeeModel`, Nautilus `MakerTakerFeeModel`, Freqtrade fee ratio, vectorbt `fees`) | Binance VIP 0 0.100% / 0.100%, 0.075% with BNB [41]; Bybit VIP 0 0.1000% / 0.1000% [42] |
| Crypto perpetuals | Maker/taker percentage plus funding from rate data at each funding time (Nautilus, LEAN, Freqtrade) | Binance USDS-M USDT perps, regular user 0.0200% maker / 0.0500% taker, 0.0180% / 0.0450% with BNB [45][43] (LEAN's code uses 0.04% taker [19]); Bybit VIP 0 0.0200% / 0.0550% [42]. Binance funding every 8 h at 00:00/08:00/16:00 UTC, `nominal value * funding rate`, base interest 0.01% per 8 h, positive rate longs pay shorts [44] |
| Stocks / CFDs | Per share or percentage with minimums (Zipline `PerShare`, IB); slippage in bp or volume share; short borrow interest (Backtrader `interest`, LEAN short model); CFD swap like FX (Nautilus, MT5) | Not researched (out of scope) |

Pattern worth stating: no engine found here uses a clock-based spread schedule. Time-varying spread always comes
from data (quotes, MT5 per-bar spread) or a fill-model state (Nautilus `MarketHoursFillModel`, whose state cannot be
switched from Python, so in practice also not a schedule). A spread schedule
for the Daily break would be our own invention; the established alternative is a spread column in the data.

Second pattern: limit fills do not slip in TradingView [1] or Nautilus [7]; ours charges spread and slippage on
targets too. Backtrader fills a limit at the limit or a better open, slipping only via `slip_open` and never past
the limit (`_try_exec_limit`) [46].

## 3. Fit against our engine

### MNQ / MES / MGC (CME micro futures)

| Approach | Fit | Reasoning |
|---|---|---|
| Fixed cash per contract per side | Fits (have it) | `commission_per_side` is exactly this; $1.25 is above the NinjaTrader all-in $0.64-$0.94 for MES/MNQ and about the Free plan $1.29 for MGC [37] |
| Spread as ticks | Fits | Databento OHLCV-1m is assumed built from trades (unverified here); 1 tick spread on micros is a reasonable fixed proxy, no quote data needed |
| Slippage on stops and market fills only | Doesn't fit today | We also slip targets; with tick-through targets (ADR 0001) a target fill pays 1.5 ticks it would not in TradingView. Needs a per-fill-type rule, not a new field |
| Volume/volatility slippage (Zipline, LEAN) | Not needed | Micros at 1-few contracts are far below bar volume; adds data dependencies for no change |
| Percentage commission | Not needed | Exchange fees are per contract |
| Financing | Not needed | None on futures; rolls are handled by the roll exit (ADR 0002) |
| Currency conversion | Not needed | USD-settled, `point_value` in USD |

### EURUSD / GBPUSD

| Approach | Fit | Reasoning |
|---|---|---|
| Fixed spread in price units | Fits for normal hours | Ours: 1 pip spread (half per side) + 0.5 pip slippage per side = 2 pips ($20 per 100k) RT. OANDA core commission alone is $14 per 100k RT (1.4 pips) on top of a spread "as low as 0.0" [38], so ours is about commission plus 0.6 pip; venue unknown, so unverified as calibration |
| Cash per unit commission | Fits (have it) | OANDA's 0.70 USD per 10k per leg is `commission_per_side = 0.00007` on base units for USD-quoted pairs; a percentage field is not needed. Only IB-style per-order minimums are inexpressible [17] |
| Time-varying spread from data (bid/ask or spread column) | Doesn't fit current data | CSVs have no bid/ask or spread column; would need a new data source |
| Spread schedule for the Daily break | Possible, our invention | Would need e.g. `spread_schedule: [{start: "16:45", end: "18:15", tz: "America/New_York", spread_points: x}]`; size of widening has no primary source (unverified) |
| Daily swap at 17:00 New York, triple Wednesday | Not needed unless trades hold past 17:00 | ~0.82 bp/day on the OANDA -3% example (3% / 365) [40]; would need per-pair long/short annual rates over time, plus a check of whether `--session_exit` already closes before 17:00 |
| Bid-based bars (MT5) | Open question | If bars are bid, a short's stop triggers on bid when it should trigger on ask; half-spread-each-side assumes mid bars |
| Currency conversion | Not needed now | Both pairs are USD-quoted, so PnL is already USD; a JPY or CHF-quoted pair would need a quote-to-USD rate per bar (`point_value` stops being constant) |

### BTCUSD

| Approach | Fit | Reasoning |
|---|---|---|
| Fixed $5 spread + $1 slippage | Doesn't fit | $7 RT is 12.3 bp at $5.7k and 1.1 bp at $65k; costs fall ~11x as price rises, so early years are overcharged and late years nearly free |
| Percentage commission per side | Needed | Both venues checked charge percent of notional: 0.10% spot taker [41][42] = 20 bp RT, 0.05-0.055% perp taker [42][43] = 10-11 bp RT. Needs e.g. `commission_pct_per_side`, applied to fill price * qty * point_value |
| Spread/slippage as percentage or bp | Fits better than points | Same scaling problem as the fee; e.g. `spread_bps` / `slippage_bps`, or a single bp cost like Zipline's `FixedBasisPointsSlippage` [28] |
| Maker/taker split | Not needed now | We only take (next-open, stop entries, stops); targets would be maker, but one taker rate is a conservative first step |
| Funding | Only if BTCUSD is a perp | Spot has none. Perp: rate data per 8 h, or a constant like Freqtrade's `futures_funding_rate`; base 0.01% per 8 h [44] is 1 bp per funding time crossed |
| Currency conversion | Not needed | USD quoted; a USDT-quoted perp would add USDT/USD basis (ignore) |

### Config sketch (for discussion, not a design)

- `commission_pct_per_side` (fraction of notional), alongside `commission_per_side`; one of them non-zero.
- `spread_bps` / `slippage_bps`, or a `cost_unit: "points" | "bps"` switch for the two existing fields.
- Optional `spread_schedule` keyed on New York time for FX, only if the grilling decides the Daily break matters.
- Per-fill-type cost rule: whether targets (limit-like) pay spread and slippage.

## 4. Open questions for the grilling

1. Are the FX bars bid, ask or mid? MT5 builds bars on bid [31]. This changes stop triggers on shorts, not only cost.
2. Is BTCUSD spot or a perp, and which venue? Decides percentage fee level and whether funding exists at all.
3. Do any FX strategies hold through 17:00 New York or BTC strategies through 00/08/16 UTC? Check `--session_exit`
   usage and trade durations before adding swap or funding.
4. Should targets keep paying spread and slippage, given TradingView and Nautilus do not slip limit fills [1][7]?
   This is also a Pine parity question for the README's "Differences From Pine".
5. Percentage fields for BTC only, or a general `bps` unit for every asset? Futures and FX fit cash-per-unit.
6. How big is the Daily break widening? No primary source found; any number would be an assumption (unverified).
   Is a schedule worth it versus blocking entries in the Daily break?
7. Is `commission_per_side = 1.25` meant to be conservative? It is ~1.3-2x the all-in retail rate for MES/MNQ [37].
8. Should risk sizing include expected costs? Today `qty` is sized from raw entry to stop, costs excluded.
9. Any plan for non-USD-quoted pairs (USDJPY, EURGBP)? That is when `point_value` must become a per-bar conversion.

## 5. Sources

Commits: TradingView docs live; Nautilus `6702552`; Backtrader `b853d7c`; vectorbt `9ac1b69`; LEAN `f696c55`;
Zipline-reloaded `5dd9fef`; Freqtrade `7fcb509`.

1. TradingView, Declaration statements: https://www.tradingview.com/pine-script-docs/language/declaration-statements/
2. TradingView, Strategy properties: https://www.tradingview.com/support/solutions/43000628599-strategy-properties/
3. TradingView, Broker emulator: https://www.tradingview.com/support/solutions/43000786181-broker-emulator/
4. TradingView, Concepts / Strategies (currency, commission, slippage): https://www.tradingview.com/pine-script-docs/concepts/strategies/
5. Nautilus fee model stubs: https://github.com/nautechsystems/nautilus_trader/blob/6702552d2e0653ced9581b0d86701594b165cbc0/python/nautilus_trader/execution/__init__.pyi#L139-L242
6. Nautilus, Data and venues: https://github.com/nautechsystems/nautilus_trader/blob/6702552d2e0653ced9581b0d86701594b165cbc0/docs/concepts/backtesting/data-and-venues.md#L40-L48
7. Nautilus, Fill models: https://nautilustrader.io/docs/latest/concepts/backtesting/fill-models
8. Nautilus, Fill prices and matching (price protection): https://github.com/nautechsystems/nautilus_trader/blob/6702552d2e0653ced9581b0d86701594b165cbc0/docs/concepts/backtesting/fill-prices-and-matching.md#L131-L141
9. Nautilus FX rollover module: https://github.com/nautechsystems/nautilus_trader/blob/6702552d2e0653ced9581b0d86701594b165cbc0/crates/backtest/src/modules/fx_rollover.rs#L133-L322
10. Nautilus, Simulation modules: https://nautilustrader.io/docs/latest/concepts/backtesting/simulation-modules
11. Nautilus, Accounts and margin (funding): https://github.com/nautechsystems/nautilus_trader/blob/6702552d2e0653ced9581b0d86701594b165cbc0/docs/concepts/backtesting/accounts-and-margin.md#L1-L36
12. Nautilus `SimulatedExchange` funding: https://github.com/nautechsystems/nautilus_trader/blob/6702552d2e0653ced9581b0d86701594b165cbc0/crates/backtest/src/exchange.rs#L1161-L1300
13. Backtrader `comminfo.py`: https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader/comminfo.py#L76-L245
14. Backtrader `bbroker.py` slippage params: https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader/brokers/bbroker.py#L125-L237
15. Backtrader `fillers.py`: https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader/fillers.py
16. vectorbt `Portfolio.from_orders` params: https://github.com/polakowo/vectorbt/blob/9ac1b697166b00911def494cf95937274218aa54/vectorbt/portfolio/base.py#L1689-L1694
17. LEAN `InteractiveBrokersFeeModel.cs`: https://github.com/QuantConnect/Lean/blob/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Orders/Fees/InteractiveBrokersFeeModel.cs#L270-L510
18. LEAN `BinanceFeeModel.cs`: https://github.com/QuantConnect/Lean/blob/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Orders/Fees/BinanceFeeModel.cs#L30-L46
19. LEAN `BinanceFuturesFeeModel.cs`: https://github.com/QuantConnect/Lean/blob/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Orders/Fees/BinanceFuturesFeeModel.cs#L29-L47
20. LEAN `FillModel.cs` bid/ask: https://github.com/QuantConnect/Lean/blob/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Orders/Fills/FillModel.cs#L880-L988
21. QuantConnect, Slippage key concepts: https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/slippage/key-concepts
22. LEAN slippage models: https://github.com/QuantConnect/Lean/tree/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Orders/Slippage
23. LEAN `BinanceFutureMarginInterestRateModel.cs`: https://github.com/QuantConnect/Lean/blob/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Securities/CryptoFuture/BinanceFutureMarginInterestRateModel.cs
24. LEAN source tree (searched for rollover/swap/margin interest): https://github.com/QuantConnect/Lean/tree/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Securities
25. LEAN `Cash.cs`: https://github.com/QuantConnect/Lean/blob/f696c556828e9b3ea6695cb4ac41ff233ba5f430/Common/Securities/Cash.cs#L113-L142
26. Zipline `commission.py`: https://github.com/stefan-jansen/zipline-reloaded/blob/5dd9fef5d42c2d084021b5ae10454de166cc5541/src/zipline/finance/commission.py#L25-L230
27. Zipline `constants.py` exchange fees: https://github.com/stefan-jansen/zipline-reloaded/blob/5dd9fef5d42c2d084021b5ae10454de166cc5541/src/zipline/finance/constants.py#L29-L46
28. Zipline `slippage.py`: https://github.com/stefan-jansen/zipline-reloaded/blob/5dd9fef5d42c2d084021b5ae10454de166cc5541/src/zipline/finance/slippage.py#L334-L645
29. Zipline default models: https://github.com/stefan-jansen/zipline-reloaded/blob/5dd9fef5d42c2d084021b5ae10454de166cc5541/src/zipline/finance/blotter/simulation_blotter.py#L63-L77
30. MetaTrader 5, Strategy testing: https://www.metatrader5.com/en/terminal/help/algotrading/testing
31. MetaTrader 5, Tick generation modes: https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation
32. MQL5, Symbol properties: https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants
33. Freqtrade, Backtesting: https://www.freqtrade.io/en/stable/backtesting/
34. Freqtrade, Leverage (funding in backtesting): https://www.freqtrade.io/en/stable/leverage/
35. Freqtrade `exchange.py` funding fee: https://github.com/freqtrade/freqtrade/blob/7fcb509a434cfaa5e0943a7ccc005967cfd0b9d9/freqtrade/exchange/exchange.py#L4199-L4261
36. Freqtrade, Configuration: https://www.freqtrade.io/en/stable/configuration/
37. NinjaTrader, Commissions (itemized, as of 2026-08-14): https://ninjatrader.com/pricing/commissions/
38. OANDA US, Core spreads + commission: https://help.oanda.com/us/en/faqs/spreads-commission.htm
39. OANDA UK, Trading hours: https://www.oanda.com/uk-en/trading/hours-of-operation/
40. OANDA US, Financing fees: https://www.oanda.com/us-en/trading/financing-fees/
41. Binance, Spot fee rate: https://www.binance.com/en/fee/trading
42. Bybit, Trading fee structure (updated 2026-11-20): https://www.bybit.com/en/help-center/article/Trading-Fee-Structure
43. Binance, Futures fee structure FAQ: https://www.binance.com/en/support/faq/360033544231
44. Binance, Funding rates FAQ: https://www.binance.com/en/support/faq/introduction-to-binance-futures-funding-rates-360033525031
45. Binance, USDS-M futures fee rate table (fetched 2026-11-24): https://www.binance.com/en/fee/futureFee
46. Backtrader `bbroker.py` `_try_exec_limit`: https://github.com/mementum/backtrader/blob/b853d7c90b6721476eb5a5ea3135224e33db1f14/backtrader/brokers/bbroker.py#L898-L919
