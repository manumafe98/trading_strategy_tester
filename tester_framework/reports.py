from __future__ import annotations

import math
import shutil
import types
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from string import Template
from typing import TypeAlias

import pandas as pd

from .cli import RunConfig
from .models import Side
from .settings import COST_PAIR_COLUMNS, FINANCIAL_COLUMNS, RESULTS_DIR, ROOT, TRADES_DIR
from .utils import clean_exit_name


MetricValue: TypeAlias = float | int | tuple[float | int, ...] | list[float | int] | str


ASSET_COLORS = ["#f9c22e", "#ff922b", "#4c8bf5", "#5cc9c8", "#b197fc", "#69db7c"]
HTML_LABELS = {"Sharpe Ratio": "Sharpe", "Return / DD": "Ret / DD"}
RESULTS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "results.html"
TRADE_CHART_BEFORE_BARS = 20
TRADE_CHART_AFTER_BARS = 20
__all__ = ["format_metric", "metric_class", "reset_output_dirs", "write_results_html", "write_trade_html"]


def reset_output_dirs() -> None:
    root = ROOT.resolve()
    for folder in (RESULTS_DIR, TRADES_DIR):
        resolved = folder.resolve()
        if root not in (resolved, *resolved.parents):
            raise RuntimeError(f"Refusing to clear outside workspace: {resolved}")
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)


def write_trade_html(data: pd.DataFrame, trade: dict, strategy: types.ModuleType) -> Path | None:
    # ponytail: keep plotly lazy so tests avoid chart dependencies.
    import plotly.graph_objects as go

    TRADES_DIR.mkdir(exist_ok=True)
    start = max(0, trade["entry_i"] - TRADE_CHART_BEFORE_BARS)
    if pd.notna(trade.get("plot_start_time")):
        plot_i = data.index.searchsorted(pd.Timestamp(trade["plot_start_time"]), side="left")
        start = min(max(0, plot_i), trade["entry_i"])
    end = min(len(data), trade["exit_i"] + TRADE_CHART_AFTER_BARS + 1)
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
        plot_indicators(
            fig,
            data=data,
            view=view,
            asset=trade["asset"],
            timeframe=trade["timeframe"],
            params={"trade": trade, "tick_size": trade["tick_size"]},
        )
    color = "green" if trade["side"] == Side.LONG else "red"
    fig.add_trace(go.Scatter(x=[trade["entry_time"]], y=[trade["entry"]], mode="markers", name="entry", marker={"color": color, "size": 10}))
    for fill in trade["exits"]:
        name = f'{fill["exit_reason"]} {fill["qty"]:g}'
        fig.add_trace(go.Scatter(x=[fill["exit_time"]], y=[fill["exit"]], mode="markers", name=name, marker={"color": "black", "size": 10}))
    fig.add_hline(y=trade["stop"], line_dash="dot", line_color="red", annotation_text="stop")
    for target in trade["targets"]:
        fig.add_hline(y=target["price"], line_dash="dot", line_color="green", annotation_text=f'{target["r"]:g}R target')
    fig.update_layout(title=f"{trade['strategy']} {trade['asset']} {trade['timeframe']} {trade['side']} {trade['exit_mode']}", xaxis_rangeslider_visible=False, xaxis_title="time (UTC)")

    stamp = pd.Timestamp(trade["entry_time"]).strftime("%Y-%m-%d_%H%M")
    rr = clean_exit_name(f"{trade['risk_reward_ratio']:g}RR")
    base = f"{trade['strategy']}_{trade['asset']}_{trade['timeframe']}_{rr}_{trade['exit_mode']}_{stamp}_{trade['side']}"
    path = TRADES_DIR / f"{base}.html"
    n = 2
    while path.exists():
        path = TRADES_DIR / f"{base}_{n}.html"
        n += 1
    try:
        fig.write_html(path, include_plotlyjs="cdn")
    except Exception as exc:
        print(f"warning: could not write trade chart {path}: {exc}")
        return None
    return path


def format_metric(column: str, value: MetricValue) -> str:
    if isinstance(value, (tuple, list)):
        return " / ".join(format_metric(column, item) for item in value)
    if isinstance(value, (float, int)) and not math.isfinite(float(value)):
        return "N/A"
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


def metric_class(column: str, value: MetricValue) -> str:
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


def _exit_path(trade: dict) -> str:
    parts = []
    for fill in trade["exits"]:
        label = f'{fill["target_r"]:g}R' if fill["target_r"] is not None else f'{fill["realized_r"]:+.2f}R stop'
        parts.append(f'{fill["qty"]:g} @ {label}')
    return ", ".join(parts)


def _managed_section(analytics: dict) -> str:
    managed = analytics["managed"]
    mode = str(managed["Mode"]).title()
    summary = _detail_table(
        ["Target completions", "Stop completions", "Avg realized", "Avg MFE", "Avg giveback"],
        [[managed["Target Completions"], managed["Stop Completions"], _r(managed["Avg Realized R"]), _r(managed["Avg MFE R"]), _r(managed["Avg Giveback R"])]],
    )
    ledger = []
    for trade in managed["trades"]:
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
            f'<td>{html_escape(_exit_path(trade))}</td>'
            f'<td>{float(trade["risk_reward_ratio"]):g}R</td>'
            f'<td>{_r(trade["realized_r"])}</td>'
            f'<td>{_r(trade["mfe_r"])}</td>'
            f'<td>{_r(trade["giveback_r"])}</td>'
            "</tr>"
        )
    headers = ["Trade", "Entry UTC", "Exit UTC", "Duration", "Side", "Outcome", "Exit path", "Target", "Realized", "MFE", "Giveback"]
    if ledger:
        head = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
        ledger_html = f'<div class="detail-scroll"><table class="detail-table ledger"><thead><tr>{head}</tr></thead><tbody>{"".join(ledger)}</tbody></table></div>'
    else:
        ledger_html = '<p class="empty">No trades</p>'
    return f'<section><h3>{mode} summary</h3>{summary}</section><section><h3>{mode} trades</h3>{ledger_html}</section>'


def _variant_details(row: pd.Series) -> str:
    analytics = row["_analytics"]
    overall = analytics["outcomes"][0]
    sections = [
        f"<section><h3>Outcomes</h3>{_outcome_table(analytics)}</section>",
        f'<div class="detail-grid"><section><h3>Entry weekday (UTC)</h3>{_period_table(analytics["weekday"])}</section><section><h3>Entry month (UTC)</h3>{_period_table(analytics["month"])}</section></div>',
    ]
    if row["Exit Mode"] != "fixed":
        sections.append(_managed_section(analytics))
    custom_metrics = row.get("_strategy_metrics", {})
    if custom_metrics:
        sections.append(f'<section><h3>Strategy metrics</h3>{_detail_table(["Metric", "Value"], [[name, value] for name, value in custom_metrics.items()])}</section>')
    title = f'{row["Strategy"]} {row["Asset"]} {row["TF"]} | {row["RR"]}R | {row["Exit Mode"]}'
    result = f'{overall["Wins"]}W / {overall["BE"]}BE / {overall["Losses"]}L | {_r(overall["Expectancy R"])}'
    return f'<details class="variant"><summary><strong>{html_escape(title)}</strong><span>{html_escape(result)}</span></summary><div class="variant-body">{"".join(sections)}</div></details>'


def _render_header(column: str, with_costs: bool) -> str:
    label = html_escape(HTML_LABELS.get(column, column))
    suffix = '<span class="pair-label">Gross / Net</span>' if with_costs and column in COST_PAIR_COLUMNS else ""
    return f"<th>{label}{suffix}</th>"


def _render_cell(col: str, value, color: str) -> str:
    if col == "Strategy":
        return f'<td class="c"><span class="strategy-badge">{html_escape(str(value))}</span></td>'
    if col == "Asset":
        return f'<td><div class="asset-cell"><span class="asset-mark" style="background:{color}"></span><span class="asset" style="color:{color}">{html_escape(str(value))}</span></div></td>'
    if col == "TF":
        return f'<td class="c"><span class="tf-badge">{html_escape(str(value))}</span></td>'
    if col == "Long":
        return f'<td class="c"><span class="dir long">+ {html_escape(str(value))}</span></td>'
    if col == "Short":
        return f'<td class="c"><span class="dir short">- {html_escape(str(value))}</span></td>'
    if col == "Trades":
        return f'<td class="c n">{html_escape(str(value))}</td>'
    return f'<td class="c"><span class="pill {metric_class(col, value)}">{html_escape(format_metric(col, value))}</span></td>'


def write_results_html(table: pd.DataFrame, config: RunConfig, columns: list[str] | None = None) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    columns = columns or [column for column in table.columns if not column.startswith("_") and column not in {"Gross", "Net"}]
    columns = [column for column in ("Strategy", "Asset", "TF", "RR", "Exit Mode", *FINANCIAL_COLUMNS) if column in columns]
    rows = []
    asset_colors = {}
    for _, row in table.iterrows():
        asset = str(row["Asset"])
        if asset not in asset_colors:
            asset_colors[asset] = ASSET_COLORS[len(asset_colors) % len(ASSET_COLORS)]
        color = asset_colors[asset]
        cells = [_render_cell(col, row[col], color) for col in columns]
        rows.append("<tr>" + "".join(cells) + "</tr>")

    details = "\n".join(_variant_details(row) for _, row in table.iterrows())
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tags = [
        f'<span class="tag">{html_escape(tag)}</span>'
        for tag in (
            f"{config.time_period} period",
            f"{config.operation.replace('_', ' ')} operations",
            f"RR {config.risk_reward_ratio}",
            f"exit modes {config.exit_mode}",
            f"{config.risk}% risk",
            f"${config.capital:,.0f} capital",
            f"costs {'on' if config.with_costs else 'off'}",
        )
    ]
    if config.max_trades is not None:
        tags.append(f'<span class="tag">first {config.max_trades} closed trades per variant</span>')
    if not config.trade_html:
        tags.append('<span class="tag">trade charts off</span>')
    strategy_label = ", ".join(config.strategies)
    template = Template(RESULTS_TEMPLATE.read_text(encoding="utf-8"))
    html = template.substitute(
        title=f"{html_escape(strategy_label)} results",
        strategy=html_escape(strategy_label),
        tags="\n".join(tags),
        columns="".join(_render_header(col, config.with_costs) for col in columns),
        rows="\n".join(rows),
        details=details,
        generated=generated,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "_".join(config.strategies)
    path = RESULTS_DIR / f"{safe_name}_results_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    return path
