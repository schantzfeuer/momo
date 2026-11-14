from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from ...assemblers.stock import assemble_stock_data
from ...core.exceptions import MomoError, MomoValidationError
from .routes import STOCK_SECTION_ROUTES, _ticker_from_stock_html, detect_stock_section_from_html, parse_stock_section_html

def parse_stock_snapshot(
    pages: Mapping[str, str],
    ticker: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse a collection of downloaded stock HTML pages without network access.

    `pages` may use Momo section keys or arbitrary keys/file names; unknown
    keys are detected from the canonical URL.
    """
    parsed: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    inferred_ticker = ticker.upper() if ticker else None

    for key, html in pages.items():
        section = key if key in STOCK_SECTION_ROUTES else detect_stock_section_from_html(html)
        if not section:
            errors.append({"section": str(key), "error": "Could not detect a section from the HTML."})
            continue
        if inferred_ticker is None:
            inferred_ticker = _ticker_from_stock_html(html)
        try:
            parsed[section] = parse_stock_section_html(section, html)
        except MomoError as exc:
            errors.append({"section": section, "error": str(exc)})
        except Exception as exc:
            errors.append({"section": section, "error": f"Unexpected error: {exc}"})

    if not inferred_ticker:
        raise MomoValidationError("Ticker could not be inferred from the HTML snapshot; provide it explicitly.")
    return assemble_stock_data(inferred_ticker, parsed, errors or None)

def parse_stock_directory(directory: Path, ticker: Optional[str] = None) -> Dict[str, Any]:
    """Parse all .html files in a folder produced by the `main.py` downloader."""
    directory = Path(directory)
    if not directory.exists() or not directory.is_dir():
        raise MomoValidationError(f"HTML directory not found: {directory}")
    pages: Dict[str, str] = {}
    for path in sorted(directory.glob("*.html")):
        pages[path.name] = path.read_text(encoding="utf-8", errors="replace")
    if not pages:
        raise MomoValidationError(f"No .html files found in {directory}")
    return parse_stock_snapshot(pages, ticker=ticker)
