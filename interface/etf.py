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
    _deep_get,
    _display,
    _json_dump,
    _pct,
    _style_for_number,
)
from .visual import _money, etf_visual_filename, write_etf_visual_report
from .view_models import assets_under_management as _assets_under_management

ETF_DASHBOARD_REVISION = "r3-2026.08.22"
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


def _build_etf_dashboard(
    data: Mapping[str, Any],
    sections: Sequence[str],
    *,
    elapsed: Optional[float] = None,
    ok_count: int = 1,
    fail_count: int = 0,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Layout:
    """Build a screenshot-first ETF dashboard that consumes the viewport.

    The wide layout intentionally mirrors the requested reference image:

        header -----------------------------------------------------------
        KPI strip --------------------------------------------------------
        holdings 50% | performance 25% | dividend 25%
        sector   25% | country     25% | news     50%
        status footer ----------------------------------------------------

    No ETF section is removed.  When width is insufficient, panels are moved
    to the next row; when height is constrained, Rich clips *inside* the fixed
    viewport instead of making the report taller than the terminal.
    """
    width = int(width or console.size.width)
    terminal_height = int(height or console.size.height)
    requested = set(sections)

    # Keep the report bounded to the visible terminal.  The old implementation
    # could grow beyond console.size.height, which defeated screenshot framing.
    dashboard_height = max(20, terminal_height)
    root = Layout(name="etf_dashboard", size=dashboard_height)
    root.split_column(
        Layout(_etf_header_panel(data, sections), name="header", size=4),
        Layout(_etf_kpi_panel(data), name="kpis", size=4),
        Layout(name="body", ratio=1),
        Layout(_etf_footer(elapsed, ok_count, fail_count), name="footer", size=3),
    )
    body = root["body"]

    holdings_panel = _etf_holdings_panel(data) if "holdings" in requested else None
    performance_panel = _etf_performance_panel(data) if "performance" in requested else None
    dividend_panel = _etf_dividend_panel(data) if "dividends" in requested else None
    sector_panel = (
        _etf_breakdown_panel(data, "sectors", "Sector", PALETTE["brand"])
        if "sectors" in requested else None
    )
    country_panel = (
        _etf_breakdown_panel(data, "countries", "Country", "bright_blue")
        if "countries" in requested else None
    )
    news_panel = _etf_news_panel(data) if "news" in requested else None

    top = [("holdings", holdings_panel, 2), ("performance", performance_panel, 1), ("dividends", dividend_panel, 1)]
    bottom = [("sectors", sector_panel, 1), ("countries", country_panel, 1), ("news", news_panel, 2)]
    top = [(n, p, r) for n, p, r in top if p is not None]
    bottom = [(n, p, r) for n, p, r in bottom if p is not None]

    def split_with_gaps(layout: Layout, panels: Sequence[Tuple[str, Any, int]]) -> None:
        children: List[Layout] = []
        for idx, (name, panel, ratio) in enumerate(panels):
            if idx:
                children.append(Layout(Text(""), name=f"{layout.name}_gap_{idx}", size=1))
            children.append(Layout(panel, name=name, ratio=ratio))
        if children:
            layout.split_row(*children)

    # Reference-image geometry.  At common screenshot widths this is a strict
    # 50/25/25 upper row and 25/25/50 lower row, with a one-column gutter.
    if width >= 140:
        upper = Layout(name="upper", ratio=3)
        lower = Layout(name="lower", ratio=2)
        body.split_column(upper, lower)
        split_with_gaps(upper, top)
        split_with_gaps(lower, bottom)
        return root

    # Medium width: preserve content but use two columns.  A section only moves
    # down when it no longer has enough horizontal room to stay readable.
    if width >= 96:
        upper = Layout(name="upper", ratio=3)
        lower = Layout(name="lower", ratio=2)
        body.split_column(upper, lower)

        if holdings_panel is not None:
            right_stack = Layout(name="upper_right", ratio=1)
            upper.split_row(Layout(holdings_panel, name="holdings", ratio=2), right_stack)
            right_items = []
            if performance_panel is not None:
                right_items.append(Layout(performance_panel, name="performance", ratio=1))
            if dividend_panel is not None:
                right_items.append(Layout(dividend_panel, name="dividends", ratio=1))
            if right_items:
                right_stack.split_column(*right_items)
        else:
            split_with_gaps(upper, top)

        allocations = [("sectors", sector_panel, 1), ("countries", country_panel, 1)]
        allocations = [(n, p, r) for n, p, r in allocations if p is not None]
        if news_panel is not None and allocations:
            alloc_layout = Layout(name="allocations", ratio=1)
            lower.split_row(alloc_layout, Layout(news_panel, name="news", ratio=1))
            split_with_gaps(alloc_layout, allocations)
        else:
            split_with_gaps(lower, bottom)
        return root

    # Narrow terminal: all sections remain present and simply flow downward.
    panels = top + bottom
    stack = Layout(name="stack")
    body.update(stack)
    if panels:
        stack.split_column(*[Layout(panel, name=name, ratio=1) for name, panel, _ in panels])
    return root


def _etf_breakdown_panel(data: Mapping[str, Any], key: str, title: str, color: str) -> Panel:
    holdings = data.get("holdings") or {}
    rows = _etf_breakdown_rows(holdings.get(key) or [], max_rows=10)
    table = Table(box=None, expand=True, padding=(0, 1), show_header=False)
    table.add_column(overflow="ellipsis", no_wrap=True)
    table.add_column(justify="right", width=10, no_wrap=True)
    for label, weight in rows:
        table.add_row(label, weight)
    return Panel(table, title=title, box=box.SQUARE, border_style=color, padding=(0, 0))


def _etf_breakdown_rows(items: Sequence[Any], max_rows: int = 10) -> List[Tuple[str, str]]:
    """Normalize sector/country rows without assuming one response layout."""
    rows: List[Tuple[str, str]] = []
    for item in list(items or [])[:max_rows]:
        if not isinstance(item, Mapping):
            continue
        keys = list(item.keys())
        if not keys:
            continue
        weight_key = next(
            (k for k in keys if str(k).lower() in {"w", "weight", "percent", "percentage", "pct"}),
            None,
        )
        label_key = next(
            (k for k in keys if k != weight_key and str(k).lower() in {"name", "n", "sector", "country", "label", "code"}),
            None,
        ) or next((k for k in keys if k != weight_key), keys[0])
        rows.append(
            (
                _display(item.get(label_key)),
                _display(item.get(weight_key)) if weight_key is not None else "—",
            )
        )
    return rows


def _etf_dividend_panel(data: Mapping[str, Any]) -> Panel:
    dividends = data.get("dividends") or {}
    metrics = dividends.get("metrics") or {}
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style="bright_blue")
    table.add_column(justify="right")
    for label, value in [
        ("Yield", dividends.get("yield")),
        ("Annual", metrics.get("annualDividend")),
        ("Ex-Date", metrics.get("exDividendDate")),
        ("Frequency", metrics.get("payoutFrequency")),
        ("Payout", metrics.get("payoutRatio")),
        ("Growth 1Y", metrics.get("dividendGrowth1Y")),
    ]:
        table.add_row(label, _display(value))
    return Panel(table, title="Dividend", box=box.SQUARE, border_style="green3", padding=(0, 0))


def _etf_footer(elapsed: Optional[float], ok_count: int, fail_count: int) -> Panel:
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=3)
    grid.add_column(ratio=2, justify="center")
    grid.add_column(ratio=3, justify="right")

    left = Text()
    left.append("● READY", style=f"bold {PALETTE['good']}")
    left.append("  ETF viewport dashboard", style=PALETTE["muted"])

    middle = Text()
    middle.append(f"{ok_count} ok", style=PALETTE["good"])
    middle.append("  ")
    middle.append(f"{fail_count} fail", style=PALETTE["bad"] if fail_count else PALETTE["muted"])

    right = Text(f"elapsed {elapsed:.1f}s" if elapsed is not None else "ready", style=PALETTE["muted"])
    grid.add_row(left, middle, right)
    return Panel(grid, box=box.SQUARE, border_style="grey50", padding=(0, 0))


def _etf_header_panel(data: Mapping[str, Any], sections: Sequence[str]) -> Panel:
    ticker = _display(data.get("ticker"))
    profile = data.get("profile") or {}
    quote = data.get("quote") or {}
    change_style = _style_for_number(quote.get("change"))

    left = Text()
    left.append(f"MOMO ETF CLI  v{MOMO_VERSION}", style=f"bold {PALETTE['brand']}")
    left.append(f"  [{ETF_DASHBOARD_REVISION}]", style="grey42")
    left.append("\n")
    left.append(ticker, style=f"bold {PALETTE['info']}")
    left.append("  ")
    left.append(_display(profile.get("name"), "ETF"), style="bold white")

    middle = Text(justify="center")
    middle.append(_display(quote.get("price")), style="bold white")
    middle.append("\n")
    middle.append(
        f"{_display(quote.get('change'))} ({_display(quote.get('changePercent'))})",
        style=change_style,
    )

    right = Text(justify="right")
    right.append(f"{len(sections)} section" + ("s" if len(sections) != 1 else ""), style="bold white")
    right.append("\n")
    right.append(f"generated {_display(data.get('generatedAt'))}", style=PALETTE["muted"])

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=5)
    grid.add_column(ratio=2, justify="center")
    grid.add_column(ratio=5, justify="right")
    grid.add_row(left, middle, right)
    return Panel(grid, box=box.SQUARE, border_style="grey70", padding=(0, 1))


def _etf_holdings_panel(data: Mapping[str, Any]) -> Panel:
    holdings = data.get("holdings") or {}
    rows = holdings.get("topHoldings") or []
    table = Table(show_header=True, header_style=f"bold {PALETTE['gold']}", box=None, expand=True, padding=(0, 1))
    table.add_column("#", width=3, no_wrap=True)
    table.add_column("Symbol", style="bold", width=9, no_wrap=True)
    table.add_column("Holding", ratio=3, overflow="ellipsis", no_wrap=True)
    table.add_column("Weight", justify="right", width=10, no_wrap=True)
    for idx, row in enumerate(rows[:12], 1):
        table.add_row(
            str(idx),
            _display(row.get("symbol")),
            _display(row.get("name")),
            _display(row.get("weight")),
        )
    subtitle_bits = []
    if holdings.get("asOfDate"):
        subtitle_bits.append(f"as of {_display(holdings.get('asOfDate'))}")
    if holdings.get("totalHoldingsCount") is not None:
        subtitle_bits.append(f"total {_display(holdings.get('totalHoldingsCount'))}")
    return Panel(
        table,
        title="Top Holdings",
        subtitle=" • ".join(subtitle_bits),
        box=box.SQUARE,
        border_style=PALETTE["gold"],
        padding=(0, 0),
    )


def _etf_kpi_panel(data: Mapping[str, Any]) -> Panel:
    profile = data.get("profile") or {}
    quote = data.get("quote") or {}
    holdings = data.get("holdings") or {}
    dividends = data.get("dividends") or {}
    metrics = dividends.get("metrics") or {}

    fields = [
        ("AUM", _money(_assets_under_management(data), compact=True), PALETTE["info"]),
        ("EXPENSE", profile.get("expenseRatio"), "bright_magenta"),
        ("DIV YIELD", dividends.get("yield"), PALETTE["good"]),
        ("52W HIGH", quote.get("week52High"), PALETTE["good"]),
        ("52W LOW", quote.get("week52Low"), PALETTE["bad"]),
        ("HOLDINGS", holdings.get("totalHoldingsCount"), "white"),
        ("FREQUENCY", metrics.get("payoutFrequency"), PALETTE["warn"]),
    ]
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in fields:
        grid.add_column(ratio=1, justify="center")
    grid.add_row(*[Text(label, style=PALETTE["muted"]) for label, _, _ in fields])
    grid.add_row(*[Text(_display(value), style=style) for _, value, style in fields])
    return Panel(grid, title="Key Metrics", box=box.SQUARE, border_style="grey50", padding=(0, 0))


def _etf_news_panel(data: Mapping[str, Any]) -> Panel:
    news = data.get("news") or []
    table = Table(box=None, expand=True, padding=(0, 1), show_header=False)
    table.add_column("Date", width=14, style="bright_blue", no_wrap=True, overflow="ellipsis")
    table.add_column("Headline", ratio=4, no_wrap=True, overflow="ellipsis")
    table.add_column("Source", width=16, no_wrap=True, overflow="ellipsis")
    for item in news[:8]:
        table.add_row(
            _display(item.get("published") or item.get("date")),
            _display(item.get("title")),
            _display(item.get("source")),
        )
    return Panel(table, title="News", box=box.SQUARE, border_style=PALETTE["accent"], padding=(0, 0))


def _etf_performance_panel(data: Mapping[str, Any]) -> Panel:
    perf = _deep_get(data, "performance", "totalReturn", default={}) or {}
    labels = {
        "tr1m": "1 Month",
        "1m": "1 Month",
        "trytd": "Year-to-Date",
        "ytd": "Year-to-Date",
        "tr1y": "1 Year",
        "tr3y": "CAGR 3Y",
        "tr5y": "5 Years CAGR",
        "cagr5y": "5 Years CAGR",
        "tr10y": "10 Years CAGR",
        "cagr10y": "10 Years CAGR",
        "trmax": "CAGR Since Inception",
        "cagrmax": "CAGR Since Inception",
        "max": "CAGR Since Inception",
    }
    table = Table(box=None, expand=True, padding=(0, 1), show_header=False)
    table.add_column(style="bright_blue")
    table.add_column(justify="right")
    if isinstance(perf, Mapping):
        for key, value in perf.items():
            normalized_key = str(key).lower()
            table.add_row(
                labels.get(normalized_key, labels.get(str(key), str(key))),
                Text(_pct(value), style=_style_for_number(value)),
            )
    return Panel(table, title="Performance", box=box.SQUARE, border_style=PALETTE["good"], padding=(0, 0))


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
    etf = client.ETF(ticker)
    needed_pages: set[str] = set()
    for section in sections:
        needed_pages |= ETF_PAGES_FOR_SECTION.get(section, set())

    if needed_pages >= {"overview", "holdings", "dividends"}:
        return etf.get_data()

    overview: Dict[str, Any] = {}
    holdings: Dict[str, Any] = {}
    dividends: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    if "overview" in needed_pages:
        try:
            overview = etf.get_overview()
        except MomoError as exc:
            errors.append({"section": "overview", "error": str(exc)})
    if "holdings" in needed_pages:
        try:
            holdings = etf.get_holdings()
        except MomoError as exc:
            errors.append({"section": "holdings", "error": str(exc)})
    if "dividends" in needed_pages:
        try:
            dividends = etf.get_dividends()
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
            _display(div.get("yield")), _display(profile.get("expenseRatio")),
            _money(_assets_under_management(data), compact=True),
            _display(hold.get("totalHoldingsCount")),
        )
    console.print(Panel(table, title="🔀 Cross-ETF Comparison", border_style=PALETTE["brand"], box=box.ROUNDED))


def render_etf_report(
    data: Mapping[str, Any],
    sections: Sequence[str],
    *,
    elapsed: Optional[float] = None,
    ok_count: int = 1,
    fail_count: int = 0,
) -> None:
    console.print(
        _build_etf_dashboard(
            data,
            sections,
            elapsed=elapsed,
            ok_count=ok_count,
            fail_count=fail_count,
        )
    )
    if data.get("errors"):
        errors = data.get("errors") or []
        error_table = Table(show_header=True, header_style=f"bold {PALETTE['bad']}", box=box.SIMPLE_HEAVY)
        error_table.add_column("Section")
        error_table.add_column("Error", overflow="fold")
        for error in errors:
            if isinstance(error, Mapping):
                error_table.add_row(_display(error.get("section")), _display(error.get("error")))
            else:
                error_table.add_row("—", _display(error))
        console.print(Panel(error_table, title="❌ ETF Data Errors", border_style=PALETTE["bad"], box=box.ROUNDED))


def run_etf_pipeline(
    tickers: Sequence[str],
    config: MomoConfig,
    sections: Sequence[str],
    dump_json: bool,
    output_dir: Path,
    debug_html: bool,
    compare: bool,
    visual: bool = False,
    period: str = "5y",
    interval: str = "1d",
    use_price: bool = True,
) -> int:
    started = time.monotonic()
    results: List[Dict[str, Any]] = []
    failures: List[Tuple[str, str]] = []
    visual_reports: List[Path] = []

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
    tasks = {ticker: progress.add_task("queued", total=2, ticker=ticker) for ticker in tickers}

    with Momo(config=config, save_debug_html=debug_html) as client:
        with progress:
            for ticker in tickers:
                task = tasks[ticker]
                try:
                    progress.update(task, description="fetching ETF data")
                    data = fetch_etf_targeted(client, ticker, sections)
                    price_data = None
                    technical: Dict[str, Any] = {"available": False, "reason": "Historical prices were not requested.", "timeframes": {}}
                    if visual:
                        data = client.ETF(ticker).get_data()
                        if use_price:
                            try:
                                from momo.engine.market_data import fetch_yahoo_history
                                price_data = fetch_yahoo_history(ticker, period=period, interval=interval)
                                technical = client.Technical(
                                    ticker, price_data=price_data, auto_fetch_price=False,
                                    timeframes=("1m", "3m", "6m", "12m"),
                                )
                            except (ImportError, MomoError, ValueError) as exc:
                                technical = {"available": False, "reason": str(exc), "timeframes": {}}
                        visual_path = output_dir / etf_visual_filename(ticker)
                        write_etf_visual_report(ticker, data, visual_path, price_data, technical)
                        visual_reports.append(visual_path)
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

    elapsed = time.monotonic() - started
    for visual_path in visual_reports:
        try:
            opened = webbrowser.open(visual_path.resolve().as_uri(), new=2)
        except OSError:
            opened = False
        console.print(
            f"[{PALETTE['good'] if opened else PALETTE['info']}]ETF dashboard "
            f"{'opened' if opened else 'saved'}:[/] {visual_path}"
        )
    if compare and results:
        render_etf_comparison(results)
    else:
        for idx, data in enumerate(results):
            if idx:
                console.print(Rule(style=PALETTE["brand"]))
            render_etf_report(
                data,
                sections,
                elapsed=elapsed,
                ok_count=len(results),
                fail_count=len(failures),
            )
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

    run_summary = {
        "assetType": "etf",
        "exitReason": "ok" if results and not failures else ("partial" if results else "failed"),
        "attempted": list(tickers),
        "available": [data.get("ticker") for data in results],
        "failed": {ticker: reason for ticker, reason in failures},
        "providerLatencyMs": {},
    }
    _json_dump(output_dir / "momo_run_summary.json", run_summary)
    return 0 if results else 1
