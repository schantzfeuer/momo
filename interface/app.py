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
    _normalize_tickers,
    _parse_csv,
)
from .etf import _expand_etf_sections, run_etf_pipeline
from .stock import _stock_sections_from_option, _timeframes_from_option, run_stock_pipeline

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
        help="Enable or disable historical OHLCV. --no-price produces a fundamental-only analysis.",
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
        help="Enable or disable the in-memory MOMO cache.",
    ),
    timeout: float = typer.Option(
        15.0,
        "--timeout",
        min=1.0,
        help="StockAnalysis HTTP timeout in seconds.",
    ),
    retries: int = typer.Option(
        3,
        "--retries",
        min=1,
        max=10,
        help="Number of HTTP retries.",
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
        help="Pack stock report sections more aggressively without hiding any report content.",
    ),
    visual: bool = typer.Option(
        False,
        "--visual",
        help="Create and open a standalone visual stock chart report.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable detailed MOMO logging.",
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

    if visual and etf:
        raise typer.BadParameter("--visual is available for stocks only; remove --etf.")
    if visual and not price:
        raise typer.BadParameter("--visual requires historical price data; remove --no-price.")

    if verbose:
        enable_logging(logging.DEBUG)

    output_dir = output_dir.expanduser().resolve()
    if dump_json or debug_html or visual:
        output_dir.mkdir(parents=True, exist_ok=True)

    config = MomoConfig(
        timeout=timeout,
        max_retries=retries,
        use_cache=cache,
        max_workers=workers,
        debug_dir=output_dir,
    )

    asset_type = "ETF" if etf else "Stock"
    # Stock and comparison modes keep the generic banner. A normal ETF report
    # owns the whole viewport and renders its identity inside the dashboard header.
    if not etf or compare:
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
                visual=visual,
            )
    except typer.BadParameter as exc:
        console.print(Panel(str(exc), title="Invalid option", border_style=PALETTE["bad"]))
        raise typer.Exit(code=2)
    except KeyboardInterrupt:
        console.print("\n[bright_yellow]Cancelled by the user.[/bright_yellow]")
        raise typer.Exit(code=130)

    elapsed = time.monotonic() - start
    status_style = PALETTE["good"] if exit_code == 0 else PALETTE["bad"]
    # The single-ETF dashboard already contains its own compact status/footer.
    if not (etf and not compare):
        console.print(Rule(f"Finished in {elapsed:.1f}s", style=status_style))
    if exit_code:
        raise typer.Exit(code=exit_code)
