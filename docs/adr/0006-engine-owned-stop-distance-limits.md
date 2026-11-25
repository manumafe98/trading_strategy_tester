# Stop distance limits belong to the engine

Stop distance limits, the smallest and largest R a variant accepts, are an engine setting (`--stop_distance`, per asset and swept like RR), not a strategy parameter. `choch_sr` used to filter its own signals with `stop_min`, `stop_max` and `stop_unit`, so no other strategy could have the limits, the check had to guess the fill price, and filtered signals never showed in `Discarded`. The engine knows the real fill and already discards trades below `min_risk_ticks`, so the limits sit beside that check and measure the trade's actual R.

## Consequences

A strategy no longer sees whether its stop fits the limits, so it cannot fall back to another setup when the first one's stop is too wide or too tight: an out-of-limits signal is simply discarded. A strategy that needs such a fallback must do its own check, under its own parameter name.
