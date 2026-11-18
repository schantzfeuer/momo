"""Momo stock and ETF data collection library."""

__version__ = "3.1.7a1"

from .assemblers.etf import assemble_etf_data
from .assemblers.stock import assemble_stock_data
from .client import Momo, enable_logging, disable_logging
from .core.observability import get_metrics
from .core.exceptions import MomoClosedError
from .core.observability import logging_context
from .core.validation import resolve_ticker, ResolvedSymbol
from .core.config import MomoConfig
from .core.exceptions import (
    MomoError,
    MomoNetworkError,
    MomoParseError,
    MomoValidationError,
    ProviderNetworkError,
    RateLimitError,
    SchemaMismatchError,
    DataValidationError,
)
from .objects import ETF, ETFs, Stock, Stocks
from .parsers.etf import parse_overview_html, parse_holdings_html, parse_dividend_html
from .parsers.stocks.analysis import parse_stock_statistics_html, parse_stock_forecast_html, parse_stock_ratings_html, parse_stock_market_cap_html, parse_stock_revenue_html
from .parsers.stocks.financials import parse_stock_financials_overview_html, parse_stock_financial_statement_html, parse_stock_financial_metrics_html
from .parsers.stocks.metrics import parse_stock_business_metrics_html, parse_stock_metric_detail_html
from .parsers.stocks.overview import parse_stock_overview_html, parse_stock_company_html
from .parsers.stocks.routes import parse_stock_section_html, detect_stock_section_from_html, STOCK_SECTION_ROUTES
from .parsers.stocks.snapshot import parse_stock_snapshot, parse_stock_directory

__all__ = [
    'Momo', 'MomoConfig', 'Stock', 'Stocks', 'ETF', 'ETFs',
    'MomoError', 'MomoNetworkError', 'MomoParseError', 'MomoValidationError', 'MomoClosedError',
    'ProviderNetworkError', 'RateLimitError', 'SchemaMismatchError', 'DataValidationError',
    'enable_logging', 'disable_logging', 'get_metrics', 'logging_context', 'assemble_etf_data', 'assemble_stock_data', 'parse_overview_html',
    'resolve_ticker', 'ResolvedSymbol',
    'parse_holdings_html', 'parse_dividend_html', 'parse_stock_overview_html',
    'parse_stock_company_html', 'parse_stock_financials_overview_html',
    'parse_stock_financial_statement_html', 'parse_stock_financial_metrics_html',
    'parse_stock_statistics_html', 'parse_stock_forecast_html', 'parse_stock_ratings_html',
    'parse_stock_market_cap_html', 'parse_stock_revenue_html',
    'parse_stock_business_metrics_html', 'parse_stock_metric_detail_html',
    'parse_stock_section_html', 'parse_stock_snapshot', 'parse_stock_directory',
    'detect_stock_section_from_html', 'STOCK_SECTION_ROUTES',
]
