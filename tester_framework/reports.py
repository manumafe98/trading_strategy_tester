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
FINANCIAL_COLUMNS = {"Return", "Max DD", "Sharpe Ratio", "Return / DD"}
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


def write_trade_html(data: pd.DataFrame, trade: dict, strategy: types.ModuleType) -> Path:
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
    return path


def format_metric(column: str, value) -> str:
    if isinstance(value, (tuple, list)):
        return " / ".join(format_metric(column, item) for item in value)
    if column in {"Return", "Max DD"}:
        prefix = "+" if column == "Return" and float(value) > 0 else ""
        return f"{prefix}{float(value):.2f}%"
    if column in {"Return / DD", "Sharpe Ratio"}:
        return f"{float(value):.2f}"
    if column == "Win Rate":
        return f"{float(value):.2f}%"
    if column.endswith(" R"):
        return f"{float(value):+.2f}R"
    return str(value)


def metric_class(column: str, value) -> str:
    if isinstance(value, (tuple, list)):
        value = value[-1]
    if column == "Return":
        return "good" if float(value) > 0 else "bad" if float(value) < 0 else "neutral"
    if column == "Max DD":
        return "warn"
    if column == "Expectancy R":
        return "good" if float(value) > 0 else "bad" if float(value) < 0 else "neutral"
    if column == "W":
        return "good"
    if column == "L":
        return "bad"
    if column == "Return / DD":
        return "neutral"
    return "plain"


def _detail_table(headers: list[str], rows: list[list[object]], extra_class: str = "") -> str:
    if not rows:
        return '<p class="empty">No trades</p>'
    head = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    body = "\n".join("<tr>" + "".join(f"<td>{html_escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    return f'<div class="detail-scroll"><table class="detail-table {extra_class}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _r(value) -> str:
    return f"{float(value):+.2f}R"


def _outcome_table(analytics: dict) -> str:
    rows = []
    for stats in analytics["outcomes"]:
        rows.append(
            [
                stats["Group"],
                stats["Trades"],
                stats["Wins"],
                stats["BE"],
                stats["Losses"],
                f'{stats["Win Rate"]:.2f}%',
                _r(stats["Avg Win R"]),
                _r(stats["Avg Loss R"]),
                _r(stats["Expectancy R"]),
                stats["Max Losing Streak"],
                stats["Avg Duration"],
                stats["Median Duration"],
            ]
        )
    headers = ["Group", "Trades", "W", "BE", "L", "Win rate", "Avg win", "Avg loss", "Expectancy", "Max losing streak", "Avg duration", "Median duration"]
    return _detail_table(headers, rows)


def _period_table(rows: list[dict]) -> str:
    return _detail_table(
        ["Period", "Trades", "W", "BE", "L", "Win rate"],
        [[row["Period"], row["Trades"], row["Wins"], row["BE"], row["Losses"], f'{row["Win Rate"]:.2f}%'] for row in rows],
    )


def _trailing_section(analytics: dict) -> str:
    trailing = analytics["trailing"]
    summary = _detail_table(
        ["Target hits", "Early exits", "Avg realized", "Avg MFE", "Avg giveback"],
        [[trailing["Target Hits"], trailing["Early Exits"], _r(trailing["Avg Realized R"]), _r(trailing["Avg MFE R"]), _r(trailing["Avg Giveback R"])]],
    )
    ledger = []
    for trade in trailing["trades"]:
        chart_path = trade.get("chart_path")
        chart = "-"
        if chart_path:
            name = html_escape(Path(chart_path).name, quote=True)
            chart = f'<a href="../trades/{name}" target="_blank" rel="noopener">chart</a>'
        ledger.append(
            "<tr>"
            f"<td>{chart}</td>"
            f'<td>{html_escape(pd.Timestamp(trade["entry_time"]).strftime("%Y-%m-%d %H:%M"))}</td>'
            f'<td>{html_escape(pd.Timestamp(trade["exit_time"]).strftime("%Y-%m-%d %H:%M"))}</td>'
            f'<td>{html_escape(str(trade["holding_duration"]))}</td>'
            f'<td>{html_escape(str(trade["side"]))}</td>'
            f'<td>{html_escape(str(trade["outcome"]).replace("_", " ").title())}</td>'
            f'<td>{html_escape("Target hit" if trade["exit_reason"] == "target" else "Early stop")}</td>'
            f'<td>{float(trade["risk_reward_ratio"]):g}R</td>'
            f'<td>{_r(trade["realized_r"])}</td>'
            f'<td>{_r(trade["mfe_r"])}</td>'
            f'<td>{_r(trade["giveback_r"])}</td>'
            "</tr>"
        )
    headers = ["Trade", "Entry UTC", "Exit UTC", "Duration", "Side", "Outcome", "Exit", "Target", "Realized", "MFE", "Giveback"]
    if ledger:
        head = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
        ledger_html = f'<div class="detail-scroll"><table class="detail-table ledger"><thead><tr>{head}</tr></thead><tbody>{"".join(ledger)}</tbody></table></div>'
    else:
        ledger_html = '<p class="empty">No trades</p>'
    return f'<section><h3>Trailing summary</h3>{summary}</section><section><h3>Trailing trades</h3>{ledger_html}</section>'


def _variant_details(row: pd.Series) -> str:
    analytics = row["_analytics"]
    overall = analytics["outcomes"][0]
    sections = [
        f"<section><h3>Outcomes</h3>{_outcome_table(analytics)}</section>",
        f'<div class="detail-grid"><section><h3>Entry day UTC</h3>{_period_table(analytics["daily"])}</section><section><h3>Entry month UTC</h3>{_period_table(analytics["monthly"])}</section></div>',
    ]
    if row["Trailing"] == "yes":
        sections.append(_trailing_section(analytics))
    custom_metrics = row.get("_strategy_metrics", {})
    if custom_metrics:
        sections.append(f'<section><h3>Strategy metrics</h3>{_detail_table(["Metric", "Value"], [[name, value] for name, value in custom_metrics.items()])}</section>')
    title = f'{row["Asset"]} {row["TF"]} | {row["RR"]}R | trailing {row["Trailing"]}'
    result = f'{overall["Wins"]}W / {overall["BE"]}BE / {overall["Losses"]}L | {_r(overall["Expectancy R"])}'
    return f'<details class="variant"><summary><strong>{html_escape(title)}</strong><span>{html_escape(result)}</span></summary><div class="variant-body">{"".join(sections)}</div></details>'


def write_results_html(table: pd.DataFrame, args, columns: list[str] | None = None) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    columns = columns or [column for column in table.columns if not column.startswith("_") and column not in {"Gross", "Net"}]
    rows = []
    asset_colors = {}
    for _, row in table.iterrows():
        cells = []
        asset = str(row["Asset"])
        if asset not in asset_colors:
            asset_colors[asset] = ASSET_COLORS[len(asset_colors) % len(ASSET_COLORS)]
        color = asset_colors[asset]
        for col in columns:
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
    details = "\n".join(_variant_details(row) for _, row in table.iterrows())
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
main {{ max-width: 1540px; margin: 0 auto; padding: 52px 52px 72px; }}
.hdr {{ margin-bottom: 40px; padding-bottom: 36px; border-bottom: 1px solid var(--border); }}
.hdr-title {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 30px; font-weight: 600; color: var(--text); margin-bottom: 6px; }}
.hdr-title em {{ color: var(--gold); font-style: normal; }}
.hdr-sep {{ color: var(--dim); margin: 0 10px; font-weight: 400; }}
.hdr-sub {{ font-size: 14px; color: var(--sub); margin-bottom: 16px; }}
.tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.tag {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 11px; color: var(--sub); background: var(--surface2); border: 1px solid var(--border2); border-radius: 5px; padding: 4px 10px; }}
.tbl-wrap {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
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
.pill {{ display: inline-flex; align-items: center; justify-content: center; font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 13px; font-weight: 600; padding: 7px 10px; border-radius: 7px; min-width: 62px; letter-spacing: 0; }}
.good {{ background: var(--green-bg); color: #38d888; border: 1px solid var(--green-bdr); }}
.bad {{ background: rgba(196,56,72,.12); color: #e06878; border: 1px solid rgba(196,56,72,.28); }}
.warn {{ background: var(--amber-bg); color: #dfaa38; border: 1px solid var(--amber-bdr); }}
.neutral, .plain {{ background: var(--neutral-bg); color: var(--sub); border: 1px solid var(--neutral-bdr); }}
.pair-label {{ display: block; margin-top: 2px; font-size: 8px; color: var(--sub); letter-spacing: 0; text-transform: none; }}
.details-wrap {{ margin-top: 34px; border-top: 1px solid var(--border2); }}
.variant {{ border-bottom: 1px solid var(--border2); }}
.variant summary {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 18px 4px; cursor: pointer; color: var(--text); }}
.variant summary strong {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 14px; }}
.variant summary span {{ font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 12px; color: var(--sub); }}
.variant-body {{ padding: 4px 0 30px; }}
.variant-body section {{ margin-top: 24px; }}
.variant-body h3 {{ margin-bottom: 9px; font-size: 11px; font-weight: 600; letter-spacing: .8px; text-transform: uppercase; color: var(--dim); }}
.detail-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }}
.detail-scroll {{ overflow-x: auto; border-top: 1px solid var(--border); }}
.detail-table {{ min-width: 720px; background: transparent; }}
.detail-table.ledger {{ min-width: 1050px; }}
.detail-table th {{ padding: 9px 12px; text-align: right; background: var(--surface); }}
.detail-table th:first-child {{ text-align: left; }}
.detail-table td {{ padding: 10px 12px; text-align: right; font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 12px; color: var(--sub); }}
.detail-table td:first-child {{ text-align: left; color: var(--text); }}
.detail-table a {{ color: var(--gold); text-decoration: none; }}
.detail-table a:hover {{ text-decoration: underline; }}
.empty {{ padding: 12px 0; color: var(--dim); font-size: 12px; }}
.footer {{ margin-top: 28px; display: flex; justify-content: flex-end; font-family: Consolas, 'Cascadia Mono', ui-monospace, monospace; font-size: 11px; color: var(--dim); }}
@media (max-width: 900px) {{ .detail-grid {{ grid-template-columns: 1fr; gap: 0; }} }}
@media (max-width: 720px) {{ main {{ padding: 32px 18px 48px; }} .hdr-title {{ font-size: 24px; }} .variant summary {{ align-items: flex-start; flex-direction: column; gap: 5px; }} }}
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
<thead><tr>{''.join(f'<th>{html_escape(HTML_LABELS.get(col, col))}{f"<span class=pair-label>Gross / Net</span>" if args.with_costs and col in FINANCIAL_COLUMNS else ""}</th>' for col in columns)}</tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</div>
</div>
<section class="details-wrap">
{details}
</section>
<div class="footer"><span>generated {generated}</span></div>
</main>
</body>
</html>
"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{args.strategy}_results_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    return path
