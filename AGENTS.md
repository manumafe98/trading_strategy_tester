# AGENTS.md

Use ponytail for this project: keep changes small, boring, and runnable.

## Project Shape

- `tester_framework/` is the tracked Python runner.
- `config/` contains JSON runtime config such as asset aliases, point values, and cost assumptions.
- `strategy/` contains strategy modules loaded by the runner.
- `trades/` contains per-trade HTML charts.
- `results/` contains visual HTML result reports.
- `.venv` is the project virtual environment; do not install dependencies globally.

## Code Shape

- `tester_framework/__main__.py` parses CLI args and orchestrates runs.
- `tester_framework/settings.py` loads config and keeps runtime constants.
- `tester_framework/data.py` downloads and normalizes OHLCV data.
- `tester_framework/backtest.py` owns fills, exits, risk sizing, and metrics.
- `tester_framework/reports.py` clears output folders and writes HTML reports/charts.
- `tester_framework/strategy_loader.py` loads local strategy modules.

Normal runs clear `results/` and `trades/` before generating fresh output.

## Strategy Contract

Each strategy is `strategy/<name>.py` and must expose:

```python
def generate_signals(df, asset, timeframe, params):
    return signals  # columns: time, side, stop
```

Optional chart overlay hook:

```python
def plot_indicators(fig, data, view, asset, timeframe, params):
    ...
```

Strategies own indicators and stops. The framework owns data loading, fills, exits, risk sizing, costs, result HTML, and trade HTML.

## Checks

Run the smallest useful checks after changes:

```bash
source .venv/Scripts/activate
python -m tester_framework --self_check
python -m tester_framework --strategy ema50 --asset MGC --timeframe 1h --time_period 60d --operation all --exit_structure 1RR --risk 1 --capital 10000 --with_costs
```
