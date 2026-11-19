from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def pivot_high_bars(values: np.ndarray, length: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    is_pivot = np.zeros(len(values), dtype=bool)
    if len(values) < 2 * length + 1:
        return is_pivot
    windows = sliding_window_view(values, 2 * length + 1)
    center = windows[:, length]
    left_ok = (windows[:, :length] <= center[:, None]).all(axis=1)
    right_ok = (windows[:, length + 1 :] < center[:, None]).all(axis=1)
    is_pivot[length:-length] = left_ok & right_ok
    return is_pivot


__all__ = ["pivot_high_bars"]
