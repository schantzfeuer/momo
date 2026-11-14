"""ETF and stock HTML parsers."""
from .etf import parse_dividend_html, parse_holdings_html, parse_overview_html
from .stocks.routes import STOCK_SECTION_ROUTES, detect_stock_section_from_html, parse_stock_section_html
from .stocks.snapshot import parse_stock_directory, parse_stock_snapshot
from .stocks.overview import parse_stock_company_html, parse_stock_overview_html
from .stocks.financials import parse_stock_financial_metrics_html, parse_stock_financial_statement_html, parse_stock_financials_overview_html
from .stocks.analysis import parse_stock_forecast_html, parse_stock_market_cap_html, parse_stock_ratings_html, parse_stock_revenue_html, parse_stock_statistics_html
from .stocks.metrics import parse_stock_business_metrics_html, parse_stock_metric_detail_html
__all__ = [name for name in globals() if name.startswith("parse_") or name in {"STOCK_SECTION_ROUTES", "detect_stock_section_from_html"}]
