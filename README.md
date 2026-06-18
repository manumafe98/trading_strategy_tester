# trading_strategy_tester

Minimal Python backtesting runner.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
```

## Run

```bash
python -m tester_framework --strategy ema50 --asset MGC --timeframe 1h --time_period 60d --operation all --exit_structure 1RR --risk 1 --capital 10000 --with_costs
```

Each normal run clears `results/` and `trades/` first, then writes the latest visual result HTML and trade charts.

When `--operation all` is used, results include total, long, and short trade counts. For `long_only` or `short_only`, only total trades is shown.

Asset aliases, point values, and cost assumptions live in `config/assets.json`.

## Command Examples

Multiple values are comma-separated, without spaces:

```bash
python -m tester_framework --strategy ema50 --asset MGC,MNQ --timeframe 1h,30m --exit_structure 1RR,2RR
```

Use all configured assets by omitting `--asset`; use the default timeframe list by omitting `--timeframe`:

```bash
python -m tester_framework --strategy ema50 --exit_structure 1RR
```

Long-only or short-only runs hide the long/short split columns because the operation already explains the side:

```bash
python -m tester_framework --strategy ema50 --asset MGC --operation long_only
python -m tester_framework --strategy ema50 --asset MGC --operation short_only
```

Risk can be global or per asset:

```bash
python -m tester_framework --strategy ema50 --asset MGC,MNQ --risk 1
python -m tester_framework --strategy ema50 --asset MGC,MNQ --risk MGC=1,MNQ=0.5
```

## Strategy API

Put local strategies in `strategy/<name>.py`:

```python
def generate_signals(df, asset, timeframe, params):
    return signals  # columns: time, side, stop
```

`side` must be `long` or `short`. The strategy owns entries and stops; exit structures own targets and trailing behavior.

Only trades that reach their configured TP or SL are counted. Setups still open at the end of the downloaded data are discarded.

Strategies can add indicators to trade charts with an optional hook:

```python
def plot_indicators(fig, data, view, asset, timeframe, params):
    ...
```

The framework calls this hook for each trade HTML. Keep indicator plotting in the strategy because only the strategy knows which lines matter.

## Checks

```bash
python -m tester_framework --self_check
```
