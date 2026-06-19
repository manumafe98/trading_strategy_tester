from __future__ import annotations

import shutil
import types
from datetime import datetime
from html import escape as html_escape
from pathlib import Path

import pandas as pd

from .settings import RESULTS_DIR, ROOT, TRADES_DIR
from .utils import clean_exit_name


ASSET_COLORS = ["#f9c22e", "#ff922b", "#4c8bf5", "#5cc9c8", "#b197fc", "#69db7c"]
HTML_LABELS = {"Sharpe Ratio": "Sharpe", "Return / DD": "Ret / DD"}
__all__ = ["format_metric", "metric_class", "reset_output_dirs", "write_results_html", "write_trade_html"]


def reset_output_dirs() -> None:
    root = ROOT.resolve()
    for folder in (RESULTS_DIR, TRADES_DIR):
        resolved = folder.resolve()
        if root not in (resolved, *resolved.parents):
            raise RuntimeError(f"Refusing to clear outside workspace: {resolved}")
        if folder.exists():
            for item in folder.iterdir():
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        folder.mkdir(exist_ok=True)


def write_trade_html(data: pd.DataFrame, trade: dict, strategy: types.ModuleType) -> None:
    # ponytail: keep plotly lazy so --self_check avoids chart dependencies.
    import plotly.graph_objects as go

    TRADES_DIR.mkdir(exist_ok=True)
    start = max(0, trade["entry_i"] - 20)
    if pd.notna(trade.get("plot_start_time")):
        start = min(start, max(0, data.index.searchsorted(pd.Timestamp(trade["plot_start_time"]), side="left")))
    end = min(len(data), trade["exit_i"] + 21)
    view = data.iloc[start:end]
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=view.index,
                open=view["open"],
                high=view["high"],
                low=view["low"],
                close=view["close"],
                name="price",
            )
        ]
    )
    plot_indicators = getattr(strategy, "plot_indicators", None)
    if callable(plot_indicators):
        # ponytail: strategies own their overlays; the framework only gives them the chart.
        plot_indicators(fig, data=data, view=view, asset=trade["asset"], timeframe=trade["timeframe"], params={"trade": trade})
    color = "green" if trade["side"] == "long" else "red"
    fig.add_trace(go.Scatter(x=[trade["entry_time"]], y=[trade["entry"]], mode="markers", name="entry", marker={"color": color, "size": 10}))
    fig.add_trace(go.Scatter(x=[trade["exit_time"]], y=[trade["exit"]], mode="markers", name=trade["exit_reason"], marker={"color": "black", "size": 10}))
    fig.add_hline(y=trade["stop"], line_dash="dot", line_color="red", annotation_text="stop")
    if trade["target"] is not None:
        fig.add_hline(y=trade["target"], line_dash="dot", line_color="green", annotation_text="target")
    fig.update_layout(title=f"{trade['asset']} {trade['timeframe']} {trade['side']}", xaxis_rangeslider_visible=False, xaxis_title="time (UTC)")

    stamp = pd.Timestamp(trade["entry_time"]).strftime("%Y-%m-%d_%H%M")
    rr = clean_exit_name(f"{trade['risk_reward_ratio']:g}RR")
    trail = "trail" if trade["trailing_stop"] else "fixed"
    base = f"{trade['asset']}_{trade['timeframe']}_{rr}_{trail}_{stamp}_{trade['side']}"
    path = TRADES_DIR / f"{base}.html"
    n = 2
    while path.exists():
        path = TRADES_DIR / f"{base}_{n}.html"
        n += 1
    fig.write_html(path, include_plotlyjs="cdn")


def format_metric(column: str, value) -> str:
    if column in {"Return", "Max DD"}:
        prefix = "+" if column == "Return" and float(value) > 0 else ""
        return f"{prefix}{float(value):.2f}%"
    if column in {"Return / DD", "Sharpe Ratio"}:
        return f"{float(value):.2f}"
    return str(value)


def metric_class(column: str, value) -> str:
    if column == "Return":
        return "good" if float(value) > 0 else "bad" if float(value) < 0 else "neutral"
    if column == "Max DD":
        return "warn"
    if column == "Return / DD":
        return "neutral"
    return "plain"


def write_results_html(table: pd.DataFrame, args) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    rows = []
    asset_colors = {}
    for _, row in table.iterrows():
        cells = []
        asset = str(row["Asset"])
        if asset not in asset_colors:
            asset_colors[asset] = ASSET_COLORS[len(asset_colors) % len(ASSET_COLORS)]
        color = asset_colors[asset]
        for col in table.columns:
            if col == "Asset":
                cells.append(f'<td><div class="asset-cell"><span class="asset-mark" style="background:{color}"></span><span class="asset" style="color:{color}">{html_escape(str(row[col]))}</span></div></td>')
            elif col == "TF":
                cells.append(f'<td class="c"><span class="tf-badge">{html_escape(str(row[col]))}</span></td>')
            elif col == "Long":
                cells.append(f'<td class="c"><span class="dir long">+ {html_escape(str(row[col]))}</span></td>')
            elif col == "Short":
                cells.append(f'<td class="c"><span class="dir short">- {html_escape(str(row[col]))}</span></td>')
            elif col == "Trades":
                cells.append(f'<td class="c n">{html_escape(str(row[col]))}</td>')
            else:
                cells.append(f'<td class="c"><span class="pill {metric_class(col, row[col])}">{html_escape(format_metric(col, row[col]))}</span></td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tags = [
        f"{args.time_period} period",
        f"{args.operation.replace('_', ' ')} operations",
        f"RR {args.risk_reward_ratio}",
        f"trailing {args.trailing_stop}",
        f"{args.risk}% risk",
        f"${args.capital:,.0f} capital",
        f"costs {'on' if args.with_costs else 'off'}",
    ]
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_escape(args.strategy)} results</title>
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --bg: #090c0b; --surface: #0f1312; --surface2: #161c1a; --border: #1e2422; --border2: #283230;
  --text: #ddd8c2; --sub: #7c8c88; --dim: #4a5552; --gold: #f0bb34;
  --green: #28a46a; --green-bg: rgba(40,164,106,.12); --green-bdr: rgba(40,164,106,.28);
  --red: #c43848; --amber: #c48018; --amber-bg: rgba(196,128,24,.12); --amber-bdr: rgba(196,128,24,.28);
  --neutral-bg: rgba(255,255,255,.05); --neutral-bdr: rgba(255,255,255,.10);
}}
body {{ font-family: Inter, Segoe UI, Arial, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; min-height: 100vh; }}
main {{ max-width: 1280px; margin: 0 auto; padding: 52px 52px 72px; }}
.hdr {{ margin-bottom: 40px; padding-bottom: 36px; border-bottom: 1px solid var(--border); }}
.hdr-title {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 30px; font-weight: 600; color: var(--text); margin-bottom: 6px; }}
.hdr-title em {{ color: var(--gold); font-style: normal; }}
.hdr-sep {{ color: var(--dim); margin: 0 10px; font-weight: 400; }}
.hdr-sub {{ font-size: 14px; color: var(--sub); margin-bottom: 16px; }}
.tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.tag {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 11px; color: var(--sub); background: var(--surface2); border: 1px solid var(--border2); border-radius: 5px; padding: 4px 10px; }}
.tbl-wrap {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
.tbl-bar {{ display: flex; align-items: center; padding: 14px 22px; border-bottom: 1px solid var(--border); }}
.tbl-lbl {{ font-size: 11px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; color: var(--dim); }}
.tbl-scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; min-width: 880px; }}
th {{ font-size: 10px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; color: var(--dim); padding: 11px 20px; text-align: center; border-bottom: 1px solid var(--border); background: var(--surface2); white-space: nowrap; }}
th:first-child {{ text-align: left; }}
td {{ padding: 20px; border-bottom: 1px solid var(--border); vertical-align: middle; white-space: nowrap; }}
tr:last-child td {{ border-bottom: none; }}
.c {{ text-align: center; }}
.asset-cell {{ display: flex; align-items: center; gap: 12px; }}
.asset-mark {{ width: 3px; height: 36px; flex-shrink: 0; }}
.asset {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 17px; font-weight: 600; }}
.tf-badge {{ display: inline-block; font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 12px; font-weight: 500; background: var(--surface2); border: 1px solid var(--border2); border-radius: 4px; padding: 4px 9px; color: var(--sub); }}
.n, .dir {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 15px; font-weight: 500; color: var(--text); }}
.dir {{ display: inline-block; min-width: 56px; }}
.long {{ color: var(--green); }}
.short {{ color: var(--red); }}
.pill {{ display: inline-flex; align-items: center; justify-content: center; font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 7px; min-width: 84px; letter-spacing: .2px; }}
.good {{ background: var(--green-bg); color: #38d888; border: 1px solid var(--green-bdr); }}
.bad {{ background: rgba(196,56,72,.12); color: #e06878; border: 1px solid rgba(196,56,72,.28); }}
.warn {{ background: var(--amber-bg); color: #dfaa38; border: 1px solid var(--amber-bdr); }}
.neutral, .plain {{ background: var(--neutral-bg); color: var(--sub); border: 1px solid var(--neutral-bdr); }}
.footer {{ margin-top: 28px; display: flex; justify-content: flex-end; font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 11px; color: var(--dim); }}
@media (max-width: 720px) {{ main {{ padding: 32px 18px 48px; }} .hdr-title {{ font-size: 24px; }} }}
</style>
</head>
<body>
<main>
<div class="hdr">
<p class="hdr-title"><em>{html_escape(args.strategy)}</em><span class="hdr-sep">/</span>results</p>
<p class="hdr-sub">Grouped by asset - all variants</p>
<div class="tags">
{''.join(f'<span class="tag">{html_escape(tag)}</span>' for tag in tags)}
</div>
</div>
<div class="tbl-wrap">
<div class="tbl-bar"><span class="tbl-lbl">Asset breakdown</span></div>
<div class="tbl-scroll">
<table>
<thead><tr>{''.join(f'<th>{html_escape(HTML_LABELS.get(col, col))}</th>' for col in table.columns)}</tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</div>
</div>
<div class="footer"><span>generated {generated}</span></div>
</main>
</body>
</html>
"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{args.strategy}_results_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    return path
