import pandas as pd


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
    ema = data["close"].ewm(span=50, adjust=False).mean().reindex(view.index)
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
