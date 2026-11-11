# Coding Standards

Judgement rules applied when a change is reviewed.

- Identifiers and comments use `GLOSSARY.md` terms and never a word a term lists under _Avoid_.
- Keep changes focused and Python 3.11-compatible; prefer the standard library and existing dependencies.
- Do not leave dead code, speculative abstractions, input mutation, or unrelated formatting churn.
- Validate external inputs at the CLI boundary and again in callable core functions that rely on them.
- Keep trading logic deterministic and free of lookahead: use confirmed/current-or-past bars and configured tick alignment.
- Document intentional differences from reference strategies and protect them with small synthetic regression tests.
- Keep unit tests offline and strategy samples small. Run targeted tests first and the full suite for cross-cutting changes.
- Prove a test can fail with `"$PY" tests/mutate.py <file> --tests <pytest args...> --mutation <search> <replace>` (`PY` is set in `AGENTS.md`).
