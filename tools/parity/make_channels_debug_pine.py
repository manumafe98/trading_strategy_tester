import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

output = Path(sys.argv[1] if len(sys.argv) > 1 else "choch_sr_channels_debug.pine")
src = (REPO / "pine_scripts_examples/choch_sr_mtf_strategy.pine").read_text(encoding="utf-8")
src = src.replace('strategy("CHoCH 1m + S/R Channels 15m"', 'strategy("CHoCH SR channels debug"')
src = src.replace('showSwing = input.bool(true, "Show Swing Points"', 'showSwing = input.bool(false, "Show Swing Points"')
for line in (
    '    label.new(math.floor(bar_index - (bar_index - previousHighIndex) / 2), previousHigh, highIsChoch ? "CHoCH" : "BOS", color = color.new(color.black, 100), textcolor = bosColor, size = size.tiny)\n',
    '    label.new(math.floor(bar_index - (bar_index - previousLowIndex) / 2), previousLow, lowIsChoch ? "CHoCH" : "BOS", color = color.new(color.black, 100), textcolor = bosColor, style = label.style_label_up, size = size.tiny)\n',
):
    assert line in src
    src = src.replace(line, "")
src += """
if new15Minute
    string channelText = str.format("{0,date,yyyy-MM-dd HH:mm}|{1}", time, srReferenceClose)
    for slot = 0 to 5
        channelText += str.format("|{0}/{1}", array.get(srLevels, slot * 2), array.get(srLevels, slot * 2 + 1))
    label.new(bar_index, high, channelText, color = color.new(color.black, 100), textcolor = color.new(color.white, 100), size = size.tiny)
"""
output.write_text(src, encoding="utf-8")
print(output)
