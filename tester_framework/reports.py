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

from .analytics import decode_analytics
from .cli import DAY_NAMES, MONTH_NAMES, RunConfig
from .models import Side
from .settings import COST_PAIR_COLUMNS, FINANCIAL_COLUMNS, RESULTS_DIR, ROOT, TIMEFRAMES, TRADES_DIR
from .utils import clean_exit_name


MetricValue: TypeAlias = float | int | tuple[float | int, ...] | list[float | int] | str


ASSET_COLORS = ["#f9c22e", "#ff922b", "#4c8bf5", "#5cc9c8", "#b197fc", "#69db7c"]
HTML_LABELS = {"Sharpe Ratio": "Sharpe", "Return / DD": "Ret / DD"}
FILTER_SPECS = (
    ("strategy", "Strategy", "Strategy"),
    ("asset", "Asset", "Asset"),
    ("timeframe", "Timeframe", "TF"),
    ("session", "Session", "Session"),
    ("rr", "RR", "RR"),
    ("exit-mode", "Exit Mode", "Exit Mode"),
    ("risk", "Risk", "_risk_pct"),
)
FILTER_ORDERS = {
    "timeframe": {value: index for index, value in enumerate(TIMEFRAMES)},
    "session": {value: index for index, value in enumerate(("asia", "london", "ny"))},
    "exit-mode": {value: index for index, value in enumerate(("fixed", "trailing", "partial"))},
}
RESULTS_TEMPLATE = Path(__file__).resolve().parent / "templates" / "results.html"
VARIANT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "variant.html"
TRADE_ROWS_PER_PAGE = 1_000
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
    title = f"{trade['strategy']} {trade['asset']} {trade['timeframe']}"
    if trade.get("session"):
        title = f"{title} {trade['session']}"
    title = f"{title} {trade['side']} {trade['exit_mode']}"
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, xaxis_title="time (UTC)")

    stamp = pd.Timestamp(trade["entry_time"]).strftime("%Y-%m-%d_%H%M")
    rr = clean_exit_name(f"{trade['risk_reward_ratio']:g}RR")
    session = clean_exit_name(str(trade["session"])) if trade.get("session") else None
    parts = [trade["strategy"], trade["asset"], trade["timeframe"]]
    if session:
        parts.append(session)
    parts.extend([rr, trade["exit_mode"], stamp, str(trade["side"])])
    base = "_".join(parts)
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


def _managed_summary(analytics: dict) -> str:
    managed = analytics["managed"]
    mode = str(managed["Mode"]).title()
    summary = _detail_table(
        ["Target completions", "Stop completions", "Avg realized", "Avg MFE", "Avg giveback"],
        [[managed["Target Completions"], managed["Stop Completions"], _r(managed["Avg Realized R"]), _r(managed["Avg MFE R"]), _r(managed["Avg Giveback R"])]],
    )
    return f'<section><h2>{mode} summary</h2>{summary}</section>'


def _trade_row(trade: dict) -> str:
    chart_path = trade.get("chart_path")
    chart = "-"
    if chart_path:
        name = html_escape(Path(chart_path).name, quote=True)
        chart = f'<a href="../../../trades/{name}" target="_blank" rel="noopener">chart</a>'
    return (
        '<tr class="trade-row">'
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


def _trade_table(trades: list[dict]) -> str:
    if not trades:
        return '<p class="empty">No trades</p>'
    headers = [
        "Trade",
        "Entry UTC",
        "Exit UTC",
        "Duration",
        "Side",
        "Outcome",
        "Exit path",
        "Target",
        "Realized",
        "MFE",
        "Giveback",
    ]
    head = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    rows = "".join(_trade_row(trade) for trade in trades)
    return (
        '<div class="detail-scroll"><table class="detail-table ledger">'
        f"<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>"
    )


def _variant_sections(row: pd.Series, analytics: dict) -> str:
    sections = [
        f"<section><h2>Outcomes</h2>{_outcome_table(analytics)}</section>",
        f'<div class="detail-grid"><section><h2>Entry weekday (UTC)</h2>{_period_table(analytics["weekday"])}</section><section><h2>Entry month (UTC)</h2>{_period_table(analytics["month"])}</section></div>',
        f'<section><h2>Entry year (UTC)</h2>{_period_table(analytics["year"])}</section>',
    ]
    if row["Exit Mode"] != "fixed":
        sections.append(_managed_summary(analytics))
    custom_metrics = row.get("_strategy_metrics", {})
    if custom_metrics:
        sections.append(f'<section><h2>Strategy metrics</h2>{_detail_table(["Metric", "Value"], [[name, value] for name, value in custom_metrics.items()])}</section>')
    return "".join(sections)


def _variant_filename(variant_id: int, page: int = 1) -> str:
    stem = f"{variant_id:06d}"
    return f"{stem}.html" if page == 1 else f"{stem}-p{page}.html"


def _pagination(variant_id: int, page: int, page_count: int, start: int, stop: int, total: int) -> str:
    previous = (
        f'<a rel="prev" href="{_variant_filename(variant_id, page - 1)}">Previous</a>' if page > 1 else "<span></span>"
    )
    next_page = (
        f'<a rel="next" href="{_variant_filename(variant_id, page + 1)}">Next</a>'
        if page < page_count
        else "<span></span>"
    )
    range_text = f"trades {start + 1}-{stop} of {total}" if total else "0 trades"
    return (
        f'<nav class="pagination">{previous}<span>Page {page} of {page_count} | '
        f"{range_text}</span>{next_page}</nav>"
    )


def _write_variant_pages(
    row: pd.Series,
    variant_id: int,
    variants_dir: Path,
    template: Template,
    generated: str,
) -> None:
    analytics = decode_analytics(row["_analytics"])
    overall = analytics["outcomes"][0]
    session = row.get("Session")
    session_text = f' {session}' if pd.notna(session) else ""
    title = f'{row["Strategy"]} {row["Asset"]} {row["TF"]}{session_text} | {row["RR"]}R | {row["Exit Mode"]}'
    result = f'{overall["Wins"]}W / {overall["BE"]}BE / {overall["Losses"]}L | {_r(overall["Expectancy R"])}'
    trades = analytics["managed"]["trades"] if row["Exit Mode"] != "fixed" else []
    page_count = max(1, (len(trades) + TRADE_ROWS_PER_PAGE - 1) // TRADE_ROWS_PER_PAGE)
    for page in range(1, page_count + 1):
        start = (page - 1) * TRADE_ROWS_PER_PAGE
        stop = min(start + TRADE_ROWS_PER_PAGE, len(trades))
        page_trades = trades[start:stop]
        pagination = (
            _pagination(variant_id, page, page_count, start, stop, len(trades))
            if row["Exit Mode"] != "fixed"
            else ""
        )
        trade_section = ""
        if row["Exit Mode"] != "fixed":
            mode = html_escape(str(analytics["managed"]["Mode"]).title())
            trade_section = f'<section><h2>{mode} trades</h2>{_trade_table(page_trades)}</section>'
        html = template.substitute(
            title=html_escape(title),
            result=html_escape(result),
            page_note=f"Trade page {page} of {page_count}" if page_count > 1 else "Variant details",
            sections=_variant_sections(row, analytics) if page == 1 else "",
            trade_section=trade_section,
            pagination=pagination,
            generated=generated,
        )
        (variants_dir / _variant_filename(variant_id, page)).write_text(html, encoding="utf-8")


def _filter_text(column: str, value) -> str:
    if value is None or (not isinstance(value, (tuple, list)) and pd.isna(value)):
        return "None"
    if column == "_risk_pct":
        return f"{float(value):g}%"
    return str(value)


def _filter_attrs(row: pd.Series) -> str:
    attrs = []
    for key, _, column in FILTER_SPECS:
        value = _filter_text(column, row.get(column))
        attrs.append(f'data-{key}="{html_escape(value, quote=True)}"')
    return " ".join(attrs)


def _filter_sort_key(key: str, value: str) -> tuple:
    if key in {"rr", "risk"}:
        return 0, float(value.removesuffix("%"))
    order = FILTER_ORDERS.get(key, {})
    base = value.partition("=")[0]
    return order.get(base, len(order)), value.casefold()


def _render_filters(table: pd.DataFrame) -> str:
    menus = []
    for key, label, column in FILTER_SPECS:
        if column not in table:
            continue
        values = sorted(
            dict.fromkeys(_filter_text(column, value) for value in table[column]),
            key=lambda value: _filter_sort_key(key, value),
        )
        if len(values) < 2:
            continue
        options = []
        for index, value in enumerate(values):
            safe_value = html_escape(value, quote=True)
            option_id = f"filter-{key}-{index}"
            options.append(
                f'<label class="filter-option" for="{option_id}"><input id="{option_id}" type="checkbox" '
                f'data-filter-key="{key}" value="{safe_value}" checked><span>{html_escape(value)}</span></label>'
            )
        count = len(values)
        menus.append(
            f'<details class="filter-menu" data-filter-menu="{key}"><summary><span>{html_escape(label)}</span>'
            f'<span class="filter-selection">{count}/{count}</span></summary><div class="filter-options">'
            f'{"".join(options)}</div></details>'
        )
    if not menus:
        return ""
    count = len(table)
    return (
        '<section class="filter-bar" id="filter-bar" aria-label="Variant filters">'
        f'<div class="filter-menus">{"".join(menus)}</div><div class="filter-status">'
        f'<span id="filter-match-count" aria-live="polite">{count} of {count} variants</span>'
        '<button type="button" id="reset-filters">Reset filters</button></div></section>'
    )


def _render_header(column: str, with_costs: bool) -> str:
    label = html_escape(HTML_LABELS.get(column, column))
    suffix = '<span class="pair-label">Gross / Net</span>' if with_costs and column in COST_PAIR_COLUMNS else ""
    if column in FINANCIAL_COLUMNS:
        return f'<th aria-sort="none"><button type="button" class="sort-button">{label}{suffix}</button></th>'
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
    sort_attr = ""
    if col in FINANCIAL_COLUMNS:
        sort_value = value[-1] if isinstance(value, (tuple, list)) else value
        sort_attr = f' data-sort-value="{float(sort_value)}"' if math.isfinite(float(sort_value)) else ' data-sort-value=""'
    return f'<td class="c"{sort_attr}><span class="pill {metric_class(col, value)}">{html_escape(format_metric(col, value))}</span></td>'


def write_results_html(table: pd.DataFrame, config: RunConfig, columns: list[str] | None = None) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    columns = columns or [column for column in table.columns if not column.startswith("_") and column not in {"Gross", "Net"}]
    columns = [column for column in ("Strategy", "Asset", "TF", "Session", "RR", "Exit Mode", *FINANCIAL_COLUMNS) if column in columns]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "_".join(config.strategies)
    bundle_dir = RESULTS_DIR / f"{safe_name}_results_{stamp}"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    variants_dir = bundle_dir / "variants"
    variants_dir.mkdir(parents=True)
    variant_template = Template(VARIANT_TEMPLATE.read_text(encoding="utf-8"))
    rows = []
    asset_colors = {}
    for original_order, (_, row) in enumerate(table.iterrows()):
        asset = str(row["Asset"])
        if asset not in asset_colors:
            asset_colors[asset] = ASSET_COLORS[len(asset_colors) % len(ASSET_COLORS)]
        color = asset_colors[asset]
        cells = [_render_cell(col, row[col], color) for col in columns]
        details = (
            f'<td class="c"><a class="details-link" href="variants/{_variant_filename(original_order)}" '
            'target="_blank" rel="noopener">View</a></td>'
        )
        rows.append(
            f'<tr data-variant-id="{original_order}" data-original-order="{original_order}" {_filter_attrs(row)}>'
            + "".join(cells)
            + details
            + "</tr>"
        )
        _write_variant_pages(row, original_order, variants_dir, variant_template, generated)
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
    if config.sessions and config.sessions.lower() != "none":
        tags.append(f'<span class="tag">sessions {html_escape(config.sessions)}</span>')
    if config.days:
        tags.append(f'<span class="tag">days {html_escape(", ".join(DAY_NAMES[day] for day in sorted(config.days)))}</span>')
    if config.months:
        tags.append(
            f'<span class="tag">months {html_escape(", ".join(MONTH_NAMES[month - 1] for month in sorted(config.months)))}</span>'
        )
    if config.max_trades is not None:
        tags.append(f'<span class="tag">first {config.max_trades} closed trades per variant</span>')
    if config.trade_html == 0:
        tags.append('<span class="tag">trade charts off</span>')
    elif config.trade_html is not None:
        tags.append(f'<span class="tag">first {config.trade_html} trade charts per variant</span>')
    strategy_label = ", ".join(config.strategies)
    template = Template(RESULTS_TEMPLATE.read_text(encoding="utf-8"))
    html = template.substitute(
        title=f"{html_escape(strategy_label)} results",
        strategy=html_escape(strategy_label),
        tags="\n".join(tags),
        filters=_render_filters(table),
        columns="".join(_render_header(col, config.with_costs) for col in columns) + "<th>Details</th>",
        rows="\n".join(rows),
        generated=generated,
    )
    path = bundle_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path
