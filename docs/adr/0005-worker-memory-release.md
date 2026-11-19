# Workers release chart and cache memory explicitly

Variant workers run in parallel processes and on Windows they exhausted memory, so `tester_framework/runner.py` releases memory on purpose in two places that look redundant.

Plotly figures hold reference cycles that the cyclic collector frees too late when many trade charts are written in a row, so the runner calls `gc.collect()` after every `CHARTS_PER_PLOTLY_GC` charts and once more for the remainder.

`run_variants` builds DataFrames on memory-mapped cache arrays, and those frames can outlive the call (pytest keeps them alive in traceback locals to render failures). The `finally` block drops its own references so the memory maps close as soon as the last other reference is collected, instead of staying open with the frame.

The same cache stores only float arrays, so the runner turns a futures `contract` column into integer codes with `pd.factorize` before caching: a roll needs only to know where the contract changes, not its name.
