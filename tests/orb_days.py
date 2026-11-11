from __future__ import annotations

import pandas as pd


def make_combined_day(bull=True, with_ext=True):
    """Build a 1m trading day with an ORB, FVG with/without extension, and a breakout."""
    times = pd.date_range("2025-01-02 14:30", periods=20, freq="1min")
    df = pd.DataFrame(
        {
            "open": [100.0] * 20,
            "high": [100.5] * 20,
            "low": [99.5] * 20,
            "close": [100.0] * 20,
            "volume": [0] * 20,
        },
        index=times,
    )
    if bull:
        df.iloc[2, df.columns.get_loc("high")] = 99.0
        df.iloc[3, df.columns.get_loc("low")] = 98.5 if with_ext else 99.5
        df.iloc[4, df.columns.get_loc("low")] = 101.0
        df.iloc[4, df.columns.get_loc("close")] = 101.5
        df.iloc[4, df.columns.get_loc("high")] = 102.0
        if with_ext:
            df.iloc[10, df.columns.get_loc("close")] = 103.0
            df.iloc[10, df.columns.get_loc("high")] = 103.5
            df.iloc[10, df.columns.get_loc("low")] = 102.5
        else:
            df.iloc[10, df.columns.get_loc("close")] = 101.0
            df.iloc[10, df.columns.get_loc("high")] = 101.5
            df.iloc[10, df.columns.get_loc("low")] = 100.0
    else:
        df.iloc[2, df.columns.get_loc("low")] = 101.0
        df.iloc[3, df.columns.get_loc("high")] = 101.5 if with_ext else 100.5
        df.iloc[4, df.columns.get_loc("high")] = 99.0
        df.iloc[4, df.columns.get_loc("close")] = 98.5
        df.iloc[4, df.columns.get_loc("low")] = 98.0
        if with_ext:
            df.iloc[10, df.columns.get_loc("close")] = 97.0
            df.iloc[10, df.columns.get_loc("high")] = 97.5
            df.iloc[10, df.columns.get_loc("low")] = 96.5
        else:
            df.iloc[10, df.columns.get_loc("close")] = 99.0
            df.iloc[10, df.columns.get_loc("high")] = 100.0
            df.iloc[10, df.columns.get_loc("low")] = 98.5
    return df
