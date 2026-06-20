from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


FVG_START_MINUTE = 570
FVG_END_MINUTE = 720


@dataclass(frozen=True)
class FVG:
    bar_index: int
    top: float
    bottom: float
    is_bull: bool
    ext_price: float | None


def track_fvgs_to_bar(
    day_df: pd.DataFrame,
    end_idx: int,
    minute_col: str = "_ny_minute",
    fvg_start: int = FVG_START_MINUTE,
    fvg_end: int = FVG_END_MINUTE,
) -> list[FVG]:
    """Track active FVGs from bar 0 up to and including end_idx (barstate.isconfirmed semantics).

    Processes 1m bars sequentially, matching the Pine Script detection/mitigation logic:
    - New FVG detected at bar i>=2 when in FVG session: bull if high[i-2] < low[i], bear if low[i-2] > high[i]
    - Mitigation: bull FVG removed when close < bottom; bear when close > top
    - Session-end cleanup: all FVGs cleared when minute exits FVG window
    - Extension line: bull -> low[i-1], bear -> high[i-1], retained when outside the gap
    """
    if end_idx < 2:
        return []

    fvgs: list[FVG] = []
    was_in_window = False
    minutes = day_df[minute_col].to_numpy()
    highs = day_df["high"].to_numpy(dtype=float)
    lows = day_df["low"].to_numpy(dtype=float)
    closes = day_df["close"].to_numpy(dtype=float)

    for i in range(2, end_idx + 1):
        minute = int(minutes[i])

        if fvg_start <= minute < fvg_end:
            was_in_window = True
        elif was_in_window and not (fvg_start <= minute < fvg_end) and fvgs:
            fvgs.clear()

        if fvg_start <= minute < fvg_end:
            j = len(fvgs) - 1
            while j >= 0:
                fvg = fvgs[j]
                close_i = float(closes[i])
                if (fvg.is_bull and close_i < fvg.bottom) or (not fvg.is_bull and close_i > fvg.top):
                    fvgs.pop(j)
                j -= 1

            high_prev2 = float(highs[i - 2])
            low_prev2 = float(lows[i - 2])
            high_i = float(highs[i])
            low_i = float(lows[i])

            is_bull = high_prev2 < low_i
            is_bear = low_prev2 > high_i

            if is_bull:
                top = low_i
                bottom = high_prev2
                ext = float(lows[i - 1])
                ext_price = ext if ext < bottom or ext > top else None
                fvgs.append(FVG(bar_index=i, top=top, bottom=bottom, is_bull=True, ext_price=ext_price))
            elif is_bear:
                top = low_prev2
                bottom = high_i
                ext = float(highs[i - 1])
                ext_price = ext if ext < bottom or ext > top else None
                fvgs.append(FVG(bar_index=i, top=top, bottom=bottom, is_bull=False, ext_price=ext_price))

    return fvgs


def find_qualifying_fvg(fvgs: list[FVG], direction: int, require_ext: bool = False) -> FVG | None:
    """Scan newest-to-oldest for a directional FVG match.

    direction=1 (long) needs a bullish FVG; direction=-1 (short) needs a bearish FVG.
    If require_ext, skips FVGs without an extension price.
    """
    need_bull = direction == 1
    for fvg in reversed(fvgs):
        if fvg.is_bull != need_bull:
            continue
        if require_ext and fvg.ext_price is None:
            continue
        return fvg
    return None


__all__ = [
    "FVG",
    "FVG_END_MINUTE",
    "FVG_START_MINUTE",
    "find_qualifying_fvg",
    "track_fvgs_to_bar",
]
