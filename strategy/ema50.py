import pandas as pd

CHART_EMA_WARMUP_BARS = 1000


def generate_signals(df, asset, timeframe, params):
    ema = df["close"].ewm(span=50, adjust=False).mean()
    cross_up = (df["close"] > ema) & (df["close"].shift(1) <= ema.shift(1))
    cross_down = (df["close"] < ema) & (df["close"].shift(1) >= ema.shift(1))
    lows = df["low"].rolling(5).min()
    highs = df["high"].rolling(5).max()
    rows = []

    for time in df.index[cross_up.fillna(False)]:
        stop = lows.loc[time]
        if pd.notna(stop) and stop < df.loc[time, "close"]:
            rows.append({"time": time, "side": "long", "stop": stop, "reason": "close_cross_above_ema50"})

    for time in df.index[cross_down.fillna(False)]:
        stop = highs.loc[time]
        if pd.notna(stop) and stop > df.loc[time, "close"]:
            rows.append({"time": time, "side": "short", "stop": stop, "reason": "close_cross_below_ema50"})

    return pd.DataFrame(rows).sort_values("time") if rows else pd.DataFrame(columns=["time", "side", "stop", "reason"])


def plot_indicators(fig, data, view, asset, timeframe, params):
    # The seed's weight decays as (49/51)**n; after 1000 bars it is ~1e-18, so the
    # windowed EMA matches the full-data EMA to float precision without scanning all bars.
    start = max(0, data.index.searchsorted(view.index[0]) - CHART_EMA_WARMUP_BARS)
    end = data.index.searchsorted(view.index[-1], side="right")
    ema = data["close"].iloc[start:end].ewm(span=50, adjust=False).mean().reindex(view.index)
    fig.add_trace(
        {
            "type": "scatter",
            "mode": "lines",
            "x": view.index,
            "y": ema,
            "name": "EMA 50",
            "line": {"color": "#f59f00", "width": 2},
        }
    )
