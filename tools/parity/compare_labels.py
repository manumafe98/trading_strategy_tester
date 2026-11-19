import difflib
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from strategy.utils.market_structure import market_structure  # noqa: E402
from tester_framework.data import load_data  # noqa: E402
from tester_framework.settings import load_assets  # noqa: E402

if len(sys.argv) < 2:
    sys.exit("usage: compare_labels.py TV_LABELS_TXT")
tv_labels_path = Path(sys.argv[1])

cfg = load_assets()["MGC"]
data = load_data("MGC", cfg, "1m", "2026", "local").loc["2026-05-10 22:00":"2026-06-03 02:59"]
structure = market_structure(data, 20)

ours = []
for time, row in structure.iterrows():
    if pd.notna(row["swing_high_label"]):
        ours.append((f"{row['swing_high_label']}:{row['swing_high_price']:g}", time))
    if pd.notna(row["swing_low_label"]):
        ours.append((f"{row['swing_low_label']}:{row['swing_low_price']:g}", time))
    if pd.notna(row["bullish_break_price"]):
        ours.append((f"{'C' if row['bullish_choch'] else 'B'}:{row['bullish_break_price']:g}", time))
    if pd.notna(row["bearish_break_price"]):
        ours.append((f"{'C' if row['bearish_choch'] else 'B'}:{row['bearish_break_price']:g}", time))

tv = tv_labels_path.read_text().split()
tokens = [token for token, _ in ours]
print("ours", len(tokens), "tv", len(tv))
tail = tokens[-len(tv):]
times = [time for _, time in ours[-len(tv):]]
print("tail window", times[0], "->", times[-1])
matcher = difflib.SequenceMatcher(a=tail, b=tv, autojunk=False)
print("ratio", round(matcher.ratio(), 4))
for tag, i1, i2, j1, j2 in matcher.get_opcodes():
    if tag != "equal":
        print(tag, "ours", list(zip(tail[i1:i2], [str(t) for t in times[i1:i2]])), "tv", tv[j1:j2])
