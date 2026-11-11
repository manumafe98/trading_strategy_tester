# AGENTS.md

Keep changes small, boring, and runnable.

Domain terms (session window, signal timeframe, R, variant, ...) are defined in `GLOSSARY.md`; use them as written there.

## Project Shape

- `tester_framework/` is the tracked Python runner.
- `config/` contains JSON runtime config such as asset aliases, point values, and cost assumptions.
- `strategy/` contains strategy modules loaded by the runner.
- `strategy/utils/` contains shared logic (ORB session helpers, FVG detection) used by ORB strategies.
- `pine_scripts_examples/` contains the TradingView Pine reference scripts the strategies are ported from.
- `trades/` contains per-trade HTML charts.
- `results/` contains visual HTML result reports.
- `.venv` is the project virtual environment; do not install dependencies globally.

## Code Shape

- `tester_framework/__main__.py` parses CLI args and orchestrates runs.
- `tester_framework/cli.py` holds CLI argument types and the run configuration.
- `tester_framework/runner.py` runs variants in parallel workers.
- `tester_framework/sessions.py` parses session windows and filters signals by them.
- `tester_framework/analytics.py` computes trade analytics and runs strategy metric hooks.
- `tester_framework/settings.py` loads config and keeps runtime constants.
- `tester_framework/data.py` downloads and normalizes OHLCV data.
- `tester_framework/backtest.py` owns fills, exits, risk sizing, and metrics.
- `tester_framework/reports.py` clears output folders and writes HTML reports/charts.
- `tester_framework/strategy_loader.py` loads local strategy modules.
- `tests/` contains the pytest suite.

Normal runs clear `results/` and `trades/` before generating fresh output.

## Strategy Contract

Each strategy is `strategy/<name>.py` and must expose:

```python
def generate_signals(df, asset, timeframe, params):
    return signals  # columns: time, side, stop
```

- `side` must be `long` or `short`.
- `params["tick_size"]` contains the configured asset price tick.
- The input `df` is shared across timeframes; strategies must not mutate it.
- A strategy can load data at a different execution timeframe by setting `EXECUTION_TIMEFRAME = "5m"`.
- Extra non-NA signal columns (beyond `time`, `side`, `stop`, `entry`) are copied onto the trade passed to `plot_indicators` as `params["trade"]`; engine-owned trade keys win on name clashes.

Optional hooks:

```python
def plot_indicators(fig, data, view, asset, timeframe, params):
    ...

def calculate_metrics(data, signals, trades, asset, timeframe, params):
    return {"My metric": value}  # flat dict of scalars
```

Strategies own indicators and stops. The framework owns data loading, fills, exits, risk sizing, costs, result HTML, and trade HTML.

## Code Quality

- Keep changes focused and Python 3.11-compatible; prefer the standard library and existing dependencies.
- Do not leave dead code, speculative abstractions, input mutation, or unrelated formatting churn.
- Validate external inputs at the CLI boundary and again in callable core functions that rely on them.
- Keep trading logic deterministic and free of lookahead: use confirmed/current-or-past bars and configured tick alignment.
- Document intentional differences from reference strategies and protect them with small synthetic regression tests.
- Keep unit tests offline and strategy samples small. Run targeted tests first and the full suite for cross-cutting changes.
- Keep text files LF-only and run `git diff --check` before finishing.

## Review

Use `/ponytail-review` to review a change and `/ponytail-audit` for a whole-repo audit.

## Checks

Run the smallest useful checks after changes:

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m tester_framework --strategies ema50 --asset MGC --timeframe 1h --time_period 2021 --data_source local --operation all --risk_reward_ratio 1 --risk 1 --capital 10000 --with_costs --trade_html 0
```

The smoke run takes about 20 seconds and prints 138 trades and Return -5.18% / -14.42% (gross / net). A different result signals a behaviour change. It reads the local `data/` folder, which only exists on the machine that holds it; agents elsewhere skip the smoke run.

## Agent skills

### Issue tracker

Issues and specs are tracked in GitHub Issues on `manumafe98/trading_strategy_tester` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `GLOSSARY.md` plus `docs/adr/` (created lazily). See `docs/agents/domain.md`.
