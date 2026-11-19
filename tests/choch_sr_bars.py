from __future__ import annotations

import pandas as pd


START = pd.Timestamp("2025-01-06 00:00")
HIGH_BAR_MINUTES = 5
WARM_UP_MIDS = (
    [104 - (104 - 100.1) * step / 11 for step in range(11)]
    + [100.1 + (104 - 100.1) * step / 11 for step in range(11)]
    + [104 - (104 - 100.3) * step / 11 for step in range(11)]
    + [100.3 + (103 - 100.3) * step / 11 for step in range(11)]
    + [103.0] * 8
)


def leg(start: float, end: float) -> list[float]:
    return [start + (end - start) * step / 11 for step in range(11)]


TWO_CHANNEL_MIDS = (
    leg(104, 100.1) + leg(100.1, 104) + leg(104, 101.6) + leg(101.6, 104)
    + leg(104, 100.3) + leg(100.3, 104) + leg(104, 101.8) + leg(101.8, 103) + [103.0] * 8
)
WARM_UP_SPREAD = 0.1
TRIGGER_SPREAD = 0.02
TRIGGER_CLOSES = [103.0, 103.0, 102.8, 103.1, 103.0, 102.0, 101.5, 101.0, 100.45, 100.40, 100.5, 100.3, 100.15, 100.25, 100.3, 100.55]


def bars(closes: list[float], spread: float, start: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": closes,
            "high": [round(close + spread, 4) for close in closes],
            "low": [round(close - spread, 4) for close in closes],
            "close": closes,
            "volume": [1.0] * len(closes),
        },
        index=pd.date_range(start, periods=len(closes), freq="1min"),
    )


def warm_up(mids: list[float] = WARM_UP_MIDS) -> pd.DataFrame:
    closes = [round(mid, 4) for mid in mids for _ in range(HIGH_BAR_MINUTES)]
    return bars(closes, WARM_UP_SPREAD, START)


def append(df: pd.DataFrame, closes: list[float], spread: float = TRIGGER_SPREAD) -> pd.DataFrame:
    return pd.concat([df, bars(closes, spread, df.index[-1] + pd.Timedelta(minutes=1))])


def long_setup() -> pd.DataFrame:
    return append(warm_up(), TRIGGER_CLOSES)


HIGHER_LOW_CLOSES = TRIGGER_CLOSES[:-1] + [100.25, 100.30, 100.22, 100.30, 100.35, 100.55]


def higher_low_setup() -> pd.DataFrame:
    return append(warm_up(), HIGHER_LOW_CLOSES)


def mirror(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": 200 - df["open"],
            "high": 200 - df["low"],
            "low": 200 - df["high"],
            "close": 200 - df["close"],
            "volume": df["volume"],
        },
        index=df.index,
    ).round(4)


def shifted_trigger(choch_low: float | None = None) -> pd.DataFrame:
    closes = TRIGGER_CLOSES[:7] + [round(close + 0.5, 4) for close in TRIGGER_CLOSES[7:]]
    df = append(warm_up(), closes)
    if choch_low is not None:
        df.iloc[-1, df.columns.get_loc("low")] = choch_low
    return df


def role_flip_setup(flip_close: float) -> pd.DataFrame:
    return append(long_setup().iloc[:-1], [100.25, 100.2, 100.1, 100.0, flip_close, 100.3, 100.55])


RECOVERY_CLOSES = [100.8, 101.2, 101.6, 102.0, 102.4, 102.8] + [103.0] * 60


def repeated_setup() -> pd.DataFrame:
    return append(long_setup(), RECOVERY_CLOSES + TRIGGER_CLOSES)


def two_channel_setup() -> pd.DataFrame:
    upper_trigger = TRIGGER_CLOSES[:5] + [round(close + 1.5, 4) for close in TRIGGER_CLOSES[5:]]
    return append(append(warm_up(TWO_CHANNEL_MIDS), TRIGGER_CLOSES), RECOVERY_CLOSES + upper_trigger)
