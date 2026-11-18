"""
=============================================================================
 MOMO Professional CLI — Stock-first, ETF-aware
=============================================================================

Main examples:
    python main.py AAPL
    python main.py --etf VOO

Other examples:
    python main.py AAPL MSFT NVDA --compare
    python main.py --etf VOO QQQ SCHD --compare
    python main.py AAPL --timeframes 1m,3m,6m,12m
    python main.py AAPL --visual
    python main.py AAPL --no-price                 # fundamental-only
    python main.py AAPL --json --output-dir output
    python main.py --etf VOO --sections overview,holdings,dividends

The default asset is a stock. The ``--etf`` flag explicitly enables the
legacy ETF pipeline. The CLI uses Typer for its interface and Rich for all
terminal presentation.
"""

from __future__ import annotations

import json
import logging
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import typer
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.layout import Layout
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import momo as momo_module
from momo import (
    Momo,
    MomoConfig,
    MomoError,
    MomoValidationError,
    STOCK_SECTION_ROUTES,
    assemble_etf_data,
    enable_logging,
    __version__ as MOMO_VERSION,
)

try:
    from momo.engine import __version__ as TECHNICAL_VERSION
    from momo.engine import fetch_yahoo_history
except ImportError:  # MOMO fundamental-only mode remains available
    TECHNICAL_VERSION = "unavailable"
    fetch_yahoo_history = None  # type: ignore[assignment]



from .common import *
from .common import (
    _company_label,
    _deep_get,
    _display,
    _json_dump,
    _money,
    _num,
    _parse_csv,
    _pct,
    _print_adaptive_pair,
    _score,
    _style_for_number,
    _style_for_score,
    _style_for_stance,
)
from .visual import visual_filename, write_stock_visual_report

def _analysis_via_momo(
    client: Momo,
    stock_data: Mapping[str, Any],
    price_data: Any,
    period: str,
    interval: str,
    auto_fetch_price: bool,
    timeframes: Sequence[str],
) -> Dict[str, Any]:
    """Run the integrated stock report using the configured Momo client."""
    return client.analyze_stock_data(
        stock_data,
        price_data=price_data,
        period=period,
        interval=interval,
        auto_fetch_price=auto_fetch_price,
        timeframes=timeframes,
    )


def _render_bullets(items: Sequence[Any], *, empty: str = "No material points were identified.") -> Text:
    text = Text()
    if not items:
        text.append(empty, style=PALETTE["muted"])
        return text
    for idx, item in enumerate(items):
        if idx:
            text.append("\n")
        text.append("• ", style="bold")
        text.append(_display(item))
    return text


def _stock_sections_from_option(raw: Optional[str]) -> Optional[List[str]]:
    requested = [x.lower() for x in _parse_csv(raw)]
    if not requested or "all" in requested:
        return None
    unknown = [x for x in requested if x not in STOCK_SECTION_ROUTES]
    if unknown:
        raise typer.BadParameter(
            f"Unknown stock section: {', '.join(unknown)}. Choices: {', '.join(STOCK_SECTION_ROUTES)}"
        )
    return requested


def _timeframes_from_option(raw: str) -> List[str]:
    requested = [x.lower() for x in _parse_csv(raw)]
    if not requested:
        return ["1m", "3m", "6m", "12m"]
    valid = {"1m", "3m", "6m", "12m"}
    unknown = [x for x in requested if x not in valid]
    if unknown:
        raise typer.BadParameter(f"Unknown timeframe: {', '.join(unknown)}. Use 1m,3m,6m,12m.")
    return requested


def render_business_context(analysis: Mapping[str, Any]) -> Optional[Panel]:
    ctx = _deep_get(analysis, "fundamental", "businessContext", default={}) or {}
    if not ctx:
        return None
    table = Table(show_header=True, header_style=f"bold {PALETTE['info']}", box=None, expand=True, padding=(0, 1))
    table.add_column("Metric")
    table.add_column("Reading", justify="right")
    rows = [
        ("Largest segment concentration", _pct(ctx.get("largestSegmentConcentrationPct"))),
        ("Weighted segment growth", _pct(ctx.get("weightedSegmentGrowthPct"), signed=True)),
    ]
    for label, value in rows:
        table.add_row(label, value)

    segments = ctx.get("latestSegmentMix") or []
    if segments:
        table.add_section()
        for item in segments[:5]:
            name = _display(item.get("name") or item.get("segment"))
            weight = item.get("mixPct")
            if weight is None:
                weight = item.get("weightPct") or item.get("sharePct") or item.get("weight")
            table.add_row(f"Segment: {name}", _pct(weight))
    return Panel(table, title="🧩 Business Mix", border_style="bright_blue", box=box.ROUNDED)


def render_catalysts_risks(analysis: Mapping[str, Any]) -> Tuple[Panel, Panel]:
    catalysts = Panel(
        _render_bullets(analysis.get("catalysts") or []),
        title="🚀 Catalysts",
        border_style=PALETTE["good"],
        box=box.ROUNDED,
    )
    risks = Panel(
        _render_bullets(analysis.get("risks") or []),
        title="⚠ Key Risks",
        border_style=PALETTE["bad"],
        box=box.ROUNDED,
    )
    return catalysts, risks


def render_data_quality(stock_data: Mapping[str, Any], analysis: Mapping[str, Any]) -> Panel:
    report = _deep_get(analysis, "professionalReport", "dataQuality", default={}) or {}
    context = analysis.get("dataContext") or {}
    rows = [
        ("Fundamental methods", ", ".join(report.get("fundamentalMethodsUsed") or []) or "—"),
        ("Fundamental confidence", f"{_score(report.get('fundamentalConfidence'))}/100"),
        ("Technical available", "yes" if report.get("technicalAvailable") else "no"),
        ("Technical datapoints", _display(report.get("technicalDataPoints"))),
        ("Timeframes", ", ".join(report.get("timeframesAnalyzed") or [])),
        ("Fundamental data coverage", _display(context.get("sectionCoveragePct"), "—")),
        ("Sections", str(len(context.get("sectionsAvailable") or _deep_get(stock_data, "_meta", "sectionsAvailable", default=[]) or []))),
    ]
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style=PALETTE["muted"])
    table.add_column()
    for label, value in rows:
        table.add_row(label, _display(value))
    return Panel(table, title="🔎 Data Quality", border_style="grey46", box=box.ROUNDED)


def render_errors(errors: Sequence[Mapping[str, Any]]) -> None:
    if not errors:
        return
    table = Table(show_header=True, header_style=f"bold {PALETTE['bad']}", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Section", width=24)
    table.add_column("Error")
    for err in errors:
        table.add_row(_display(err.get("section")), _display(err.get("error")))
    console.print(Panel(table, title="⚠ Source Errors", border_style=PALETTE["bad"], box=box.ROUNDED))


def render_executive_summary(analysis: Mapping[str, Any]) -> Panel:
    s = analysis.get("executiveSummary") or {}
    fv = analysis.get("fairValue") or {}
    fundamental = fv.get("fundamental") or {}
    stance = _display(s.get("stance"))
    stance_style = _style_for_stance(stance)

    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style=PALETTE["muted"], ratio=2)
    table.add_column(ratio=2)
    table.add_column(style=PALETTE["muted"], ratio=2)
    table.add_column(ratio=2)
    rows = [
        ("Overall stance", Text(stance, style=f"bold {stance_style}")),
        ("Overall score", Text(f"{_score(s.get('overallScore'))}/100", style=_style_for_score(s.get("overallScore")))),
        ("Current price", _money(s.get("currentPrice"))),
        ("Fundamental fair value", _money(s.get("fundamentalFairValue"))),
        ("Valuation", _display(s.get("fundamentalValuation"))),
        ("Business quality", _display(s.get("fundamentalQuality"))),
        ("Technical aggregate", f"{_score(s.get('technicalAggregateScore'))}/100"),
        ("FV confidence", f"{_score(fundamental.get('confidence'))}/100"),
    ]
    for idx in range(0, len(rows), 2):
        left_label, left_value = rows[idx]
        right_label, right_value = rows[idx + 1]
        table.add_row(
            left_label,
            left_value if isinstance(left_value, Text) else Text(str(left_value)),
            right_label,
            right_value if isinstance(right_value, Text) else Text(str(right_value)),
        )

    interpretation = _display(s.get("interpretation"), "")
    body: List[Any] = [table]
    if interpretation:
        note = Text()
        note.append("Model • ", style=f"bold {PALETTE['muted']}")
        note.append(interpretation, style=PALETTE["muted"])
        body.append(note)
    return Panel(Group(*body), title="🎯 Executive View", border_style=PALETTE["info"], box=box.ROUNDED)


def render_fair_value(analysis: Mapping[str, Any]) -> Panel:
    fundamental = analysis.get("fundamental") or {}
    methods = fundamental.get("methods") or {}
    consensus = fundamental.get("consensus") or {}
    current = _num(fundamental.get("currentPrice"))

    table = Table(show_header=True, header_style=f"bold {PALETTE['gold']}", box=None, expand=True, padding=(0, 1))
    table.add_column("Method", style="bold")
    table.add_column("Low", justify="right")
    table.add_column("Base", justify="right")
    table.add_column("High", justify="right")
    table.add_column("Upside", justify="right")
    table.add_column("Weight", justify="right")
    table.add_column("Confidence", justify="right")

    method_names = {
        "dcf": "DCF",
        "earningsMultiple": "Forward Earnings",
        "fcfMultiple": "FCF Multiple",
        "analystConsensus": "Analyst Consensus",
    }
    for key, item in methods.items():
        base = _num(item.get("base"))
        upside = ((base / current - 1) * 100) if base is not None and current else None
        table.add_row(
            method_names.get(key, str(key)),
            _money(item.get("low")),
            _money(item.get("base")),
            _money(item.get("high")),
            Text(_pct(upside, signed=True), style=_style_for_number(upside)),
            _pct((_num(item.get("weight")) or 0) * 100),
            f"{_score(item.get('confidence'))}/100",
        )

    if consensus:
        table.add_section()
        table.add_row(
            "CONSENSUS",
            _money(consensus.get("low")),
            Text(_money(consensus.get("base")), style=f"bold {PALETTE['good'] if (_num(consensus.get('upsideToBasePct')) or 0) >= 0 else PALETTE['bad']}"),
            _money(consensus.get("high")),
            Text(_pct(consensus.get("upsideToBasePct"), signed=True), style=_style_for_number(consensus.get("upsideToBasePct"))),
            "100%",
            f"{_score(consensus.get('confidence'))}/100",
        )

    subtitle = ""
    body: List[Any] = [table]
    if fundamental.get("status") == "INSUFFICIENT_DATA":
        reasons = fundamental.get("insufficientDataReasons") or []
        warning = Text("Insufficient data: valuation and recommendation unavailable.", style=f"bold {PALETTE['bad']}")
        if reasons:
            warning.append("\n" + " ".join(str(reason) for reason in reasons), style=PALETTE["muted"])
        body.append(warning)
        subtitle = "INSUFFICIENT_DATA — no actionable valuation"
    if consensus:
        subtitle = subtitle or (
            f"{_display(consensus.get('valuation'))} • method dispersion "
            f"{_pct(consensus.get('methodDispersionPct'))}"
        )
    return Panel(Group(*body), title="💎 Fundamental Fair Value", subtitle=subtitle, border_style=PALETTE["gold"], box=box.ROUNDED)


def render_news_stock(stock_data: Mapping[str, Any], max_rows: int = 6) -> Optional[Panel]:
    items = stock_data.get("news") or []
    if not items:
        return None
    table = Table(show_header=True, header_style=f"bold {PALETTE['accent']}", box=None, expand=True, padding=(0, 1))
    table.add_column("Date", width=13)
    table.add_column("Headline", ratio=3)
    table.add_column("Source", width=18)
    for item in items[:max_rows]:
        table.add_row(_display(item.get("published") or item.get("date")), _display(item.get("title")), _display(item.get("source")))
    return Panel(table, title="📰 Recent News", border_style=PALETTE["accent"], box=box.ROUNDED)


def render_professional_report(analysis: Mapping[str, Any]) -> Panel:
    report = analysis.get("professionalReport") or {}
    parts: List[Any] = []
    headline = _display(report.get("headline"), "")
    if headline:
        parts.append(Text(headline, style=f"bold {PALETTE['info']}"))
    valuation = _display(report.get("valuationView"), "")
    if valuation:
        line = Text()
        line.append("Valuation • ", style=f"bold {PALETTE['muted']}")
        line.append(valuation)
        parts.append(line)
    investment_case = report.get("investmentCase") or []
    if investment_case:
        block = Text()
        block.append("Investment Case • ", style=f"bold {PALETTE['muted']}")
        block.append(" • ".join(_display(item) for item in investment_case))
        parts.append(block)
    outlook = report.get("timeframeOutlook") or {}
    if outlook:
        table = Table(show_header=True, header_style=f"bold {PALETTE['muted']}", box=None, expand=True, padding=(0, 1))
        table.add_column("TF", style=f"bold {PALETTE['brand']}", width=6)
        table.add_column("Interpretation")
        for key, value in outlook.items():
            table.add_row(str(key).upper(), _display(value))
        parts.append(table)
    interpretation = _display(report.get("professionalInterpretation"), "")
    if interpretation:
        line = Text()
        line.append("How to Use • ", style=f"bold {PALETTE['muted']}")
        line.append(interpretation, style=PALETTE["muted"])
        parts.append(line)
    return Panel(Group(*parts), title="🧠 Professional Interpretation", border_style="bright_white", box=box.ROUNDED)


def render_quality(analysis: Mapping[str, Any]) -> Panel:
    quality = _deep_get(analysis, "fundamental", "quality", default={}) or {}
    metrics = [
        ("Overall", quality.get("overall")),
        ("Growth", quality.get("growth")),
        ("Profitability", quality.get("profitability")),
        ("Financial Health", quality.get("financialHealth")),
        ("Shareholder Return", quality.get("shareholderReturn")),
    ]
    table = Table(show_header=False, box=None, expand=True)
    table.add_column("Dimension", style=PALETTE["muted"])
    table.add_column("Score", justify="right")
    table.add_column("Bar")
    for label, value in metrics:
        n = max(0.0, min(100.0, _num(value) or 0.0))
        blocks = int(round(n / 10))
        bar = "█" * blocks + "░" * (10 - blocks)
        style = _style_for_score(n)
        table.add_row(label, Text(f"{n:.1f}", style=style), Text(bar, style=style))
    return Panel(table, title=f"🏢 Business Quality • {_display(quality.get('label'))}", border_style="bright_blue", box=box.ROUNDED)


def render_stock_comparison(results: Sequence[Tuple[Mapping[str, Any], Mapping[str, Any]]]) -> None:
    table = Table(show_header=True, header_style=f"bold {PALETTE['brand']}", box=box.SIMPLE_HEAVY, expand=True)
    table.add_column("Ticker", style=f"bold {PALETTE['info']}")
    table.add_column("Price", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Stance")
    table.add_column("Quality")
    table.add_column("Fund FV", justify="right")
    table.add_column("Fund Gap", justify="right")
    table.add_column("1M", justify="right")
    table.add_column("3M", justify="right")
    table.add_column("6M", justify="right")
    table.add_column("12M", justify="right")
    table.add_column("Tech", justify="right")

    for stock_data, analysis in results:
        s = analysis.get("executiveSummary") or {}
        fund = _deep_get(analysis, "fairValue", "fundamental", default={}) or {}
        tfs = analysis.get("timeframes") or {}
        stance = _display(s.get("stance"))
        row: List[Any] = [
            _display(stock_data.get("ticker")),
            _money(s.get("currentPrice")),
            Text(_score(s.get("overallScore")), style=_style_for_score(s.get("overallScore"))),
            Text(stance, style=_style_for_stance(stance)),
            _display(s.get("fundamentalQuality")),
            _money(fund.get("base")),
            Text(_pct(fund.get("upsideToBasePct"), signed=True), style=_style_for_number(fund.get("upsideToBasePct"))),
        ]
        for key in ["1m", "3m", "6m", "12m"]:
            gap = _deep_get(tfs, key, "expectedGapPct")
            row.append(Text(_pct(gap, signed=True), style=_style_for_number(gap)))
        row.append(_score(s.get("technicalAggregateScore")))
        table.add_row(*row)
    console.print(Panel(table, title="🔀 Cross-Stock Comparison", border_style=PALETTE["brand"], box=box.ROUNDED))


def render_stock_header(stock_data: Mapping[str, Any], analysis: Mapping[str, Any]) -> Panel:
    ticker = _display(stock_data.get("ticker"))
    profile = stock_data.get("profile") or {}
    quote = stock_data.get("quote") or {}
    summary = analysis.get("executiveSummary") or {}

    change = _num(quote.get("change"))
    change_style = _style_for_number(change)
    stance = _display(summary.get("stance"))
    stance_style = _style_for_stance(stance)
    company_line = _company_label(profile)

    left = Text()
    left.append(ticker, style=f"bold {PALETTE['info']}")
    left.append("  ")
    left.append(_display(profile.get("name"), "Stock"), style="bold white")
    ceo = profile.get("ceo")
    metadata = [x for x in [company_line, f"CEO: {_display(ceo)}" if ceo else ""] if x]
    if metadata:
        left.append("\n" + " • ".join(metadata), style=PALETTE["muted"])

    mid = Text(justify="center")
    mid.append(_money(quote.get("price")), style="bold white")
    mid.append("\n")
    mid.append(f"{_display(quote.get('change'))} ({_display(quote.get('changePercent'))}%)", style=change_style)
    if quote.get("asOf"):
        mid.append(" • " + _display(quote.get("asOf")), style=PALETTE["muted"])

    right = Text(justify="right")
    right.append(stance, style=f"bold {stance_style}")
    right.append("\n")
    right.append(f"Composite {_score(summary.get('overallScore'))}/100", style=_style_for_score(summary.get("overallScore")))
    right.append(" • ")
    right.append(f"Quality: {_display(summary.get('fundamentalQuality'))}", style=PALETTE["muted"])

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=4)
    grid.add_column(ratio=2, justify="center")
    grid.add_column(ratio=3, justify="right")
    grid.add_row(left, mid, right)
    return Panel(grid, title="Professional Stock Snapshot", box=box.HEAVY, border_style=PALETTE["brand"], padding=(0, 1))


def render_stock_kpis(stock_data: Mapping[str, Any]) -> Panel:
    f = stock_data.get("fundamentals") or {}
    quote = stock_data.get("quote") or {}
    fields = [
        ("Market Cap", f.get("marketCap")),
        ("Revenue TTM", f.get("revenue")),
        ("Net Income", f.get("netIncome")),
        ("EPS", f.get("eps")),
        ("P/E", f.get("peRatio")),
        ("Forward P/E", f.get("forwardPE")),
        ("Beta", f.get("beta")),
        ("52W Range", f"{_display(quote.get('week52Low'))} – {_display(quote.get('week52High'))}"),
    ]
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in fields:
        grid.add_column(ratio=1, justify="center")
    grid.add_row(*[Text(label, style=PALETTE["muted"]) for label, _ in fields])
    grid.add_row(*[Text(_display(value), style="bold white") for _, value in fields])
    return Panel(grid, title="Market & Fundamental KPIs", border_style="grey42", box=box.ROUNDED)


def render_stock_report(stock_data: Mapping[str, Any], analysis: Mapping[str, Any], compact: bool = False) -> None:
    console.print(render_stock_header(stock_data, analysis))
    console.print(render_stock_kpis(stock_data))

    _print_adaptive_pair(
        render_executive_summary(analysis),
        render_quality(analysis),
        min_width=92 if compact else 104,
        ratios=(3, 2),
    )

    _print_adaptive_pair(
        render_timeframes(analysis),
        render_fair_value(analysis),
        min_width=158 if compact else 176,
        ratios=(3, 2),
    )
    console.print(render_technical(analysis))
    catalysts_panel, risks_panel = render_catalysts_risks(analysis)
    _print_adaptive_pair(
        catalysts_panel,
        risks_panel,
        min_width=92 if compact else 104,
        ratios=(1, 1),
    )

    # Compact means denser placement, not less information. Every report
    # section remains visible; the layout simply packs panels more aggressively.
    _print_adaptive_pair(
        render_business_context(analysis),
        render_news_stock(stock_data),
        min_width=112 if compact else 126,
        ratios=(2, 3),
    )
    _print_adaptive_pair(
        render_professional_report(analysis),
        render_data_quality(stock_data, analysis),
        min_width=132 if compact else 150,
        ratios=(5, 2),
    )

    render_errors(stock_data.get("errors") or [])
    console.print(Text(_display(analysis.get("disclaimer")), style=f"italic {PALETTE['muted']}"), justify="center")


def render_technical(analysis: Mapping[str, Any]) -> Panel:
    technical = analysis.get("technical") or {}
    if not technical.get("available"):
        reason = _display(technical.get("reason"), "Historical OHLCV is unavailable")
        return Panel(
            Text(reason, style=PALETTE["warn"]),
            title="📐 Technical Analysis Unavailable",
            border_style=PALETTE["warn"],
            box=box.ROUNDED,
        )

    table = Table(show_header=True, header_style=f"bold {PALETTE['accent']}", box=None, expand=True, padding=(0, 1))
    table.add_column("TF", style="bold")
    table.add_column("Bar", justify="right")
    table.add_column("Trend")
    table.add_column("Score", justify="right")
    table.add_column("RSI", justify="right")
    table.add_column("MACD", justify="right")
    table.add_column("SMA20", justify="right")
    table.add_column("SMA50", justify="right")
    table.add_column("SMA200", justify="right")
    table.add_column("Vol", justify="right")
    table.add_column("Support")
    table.add_column("Resistance")

    for key, item in (technical.get("timeframes") or {}).items():
        ind = item.get("indicators") or {}
        levels = item.get("levels") or {}
        supports = levels.get("supports") or []
        resistances = levels.get("resistances") or []
        bar_days = int(item.get("barSizeTradingDays") or (item.get("sampling") or {}).get("barSizeTradingDays") or 1)
        bar_label = "1D" if bar_days == 1 else f"{bar_days}D"
        table.add_row(
            str(key).upper(),
            bar_label,
            _display(item.get("trend")),
            Text(_score(item.get("technicalScore")), style=_style_for_score(item.get("technicalScore"))),
            _score(ind.get("rsi14")),
            _score(ind.get("macd")),
            _money(ind.get("sma20")),
            _money(ind.get("sma50")),
            _money(ind.get("sma200")),
            _pct((_num(ind.get("annualizedVolatility")) or 0) * 100),
            ", ".join(_money(x) for x in supports[:2]) or "—",
            ", ".join(_money(x) for x in resistances[:2]) or "—",
        )

    subtitle = (
        f"aggregate {_score(technical.get('aggregateScore'))}/100 • "
        f"{technical.get('dataPoints', '—')} datapoints • indicators recomputed per timeframe bar"
    )
    return Panel(table, title="📐 Technical State", subtitle=subtitle, border_style=PALETTE["accent"], box=box.ROUNDED)


def render_timeframes(analysis: Mapping[str, Any]) -> Panel:
    timeframes = analysis.get("timeframes") or {}
    table = Table(show_header=True, header_style=f"bold {PALETTE['brand']}", box=None, expand=True, padding=(0, 1))
    table.add_column("Horizon", style="bold")
    table.add_column("Stance")
    table.add_column("Score", justify="right")
    table.add_column("FV Low", justify="right")
    table.add_column("FV Base", justify="right")
    table.add_column("FV High", justify="right")
    table.add_column("Gap", justify="right")
    table.add_column("Trend")
    table.add_column("T/F Weight", justify="center")
    table.add_column("Confidence", justify="right")

    for key, item in timeframes.items():
        fv = item.get("fairValue") or {}
        weights = item.get("weights") or {}
        gap = item.get("expectedGapPct")
        stance = _display(item.get("stance"))
        table.add_row(
            f"{key.upper()} [dim]{escape(_display(item.get('label')))}[/]",
            Text(stance, style=_style_for_stance(stance)),
            Text(_score(item.get("compositeScore")), style=_style_for_score(item.get("compositeScore"))),
            _money(fv.get("low")),
            Text(_money(fv.get("base")), style="bold white"),
            _money(fv.get("high")),
            Text(_pct(gap, signed=True), style=_style_for_number(gap)),
            _display(item.get("technicalTrend")),
            f"{(_num(weights.get('technical')) or 0)*100:.0f}/{(_num(weights.get('fundamental')) or 0)*100:.0f}",
            f"{_score(item.get('confidence'))}/100",
        )
    return Panel(table, title="🧭 Multi-Timeframe Projection", border_style=PALETTE["brand"], box=box.ROUNDED)


def run_stock_pipeline(
    tickers: Sequence[str],
    config: MomoConfig,
    sections: Optional[List[str]],
    timeframes: Sequence[str],
    period: str,
    interval: str,
    use_price: bool,
    dump_json: bool,
    output_dir: Path,
    debug_html: bool,
    compare: bool,
    compact: bool,
    visual: bool = False,
) -> int:
    results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    failures: List[Tuple[str, str]] = []
    visual_reports: List[Path] = []
    visual_failures: List[Tuple[str, str]] = []

    progress = Progress(
        SpinnerColumn(style=PALETTE["info"]),
        TextColumn("[bold]{task.fields[ticker]}[/] [grey62]{task.description}[/]"),
        BarColumn(bar_width=None, complete_style=PALETTE["good"], style="grey30"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        expand=True,
        transient=True,
    )
    tasks = {ticker: progress.add_task("queued", total=4, ticker=ticker) for ticker in tickers}

    with Momo(config=config, save_debug_html=debug_html) as client:
        with progress:
            for ticker in tickers:
                task = tasks[ticker]
                try:
                    progress.update(task, description="fetching company fundamentals")
                    stock_data = (
                        client.Stock(ticker).get_data()
                        if sections is None
                        else client.get_stock_data(ticker, sections=sections)
                    )
                    progress.advance(task)

                    price_data = None
                    auto_fetch = False
                    if use_price:
                        progress.update(task, description=f"fetching OHLCV {period}/{interval}")
                        if fetch_yahoo_history is not None:
                            try:
                                price_data = fetch_yahoo_history(ticker, period=period, interval=interval)
                            except Exception:
                                stock_data.setdefault("errors", []).append({
                                    "section": "historical_price",
                                    "error": "Historical price data could not be retrieved.",
                                })
                        else:
                            stock_data.setdefault("errors", []).append({
                                "section": "historical_price",
                                "error": "Technical analysis dependencies are unavailable.",
                            })
                    progress.advance(task)

                    progress.update(task, description="running valuation + multi-timeframe engine")
                    analysis = _analysis_via_momo(
                        client,
                        stock_data,
                        price_data=price_data,
                        period=period,
                        interval=interval,
                        auto_fetch_price=auto_fetch,
                        timeframes=timeframes,
                    )
                    progress.advance(task)

                    if visual:
                        progress.update(task, description="building visual dashboard")
                        try:
                            visual_path = output_dir / visual_filename(ticker)
                            write_stock_visual_report(ticker, price_data, analysis, visual_path, stock_data)
                            visual_reports.append(visual_path)
                        except Exception as exc:
                            visual_failures.append((ticker, str(exc)))

                    if dump_json:
                        progress.update(task, description="writing JSON artifacts")
                        _json_dump(output_dir / f"momo_{ticker.lower()}_stock.json", stock_data)
                        _json_dump(output_dir / f"momo_{ticker.lower()}_analysis.json", analysis)
                        _json_dump(
                            output_dir / f"momo_{ticker.lower()}_report.json",
                            {"data": stock_data, "analysis": analysis},
                        )
                    else:
                        progress.update(task, description="ready")
                    progress.advance(task)
                    results.append((stock_data, analysis))
                except (MomoError, MomoValidationError, ValueError, RuntimeError) as exc:
                    failures.append((ticker, str(exc)))
                    progress.update(task, description="failed", completed=4)
                except Exception as exc:  # CLI boundary: do not crash the entire batch
                    failures.append((ticker, f"Unexpected error: {exc}"))
                    progress.update(task, description="failed", completed=4)

    for visual_path in visual_reports:
        try:
            opened = webbrowser.open(visual_path.resolve().as_uri(), new=2)
        except Exception:
            opened = False
        if opened:
            console.print(f"[{PALETTE['good']}]Visual dashboard opened:[/] {visual_path}")
        else:
            console.print(f"[{PALETTE['info']}]Visual dashboard saved:[/] {visual_path}")

    if visual_failures:
        for ticker, reason in visual_failures:
            console.print(
                Panel(
                    f"{ticker}: {reason}",
                    title="⚠ Visual dashboard unavailable",
                    border_style=PALETTE["warn"],
                    box=box.ROUNDED,
                )
            )

    if compare and results:
        render_stock_comparison(results)
    else:
        for idx, (stock_data, analysis) in enumerate(results):
            if idx:
                console.print(Rule(style=PALETTE["brand"]))
            render_stock_report(stock_data, analysis, compact=compact)

        if len(results) > 1:
            console.print(Rule("Cross-Ticker Summary", style=PALETTE["brand"]))
            render_stock_comparison(results)

    if failures:
        table = Table(show_header=True, header_style=f"bold {PALETTE['bad']}", box=box.SIMPLE_HEAVY, expand=True)
        table.add_column("Ticker", width=12)
        table.add_column("Failure")
        for ticker, reason in failures:
            table.add_row(ticker, reason)
        console.print(Panel(table, title="❌ Failed Tickers", border_style=PALETTE["bad"], box=box.ROUNDED))

    run_summary = {
        "assetType": "stock",
        "exitReason": "ok" if results and not failures else ("partial" if results else "failed"),
        "attempted": list(tickers),
        "available": [data.get("ticker") for data, _ in results],
        "failed": {ticker: reason for ticker, reason in failures},
        "providerLatencyMs": {},
    }
    _json_dump(output_dir / "momo_run_summary.json", run_summary)
    return 0 if results else 1
