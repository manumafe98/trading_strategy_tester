# AGENTS.md

Keep changes small, boring, and runnable.

Domain terms (session window, signal timeframe, R, variant, ...) are defined in `GLOSSARY.md`; use them as written there.

Review-time coding standards live in `docs/CODING_STANDARDS.md`.

## Project Shape

- `tester_framework/` is the Python runner.
- `config/` contains JSON runtime config such as asset aliases, point values, and cost assumptions.
- `strategy/` contains strategy modules loaded by the runner; `strategy/utils/` holds their shared ORB logic.
- `pine_scripts_examples/` contains the TradingView Pine reference scripts the strategies are ported from.
- `trades/` and `results/` hold generated trade charts and result bundles; normal runs clear both first.
- `.venv` is the project virtual environment; do not install dependencies globally.

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

## Review

Use `/ponytail-review` to review a change and `/ponytail-audit` for a whole-repo audit.

## Pine Claims

Claims about Pine behaviour are checked against the reference scripts in `pine_scripts_examples/` and recorded in the README's "Differences From Pine" section.

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
