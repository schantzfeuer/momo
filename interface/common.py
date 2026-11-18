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
from momo.core.units import normalize_financial

try:
    from momo.engine import __version__ as TECHNICAL_VERSION
    from momo.engine import fetch_yahoo_history
except ImportError:  # MOMO fundamental-only mode remains available
    TECHNICAL_VERSION = "unavailable"
    fetch_yahoo_history = None  # type: ignore[assignment]


console = Console(highlight=False, soft_wrap=False)
APP_NAME = "MOMO Professional Analyzer"
APP_VERSION = f"MOMO {MOMO_VERSION} • Technical {TECHNICAL_VERSION}"
STOCK_CLI_REVISION = "r3-2026.08.22"
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

# =============================================================================
# Generic helpers
# =============================================================================


def _display(value: Any, fallback: str = "—") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    return text if text and text.lower() not in {"none", "nan", "n/a"} else fallback


def _num(value: Any) -> Optional[float]:
    return normalize_financial(value).value


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
            if not token:
                continue
            try:
                from momo.core.validation import validate_ticker
                canonical = validate_ticker(token)
            except MomoValidationError:
                continue
            if canonical not in output:
                output.append(canonical)
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
    from momo.core.persistence import atomic_json_write
    atomic_json_write(Path(path), payload, default=str)


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
    if asset_type.upper() == "STOCK":
        left.append(f"MOMO STOCK CLI  v{MOMO_VERSION}", style=f"bold {PALETTE['brand']}")
        left.append(f"  [{STOCK_CLI_REVISION}]", style=PALETTE["muted"])
    else:
        left.append(APP_NAME, style=f"bold {PALETTE['brand']}")
        left.append("  ")
        left.append(APP_VERSION, style=PALETTE["muted"])

    right = Text(justify="right")
    right.append(asset_type.upper(), style=f"bold {PALETTE['info']}")
    right.append("  ")
    right.append(", ".join(tickers), style="bold white")

    grid = Table.grid(expand=True)
    grid.add_column(ratio=3)
    grid.add_column(ratio=2, justify="right")
    grid.add_row(left, right)
    return Panel(grid, box=box.HEAVY, border_style=PALETTE["brand"], padding=(0, 1))


def _print_adaptive_pair(
    left: Optional[Any],
    right: Optional[Any],
    *,
    min_width: int,
    ratios: Tuple[int, int] = (1, 1),
) -> None:
    """Print two report sections side-by-side when the terminal can support it.

    Each section remains on the current row while there is enough horizontal
    room. It moves below the previous section only when the terminal width is
    too small for a readable side-by-side presentation.
    """
    if left is None and right is None:
        return
    if left is None:
        console.print(right)
        return
    if right is None:
        console.print(left)
        return

    if console.size.width >= min_width:
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(ratio=ratios[0])
        grid.add_column(ratio=ratios[1])
        grid.add_row(left, right)
        console.print(grid)
    else:
        console.print(left)
        console.print(right)


# =============================================================================
# ETF pipeline + Rich rendering
# =============================================================================

