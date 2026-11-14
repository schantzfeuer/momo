"""MOMO command-line entry point."""

import sys
from pathlib import Path

# Support running the CLI directly from a source checkout using a src layout.
_source_root = Path(__file__).resolve().parent / "src"
if (_source_root / "momo").is_dir() and str(_source_root) not in sys.path:
    sys.path.insert(0, str(_source_root))

from interface.common import *  # noqa: F401,F403
from interface.etf import *  # noqa: F401,F403
from interface.stock import *  # noqa: F401,F403
from interface.common import (
    _company_label,
    _deep_get,
    _display,
    _json_dump,
    _money,
    _normalize_tickers,
    _num,
    _parse_csv,
    _pct,
    _print_adaptive_pair,
    _score,
    _style_for_number,
    _style_for_score,
    _style_for_stance,
)
from interface.etf import (
    _build_etf_dashboard,
    _etf_breakdown_panel,
    _etf_breakdown_rows,
    _etf_dividend_panel,
    _etf_footer,
    _etf_header_panel,
    _etf_holdings_panel,
    _etf_kpi_panel,
    _etf_news_panel,
    _etf_performance_panel,
    _expand_etf_sections,
)
from interface.stock import _render_bullets, _stock_sections_from_option, _timeframes_from_option
from interface.app import main

if __name__ == "__main__":
    import typer

    typer.run(main)
