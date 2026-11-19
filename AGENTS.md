# AGENTS.md

Keep changes small, boring, and runnable.

Domain terms (session window, high timeframe, R, variant, ...) are defined in `GLOSSARY.md`; use them as written there.

Review-time coding standards live in `docs/CODING_STANDARDS.md`.

## Self-Documenting Code

Write no comments or docstrings in code. Names, small functions and tests carry the meaning; rename or extract when code needs explaining. A reason the code can't show goes in the README (Pine differences), an ADR (design decisions) or an issue or test name (unverified assumptions). Tool directives such as `# noqa` are allowed when a tool needs them.

## Strategy Contract

Writing or changing a strategy: read the README's "Strategy API" section, and "Execution Model" for stop entries and rolls. Strategies own indicators and stops. The framework owns data loading, fills, exits, risk sizing, costs, result HTML, and trade HTML.

## Pine Claims

Claims about Pine behaviour are checked against the reference scripts in `pine_scripts_examples/` and recorded in the README's "Differences From Pine" section. Checking a port against TradingView: follow `docs/agents/pine-parity.md`.

## Checks

Run the smallest useful checks after changes:

```bash
PY="$(git rev-parse --path-format=absolute --git-common-dir)/../.venv/Scripts/python.exe"
"$PY" -m pytest -q
"$PY" -m pytest -q -m smoke
```

The smoke tests run reference backtests on local `data/` (about 80 seconds) and assert their known results; a failure signals a behaviour change, and an intended one is an edit to `tests/test_smoke.py`. They skip where the data is missing. Like any run, they clear `results/` and `trades/`. From a linked worktree, set `TESTER_DATA_DIR` to the main checkout's `data/` instead of linking the folder.

## Agent skills

### Spec workflow

User-invoked, so missing from the skill list: `/grill-with-docs` → `/to-spec` → `/to-tickets` → `/implement-spec`.

### Issue tracker

Issues and specs are tracked in GitHub Issues on `manumafe98/trading_strategy_tester` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default canonical labels (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one root `GLOSSARY.md` plus `docs/adr/` (created lazily). See `docs/agents/domain.md`.
