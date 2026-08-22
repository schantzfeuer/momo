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
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import typer
from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
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
    from technical import __version__ as TECHNICAL_VERSION
    from technical import fetch_yahoo_history
except ImportError:  # MOMO fundamental-only mode remains available
    TECHNICAL_VERSION = "unavailable"
    fetch_yahoo_history = None  # type: ignore[assignment]


console = Console(highlight=False, soft_wrap=False)
APP_NAME = "MOMO Professional Analyzer"
APP_VERSION = f"MOMO {MOMO_VERSION} • Technical {TECHNICAL_VERSION}"

PALETTE = {
    "brand": "bright_magenta",
    "info": "bright_cyan",
    "good": "bright_green",
    "bad": "bright_red",
    "warn": "bright_yellow",
    "muted": "grey62",
    "accent": "deep_sky_blue1",
    "gold": "gold1",
}

ETF_SECTIONS = ["overview", "performance", "holdings", "sectors", "countries", "dividends", "news"]
ETF_PAGES_FOR_SECTION: Dict[str, set[str]] = {
    "overview": {"overview"},
    "performance": {"overview"},
    "holdings": {"holdings"},
    "sectors": {"holdings"},
    "countries": {"holdings"},
    "dividends": {"dividends"},
    "news": {"overview", "holdings", "dividends"},
}


# =============================================================================
# Generic helpers
# =============================================================================


def _display(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text and text.lower() not in {"none", "nan", "n/a"} else fallback


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text.lower() in {"none", "nan", "n/a", "—"}:
        return None
    multipliers = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    suffix = text[-1:].upper()
    mult = multipliers.get(suffix, 1.0)
    if suffix in multipliers:
        text = text[:-1]
    try:
        return float(text) * mult
    except ValueError:
        return None


def _money(value: Any, decimals: int = 2) -> str:
    num = _num(value)
    if num is None:
        return _display(value)
    return f"${num:,.{decimals}f}"


def _pct(value: Any, signed: bool = False) -> str:
    num = _num(value)
    if num is None:
        return _display(value)
    fmt = "+.1f" if signed else ".1f"
    return f"{format(num, fmt)}%"


def _score(value: Any) -> str:
    num = _num(value)
    return f"{num:.1f}" if num is not None else "—"


def _style_for_number(value: Any) -> str:
    num = _num(value)
    if num is None:
        return "white"
    if num > 0:
        return PALETTE["good"]
    if num < 0:
        return PALETTE["bad"]
    return PALETTE["muted"]


def _style_for_score(value: Any) -> str:
    num = _num(value)
    if num is None:
        return PALETTE["muted"]
    if num >= 70:
        return PALETTE["good"]
    if num >= 55:
        return "green3"
    if num >= 45:
        return PALETTE["warn"]
    return PALETTE["bad"]


def _style_for_stance(value: Any) -> str:
    text = str(value or "").lower()
    if any(k in text for k in ("attractive", "bull", "buy", "strong")):
        return PALETTE["good"]
    if any(k in text for k in ("cautious", "bear", "avoid", "overvalued", "weak")):
        return PALETTE["bad"]
    return PALETTE["warn"]


def _normalize_tickers(tickers: Sequence[str]) -> List[str]:
    output: List[str] = []
    for raw in tickers:
        for token in str(raw).replace(",", " ").split():
            token = token.strip().upper()
            if token and token not in output:
                output.append(token)
    return output


def _parse_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.replace(" ", ",").split(",") if x.strip()]


def _deep_get(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return default
        value = value.get(key)
        if value is None:
            return default
    return value


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)


def _company_label(profile: Mapping[str, Any]) -> str:
    industry = profile.get("industry")
    sector = profile.get("sector")
    if isinstance(industry, Mapping):
        industry = industry.get("value")
    if isinstance(sector, Mapping):
        sector = sector.get("value")
    return " • ".join(x for x in [_display(sector, ""), _display(industry, "")] if x)


def render_banner(asset_type: str, tickers: Sequence[str]) -> Panel:
    left = Text()
    left.append(APP_NAME, style=f"bold {PALETTE['brand']}")
    left.append("\n")
    left.append(APP_VERSION, style=PALETTE["muted"])

    right = Text(justify="right")
    right.append(asset_type.upper(), style=f"bold {PALETTE['info']}")
    right.append("\n")
    right.append(", ".join(tickers), style="bold white")

    grid = Table.grid(expand=True)
    grid.add_column(ratio=3)
    grid.add_column(ratio=2, justify="right")
    grid.add_row(left, right)
    return Panel(grid, box=box.HEAVY, border_style=PALETTE["brand"], padding=(0, 1))


# =============================================================================
# ETF pipeline + Rich rendering
# =============================================================================


def _expand_etf_sections(requested: Sequence[str]) -> List[str]:
    requested = [x.lower() for x in requested]
    if not requested or "all" in requested:
        return list(ETF_SECTIONS)
    unknown = [x for x in requested if x not in ETF_SECTIONS]
    if unknown:
        raise typer.BadParameter(
            f"Unknown ETF section: {', '.join(unknown)}. Choices: {', '.join(ETF_SECTIONS)}"
        )
    return [x for x in ETF_SECTIONS if x in requested]


def fetch_etf_targeted(
    client: Momo,
    ticker: str,
    sections: Sequence[str],
) -> Dict[str, Any]:
    needed_pages: set[str] = set()
    for section in sections:
        needed_pages |= ETF_PAGES_FOR_SECTION.get(section, set())

    if needed_pages >= {"overview", "holdings", "dividends"}:
        return client.get_etf_data(ticker)

    overview: Dict[str, Any] = {}
    holdings: Dict[str, Any] = {}
    dividends: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    if "overview" in needed_pages:
        try:
            overview = client.get_overview(ticker)
        except MomoError as exc:
            errors.append({"section": "overview", "error": str(exc)})
    if "holdings" in needed_pages:
        try:
            holdings = client.get_holdings(ticker)
        except MomoError as exc:
            errors.append({"section": "holdings", "error": str(exc)})
    if "dividends" in needed_pages:
        try:
            dividends = client.get_dividends(ticker)
        except MomoError as exc:
            errors.append({"section": "dividends", "error": str(exc)})

    news = None
    if "news" in sections:
        try:
            news = client.get_news(
                ticker,
                overview=overview or None,
                holdings=holdings or None,
                dividends=dividends or None,
            )
        except MomoError as exc:
            news = []
            errors.append({"section": "news", "error": str(exc)})

    return assemble_etf_data(
        ticker,
        overview=overview,
        holdings=holdings,
        dividends=dividends,
        news=news,
        errors=errors or None,
    )


def render_etf_report(data: Mapping[str, Any], sections: Sequence[str]) -> None:
    ticker = _display(data.get("ticker"))
    profile = data.get("profile") or {}
    quote = data.get("quote") or {}
    holdings = data.get("holdings") or {}
    dividends = data.get("dividends") or {}
    perf = _deep_get(data, "performance", "totalReturn", default={}) or {}

    change = _num(quote.get("change"))
    change_style = _style_for_number(change)
    header = Table.grid(expand=True, padding=(0, 1))
    header.add_column(ratio=3)
    header.add_column(ratio=2, justify="right")
    header.add_row(
        Text.from_markup(
            f"[bold bright_cyan]{escape(ticker)}[/]  [bold white]{escape(_display(profile.get('name'), 'ETF'))}[/]\n"
            f"[{PALETTE['muted']}]{escape(_display(profile.get('provider')))} • {escape(_display(profile.get('exchange')))}[/]"
        ),
        Text.from_markup(
            f"[bold white]{escape(_display(quote.get('price')))}[/]\n"
            f"[{change_style}]{escape(_display(quote.get('change')))} ({escape(_display(quote.get('changePercent')))})[/]"
        ),
    )
    console.print(Panel(header, title="ETF Snapshot", box=box.HEAVY, border_style=PALETTE["info"]))

    kpi = Table(show_header=True, header_style=f"bold {PALETTE['muted']}", box=box.SIMPLE_HEAVY, expand=True)
    for col in ["AUM", "Expense", "NAV", "Yield", "Holdings", "52W High", "52W Low"]:
        kpi.add_column(col, justify="center")
    kpi.add_row(
        _display(profile.get("aum")),
        _display(profile.get("expenseRatio")),
        _display(profile.get("nav")),
        _display(dividends.get("yield")),
        _display(holdings.get("totalHoldingsCount")),
        _display(quote.get("week52High")),
        _display(quote.get("week52Low")),
    )
    console.print(Panel(kpi, title="Key Metrics", border_style="grey42", box=box.ROUNDED))

    panels: List[Panel] = []
    if "performance" in sections:
        table = Table(box=None, expand=True)
        table.add_column("Period")
        table.add_column("Return", justify="right")
        labels = {"tr1m": "1M", "trYTD": "YTD", "tr1y": "1Y", "tr3y": "3Y", "tr5y": "5Y", "tr10y": "10Y"}
        if isinstance(perf, Mapping):
            for key, value in perf.items():
                style = _style_for_number(value)
                table.add_row(labels.get(str(key), str(key)), Text(_pct(value), style=style))
        panels.append(Panel(table, title="📈 Performance", border_style=PALETTE["good"], box=box.ROUNDED))

    if "dividends" in sections:
        metrics = dividends.get("metrics") or {}
        table = Table.grid(expand=True)
        table.add_column(style=PALETTE["muted"])
        table.add_column(justify="right")
        for label, value in [
            ("Yield", dividends.get("yield")),
            ("Annual Dividend", metrics.get("annualDividend")),
            ("Ex-Date", metrics.get("exDividendDate")),
            ("Frequency", metrics.get("payoutFrequency")),
            ("Payout Ratio", metrics.get("payoutRatio")),
            ("Growth 1Y", metrics.get("dividendGrowth1Y")),
        ]:
            table.add_row(label, _display(value))
        panels.append(Panel(table, title="💰 Dividend", border_style="green3", box=box.ROUNDED))

    if panels:
        console.print(Columns(panels, equal=True, expand=True))

    if "holdings" in sections:
        rows = holdings.get("topHoldings") or []
        table = Table(show_header=True, header_style=f"bold {PALETTE['gold']}", box=box.SIMPLE_HEAVY, expand=True)
        table.add_column("#", width=4)
        table.add_column("Symbol", style="bold")
        table.add_column("Holding", ratio=2)
        table.add_column("Weight", justify="right")
        for idx, row in enumerate(rows[:12], 1):
            table.add_row(str(idx), _display(row.get("symbol")), _display(row.get("name")), _display(row.get("weight")))
        console.print(Panel(table, title="🏦 Top Holdings", border_style=PALETTE["gold"], box=box.ROUNDED))

    if "sectors" in sections or "countries" in sections:
        breakdown_panels: List[Panel] = []
        for key, title, color in [
            ("sectors", "🏭 Sector Allocation", PALETTE["brand"]),
            ("countries", "🌍 Country Allocation", "bright_blue"),
        ]:
            if key not in sections:
                continue
            items = holdings.get(key) or []
            table = Table(box=None, expand=True)
            table.add_column("Name")
            table.add_column("Weight", justify="right")
            for item in items[:10]:
                keys = list(item.keys()) if isinstance(item, Mapping) else []
                if not keys:
                    continue
                weight_key = next((k for k in keys if str(k).lower() in {"w", "weight"}), None)
                label_key = next((k for k in keys if k != weight_key), keys[0])
                table.add_row(_display(item.get(label_key)), _display(item.get(weight_key)) if weight_key else "—")
            breakdown_panels.append(Panel(table, title=title, border_style=color, box=box.ROUNDED))
        if breakdown_panels:
            console.print(Columns(breakdown_panels, equal=True, expand=True))

    if "news" in sections:
        news = data.get("news") or []
        table = Table(show_header=True, header_style=f"bold {PALETTE['accent']}", box=box.SIMPLE, expand=True)
        table.add_column("Date", width=13)
        table.add_column("Headline", ratio=3)
        table.add_column("Source", width=18)
        for item in news[:8]:
            table.add_row(_display(item.get("published")), _display(item.get("title")), _display(item.get("source")))
        console.print(Panel(table, title="📰 News", border_style=PALETTE["accent"], box=box.ROUNDED))

    if data.get("errors"):
        render_errors(data.get("errors") or [])


# =============================================================================
# Stock professional analysis rendering
# =============================================================================


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
    if company_line:
        left.append("\n" + company_line, style=PALETTE["muted"])
    ceo = profile.get("ceo")
    if ceo:
        left.append(f"\nCEO: {_display(ceo)}", style=PALETTE["muted"])

    mid = Text(justify="center")
    mid.append(_money(quote.get("price")), style="bold white")
    mid.append("\n")
    mid.append(f"{_display(quote.get('change'))} ({_display(quote.get('changePercent'))}%)", style=change_style)
    if quote.get("asOf"):
        mid.append("\n" + _display(quote.get("asOf")), style=PALETTE["muted"])

    right = Text(justify="right")
    right.append(stance, style=f"bold {stance_style}")
    right.append("\n")
    right.append(f"Composite {_score(summary.get('overallScore'))}/100", style=_style_for_score(summary.get("overallScore")))
    right.append("\n")
    right.append(f"Quality: {_display(summary.get('fundamentalQuality'))}", style=PALETTE["muted"])

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=4)
    grid.add_column(ratio=2, justify="center")
    grid.add_column(ratio=3, justify="right")
    grid.add_row(left, mid, right)
    return Panel(grid, title="Professional Stock Snapshot", box=box.HEAVY, border_style=PALETTE["brand"], padding=(1, 1))


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


def render_executive_summary(analysis: Mapping[str, Any]) -> Panel:
    s = analysis.get("executiveSummary") or {}
    fv = analysis.get("fairValue") or {}
    fundamental = fv.get("fundamental") or {}
    stance = _display(s.get("stance"))
    stance_style = _style_for_stance(stance)

    table = Table.grid(expand=True, padding=(0, 2))
    table.add_column(style=PALETTE["muted"])
    table.add_column()
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
    for label, value in rows:
        table.add_row(label, value if isinstance(value, Text) else Text(str(value)))

    interpretation = _display(s.get("interpretation"), "")
    body: List[Any] = [table]
    if interpretation:
        body.extend([Rule(style="grey35"), Text(interpretation, style=PALETTE["muted"])])
    return Panel(Group(*body), title="🎯 Executive View", border_style=PALETTE["info"], box=box.ROUNDED)


def render_fair_value(analysis: Mapping[str, Any]) -> Panel:
    fundamental = analysis.get("fundamental") or {}
    methods = fundamental.get("methods") or {}
    consensus = fundamental.get("consensus") or {}
    current = _num(fundamental.get("currentPrice"))

    table = Table(show_header=True, header_style=f"bold {PALETTE['gold']}", box=box.SIMPLE_HEAVY, expand=True)
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
    if consensus:
        subtitle = (
            f"{_display(consensus.get('valuation'))} • method dispersion "
            f"{_pct(consensus.get('methodDispersionPct'))}"
        )
    return Panel(table, title="💎 Fundamental Fair Value", subtitle=subtitle, border_style=PALETTE["gold"], box=box.ROUNDED)


def render_timeframes(analysis: Mapping[str, Any]) -> Panel:
    timeframes = analysis.get("timeframes") or {}
    table = Table(show_header=True, header_style=f"bold {PALETTE['brand']}", box=box.SIMPLE_HEAVY, expand=True)
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
            f"{key.upper()}\n[dim]{escape(_display(item.get('label')))}[/]",
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

    table = Table(show_header=True, header_style=f"bold {PALETTE['accent']}", box=box.SIMPLE, expand=True)
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


def render_catalysts_risks(analysis: Mapping[str, Any]) -> Columns:
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
    return Columns([catalysts, risks], equal=True, expand=True)


def render_professional_report(analysis: Mapping[str, Any]) -> Panel:
    report = analysis.get("professionalReport") or {}
    parts: List[Any] = []
    headline = _display(report.get("headline"), "")
    if headline:
        parts.append(Text(headline, style=f"bold {PALETTE['info']}"))
    valuation = _display(report.get("valuationView"), "")
    if valuation:
        parts.extend([Rule("Valuation", style="grey35"), Text(valuation)])
    investment_case = report.get("investmentCase") or []
    if investment_case:
        parts.extend([Rule("Investment Case", style="grey35"), _render_bullets(investment_case)])
    outlook = report.get("timeframeOutlook") or {}
    if outlook:
        table = Table(show_header=False, box=None, expand=True)
        table.add_column("TF", style=f"bold {PALETTE['brand']}", width=6)
        table.add_column("Interpretation")
        for key, value in outlook.items():
            table.add_row(str(key).upper(), _display(value))
        parts.extend([Rule("Horizon Interpretation", style="grey35"), table])
    interpretation = _display(report.get("professionalInterpretation"), "")
    if interpretation:
        parts.extend([Rule("How to Use", style="grey35"), Text(interpretation, style=PALETTE["muted"])])
    return Panel(Group(*parts), title="🧠 Professional Interpretation", border_style="bright_white", box=box.ROUNDED)


def render_business_context(analysis: Mapping[str, Any]) -> Optional[Panel]:
    ctx = _deep_get(analysis, "fundamental", "businessContext", default={}) or {}
    if not ctx:
        return None
    table = Table(show_header=True, header_style=f"bold {PALETTE['info']}", box=box.SIMPLE, expand=True)
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
            weight = item.get("weightPct") or item.get("sharePct") or item.get("weight")
            table.add_row(f"Segment: {name}", _pct(weight))
    return Panel(table, title="🧩 Business Mix", border_style="bright_blue", box=box.ROUNDED)


def render_news_stock(stock_data: Mapping[str, Any], max_rows: int = 6) -> Optional[Panel]:
    items = stock_data.get("news") or []
    if not items:
        return None
    table = Table(show_header=True, header_style=f"bold {PALETTE['accent']}", box=box.SIMPLE, expand=True)
    table.add_column("Date", width=13)
    table.add_column("Headline", ratio=3)
    table.add_column("Source", width=18)
    for item in items[:max_rows]:
        table.add_row(_display(item.get("published") or item.get("date")), _display(item.get("title")), _display(item.get("source")))
    return Panel(table, title="📰 Recent News", border_style=PALETTE["accent"], box=box.ROUNDED)


def render_data_quality(stock_data: Mapping[str, Any], analysis: Mapping[str, Any]) -> Panel:
    report = _deep_get(analysis, "professionalReport", "dataQuality", default={}) or {}
    context = analysis.get("dataContext") or {}
    rows = [
        ("Fundamental methods", ", ".join(report.get("fundamentalMethodsUsed") or []) or "—"),
        ("Fundamental confidence", f"{_score(report.get('fundamentalConfidence'))}/100"),
        ("Technical available", "yes" if report.get("technicalAvailable") else "no"),
        ("Technical datapoints", _display(report.get("technicalDataPoints"))),
        ("Timeframes", ", ".join(report.get("timeframesAnalyzed") or [])),
        ("Source", _display(context.get("source"), _display(_deep_get(stock_data, "_meta", "source")))),
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


def render_stock_report(stock_data: Mapping[str, Any], analysis: Mapping[str, Any], compact: bool = False) -> None:
    console.print(render_stock_header(stock_data, analysis))
    console.print(render_stock_kpis(stock_data))

    console.print(Columns([render_executive_summary(analysis), render_quality(analysis)], equal=True, expand=True))
    console.print(render_timeframes(analysis))
    console.print(render_fair_value(analysis))
    console.print(render_technical(analysis))
    console.print(render_catalysts_risks(analysis))

    if not compact:
        context_panel = render_business_context(analysis)
        news_panel = render_news_stock(stock_data)
        if context_panel and news_panel:
            console.print(Columns([context_panel, news_panel], equal=True, expand=True))
        elif context_panel:
            console.print(context_panel)
        elif news_panel:
            console.print(news_panel)
        console.print(render_professional_report(analysis))
        console.print(render_data_quality(stock_data, analysis))

    render_errors(stock_data.get("errors") or [])
    console.print(Text(_display(analysis.get("disclaimer")), style=f"italic {PALETTE['muted']}"), justify="center")


# =============================================================================
# Cross-ticker comparison
# =============================================================================


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


def render_etf_comparison(results: Sequence[Mapping[str, Any]]) -> None:
    table = Table(show_header=True, header_style=f"bold {PALETTE['brand']}", box=box.SIMPLE_HEAVY, expand=True)
    for col, justify in [
        ("Ticker", "left"), ("Name", "left"), ("Price", "right"), ("1M", "right"),
        ("YTD", "right"), ("1Y", "right"), ("Yield", "right"), ("Expense", "right"),
        ("AUM", "right"), ("Holdings", "right"),
    ]:
        table.add_column(col, justify=justify)
    for data in results:
        profile = data.get("profile") or {}
        quote = data.get("quote") or {}
        perf = _deep_get(data, "performance", "totalReturn", default={}) or {}
        div = data.get("dividends") or {}
        hold = data.get("holdings") or {}
        table.add_row(
            _display(data.get("ticker")), _display(profile.get("name")), _display(quote.get("price")),
            _pct(perf.get("tr1m")), _pct(perf.get("trYTD")), _pct(perf.get("tr1y")),
            _display(div.get("yield")), _display(profile.get("expenseRatio")), _display(profile.get("aum")),
            _display(hold.get("totalHoldingsCount")),
        )
    console.print(Panel(table, title="🔀 Cross-ETF Comparison", border_style=PALETTE["brand"], box=box.ROUNDED))


# =============================================================================
# Execution pipeline
# =============================================================================


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


def _analysis_via_momo(
    client: Momo,
    stock_data: Mapping[str, Any],
    price_data: Any,
    period: str,
    interval: str,
    auto_fetch_price: bool,
    timeframes: Sequence[str],
) -> Dict[str, Any]:
    """Use the new MOMO analysis bridge, with a fallback for v3.0 compatibility."""
    if hasattr(client, "analyze_stock_data"):
        return client.analyze_stock_data(
            stock_data,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )

    # Compatibility bridge for momo.py v3.0, which does not expose the helper.
    try:
        from technical import ProfessionalStockAnalyzer
    except ImportError as exc:
        raise MomoError("technical.py is unavailable for stock analysis.") from exc
    analyzer = ProfessionalStockAnalyzer(
        ticker=_display(stock_data.get("ticker")),
        price_data=price_data,
        fundamental_data=stock_data,
        period=period,
        interval=interval,
        auto_fetch=auto_fetch_price,
    )
    result = analyzer.analyze(timeframes=timeframes)
    meta = stock_data.get("_meta") or {}
    result["dataContext"] = {
        "source": meta.get("source") or "stockanalysis.com",
        "generatedAt": stock_data.get("generatedAt"),
        "sectionsAvailable": meta.get("sectionsAvailable") or [],
        "sectionsRequested": meta.get("sectionsRequested") or [],
        "sourceErrors": stock_data.get("errors") or [],
    }
    return result


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
) -> int:
    results: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    failures: List[Tuple[str, str]] = []

    progress = Progress(
        SpinnerColumn(style=PALETTE["info"]),
        TextColumn("[bold]{task.fields[ticker]}[/] [grey62]{task.description}[/]"),
        BarColumn(bar_width=None, complete_style=PALETTE["good"], style="grey30"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        expand=True,
    )
    tasks = {ticker: progress.add_task("queued", total=4, ticker=ticker) for ticker in tickers}

    with Momo(config=config, save_debug_html=debug_html) as client:
        with progress:
            for ticker in tickers:
                task = tasks[ticker]
                try:
                    progress.update(task, description="fetching StockAnalysis fundamentals")
                    stock_data = client.get_stock_data(ticker, sections=sections)
                    progress.advance(task)

                    price_data = None
                    auto_fetch = False
                    if use_price:
                        progress.update(task, description=f"fetching OHLCV {period}/{interval}")
                        if fetch_yahoo_history is not None:
                            try:
                                price_data = fetch_yahoo_history(ticker, period=period, interval=interval)
                            except Exception as exc:
                                # Continue in fundamental-only mode; the reason appears in technical unavailable.
                                stock_data.setdefault("errors", []).append({
                                    "section": "historical_price",
                                    "error": str(exc),
                                })
                        else:
                            stock_data.setdefault("errors", []).append({
                                "section": "historical_price",
                                "error": "technical.py/yfinance is unavailable",
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

    console.print()
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

    return 0 if results else 1


def run_etf_pipeline(
    tickers: Sequence[str],
    config: MomoConfig,
    sections: Sequence[str],
    dump_json: bool,
    output_dir: Path,
    debug_html: bool,
    compare: bool,
) -> int:
    results: List[Dict[str, Any]] = []
    failures: List[Tuple[str, str]] = []

    progress = Progress(
        SpinnerColumn(style=PALETTE["info"]),
        TextColumn("[bold]{task.fields[ticker]}[/] [grey62]{task.description}[/]"),
        BarColumn(bar_width=None, complete_style=PALETTE["good"], style="grey30"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        expand=True,
    )
    tasks = {ticker: progress.add_task("queued", total=2, ticker=ticker) for ticker in tickers}

    with Momo(config=config, save_debug_html=debug_html) as client:
        with progress:
            for ticker in tickers:
                task = tasks[ticker]
                try:
                    progress.update(task, description="fetching ETF data")
                    data = fetch_etf_targeted(client, ticker, sections)
                    progress.advance(task)
                    if dump_json:
                        progress.update(task, description="writing JSON")
                        _json_dump(output_dir / f"momo_{ticker.lower()}_etf.json", data)
                    else:
                        progress.update(task, description="ready")
                    progress.advance(task)
                    results.append(data)
                except (MomoError, MomoValidationError, ValueError) as exc:
                    failures.append((ticker, str(exc)))
                    progress.update(task, description="failed", completed=2)
                except Exception as exc:
                    failures.append((ticker, f"Unexpected error: {exc}"))
                    progress.update(task, description="failed", completed=2)

    console.print()
    if compare and results:
        render_etf_comparison(results)
    else:
        for idx, data in enumerate(results):
            if idx:
                console.print(Rule(style=PALETTE["brand"]))
            render_etf_report(data, sections)
        if len(results) > 1:
            console.print(Rule("Cross-ETF Summary", style=PALETTE["brand"]))
            render_etf_comparison(results)

    if failures:
        table = Table(show_header=True, header_style=f"bold {PALETTE['bad']}", box=box.SIMPLE_HEAVY, expand=True)
        table.add_column("Ticker", width=12)
        table.add_column("Failure")
        for ticker, reason in failures:
            table.add_row(ticker, reason)
        console.print(Panel(table, title="❌ Failed Tickers", border_style=PALETTE["bad"], box=box.ROUNDED))

    return 0 if results else 1


# =============================================================================
# Typer entrypoint
# =============================================================================


def main(
    tickers: Optional[List[str]] = typer.Argument(
        None,
        metavar="[TICKER]...",
        help="Treat tickers as stocks by default. Use --etf for ETFs.",
    ),
    etf: bool = typer.Option(
        False,
        "--etf",
        help="Use the ETF pipeline. Without this flag, tickers are treated as stocks.",
    ),
    compare: bool = typer.Option(
        False,
        "--compare",
        "-c",
        help="Display a comparison matrix for multiple tickers.",
    ),
    sections: Optional[str] = typer.Option(
        None,
        "--sections",
        "-s",
        help="Comma-separated sections. Default: all available sections.",
    ),
    timeframes: str = typer.Option(
        "1m,3m,6m,12m",
        "--timeframes",
        "-t",
        help="Stock analysis timeframes: any combination of 1m,3m,6m,12m.",
    ),
    period: str = typer.Option(
        "5y",
        "--period",
        help="Historical OHLCV period for the technical engine (default 5y).",
    ),
    interval: str = typer.Option(
        "1d",
        "--interval",
        help="Interval OHLCV (default 1d).",
    ),
    price: bool = typer.Option(
        True,
        "--price/--no-price",
        help="Aktif/nonaktifkan historical OHLCV. --no-price menghasilkan fundamental-only analysis.",
    ),
    dump_json: bool = typer.Option(
        False,
        "--json",
        help="Save raw data and the analysis report as JSON.",
    ),
    output_dir: Path = typer.Option(
        Path("."),
        "--output-dir",
        "-o",
        help="JSON/debug HTML output directory.",
        file_okay=False,
        dir_okay=True,
    ),
    cache: bool = typer.Option(
        True,
        "--cache/--no-cache",
        help="Aktif/nonaktifkan cache in-memory MOMO.",
    ),
    timeout: float = typer.Option(
        15.0,
        "--timeout",
        min=1.0,
        help="Timeout HTTP StockAnalysis dalam detik.",
    ),
    retries: int = typer.Option(
        3,
        "--retries",
        min=1,
        max=10,
        help="Jumlah retry HTTP.",
    ),
    workers: int = typer.Option(
        6,
        "--workers",
        min=1,
        max=20,
        help="Maximum workers for parallel stock section fetching.",
    ),
    debug_html: bool = typer.Option(
        False,
        "--debug-html",
        help="Save raw HTML for debugging the MOMO parser.",
    ),
    compact: bool = typer.Option(
        False,
        "--compact",
        help="Compact stock output; hide business, news, and professional interpretation details.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Aktifkan logging detail MOMO.",
    ),
) -> None:
    """[bold magenta]MOMO Professional Analyzer[/bold magenta]

    The default asset is a stock::

        python main.py AAPL

    For ETFs, use the flag::

        python main.py --etf VOO
    """
    start = time.monotonic()

    normalized = _normalize_tickers(tickers or [])
    if not normalized:
        # Keep invocation convenient without arguments while retaining stocks as the default.
        from rich.prompt import Prompt

        raw = Prompt.ask(
            f"[bold {PALETTE['info']}]Ticker {'ETF' if etf else 'stock'}[/]",
            default="VOO" if etf else "AAPL",
        )
        normalized = _normalize_tickers([raw])

    if not normalized:
        raise typer.Exit(code=2)

    if verbose:
        enable_logging(logging.DEBUG)

    output_dir = output_dir.expanduser().resolve()
    if dump_json or debug_html:
        output_dir.mkdir(parents=True, exist_ok=True)

    config = MomoConfig(
        timeout=timeout,
        max_retries=retries,
        use_cache=cache,
        max_workers=workers,
        debug_dir=output_dir,
    )

    asset_type = "ETF" if etf else "Stock"
    console.print(render_banner(asset_type, normalized))

    try:
        if etf:
            etf_sections = _expand_etf_sections([x.lower() for x in _parse_csv(sections)] or ["all"])
            exit_code = run_etf_pipeline(
                normalized,
                config=config,
                sections=etf_sections,
                dump_json=dump_json,
                output_dir=output_dir,
                debug_html=debug_html,
                compare=compare,
            )
        else:
            stock_sections = _stock_sections_from_option(sections)
            tf = _timeframes_from_option(timeframes)
            exit_code = run_stock_pipeline(
                normalized,
                config=config,
                sections=stock_sections,
                timeframes=tf,
                period=period,
                interval=interval,
                use_price=price,
                dump_json=dump_json,
                output_dir=output_dir,
                debug_html=debug_html,
                compare=compare,
                compact=compact,
            )
    except typer.BadParameter as exc:
        console.print(Panel(str(exc), title="Invalid option", border_style=PALETTE["bad"]))
        raise typer.Exit(code=2)
    except KeyboardInterrupt:
        console.print("\n[bright_yellow]Cancelled by the user.[/bright_yellow]")
        raise typer.Exit(code=130)

    elapsed = time.monotonic() - start
    status_style = PALETTE["good"] if exit_code == 0 else PALETTE["bad"]
    console.print(
        Panel(
            Align.center(Text(f"Finished in {elapsed:.1f}s", style=f"bold {status_style}")),
            border_style=status_style,
            box=box.SQUARE,
        )
    )
    if exit_code:
        raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    typer.run(main)
