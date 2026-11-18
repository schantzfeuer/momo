from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol
from bs4 import BeautifulSoup as bs
from ...core.exceptions import MomoValidationError
from ...extractors.sveltekit import _extract_embedded_data
from ..etf import parse_dividend_html
from .analysis import parse_stock_forecast_html, parse_stock_market_cap_html, parse_stock_ratings_html, parse_stock_revenue_html, parse_stock_statistics_html
from .financials import parse_stock_financial_metrics_html, parse_stock_financial_statement_html, parse_stock_financials_overview_html
from .metrics import parse_stock_business_metrics_html, parse_stock_metric_detail_html
from .overview import parse_stock_company_html, parse_stock_overview_html

STOCK_SECTION_ROUTES: Dict[str, str] = {
    "overview": "",
    "financials": "financials/",
    "income_statement": "financials/income-statement/",
    "balance_sheet": "financials/balance-sheet/",
    "cash_flow": "financials/cash-flow-statement/",
    "ratios": "financials/ratios/",
    "financial_metrics": "financials/metrics/",
    "forecast": "forecast/",
    "ratings": "ratings/",
    "statistics": "statistics/",
    "market_cap": "market-cap/",
    "revenue": "revenue/",
    "business_metrics": "metrics/",
    "revenue_by_segment": "metrics/revenue-by-segment/",
    "gross_profit_by_type": "metrics/gross-profit-by-type/",
    "gross_margin_by_type": "metrics/gross-margin-by-type/",
    "revenue_by_geography": "metrics/revenue-by-geography/",
    "operating_expense_breakdown": "metrics/operating-expense-breakdown/",
    "dividends": "dividend/",
    "company": "company/",
}

_STOCK_ROUTE_TO_SECTION = {route.strip("/"): section for section, route in STOCK_SECTION_ROUTES.items()}

_STOCK_PARSERS: Dict[str, Callable[[str], Dict[str, Any]]] = {
    "overview": parse_stock_overview_html,
    "financials": parse_stock_financials_overview_html,
    "income_statement": parse_stock_financial_statement_html,
    "balance_sheet": parse_stock_financial_statement_html,
    "cash_flow": parse_stock_financial_statement_html,
    "ratios": parse_stock_financial_statement_html,
    "financial_metrics": parse_stock_financial_metrics_html,
    "forecast": parse_stock_forecast_html,
    "ratings": parse_stock_ratings_html,
    "statistics": parse_stock_statistics_html,
    "market_cap": parse_stock_market_cap_html,
    "revenue": parse_stock_revenue_html,
    "business_metrics": parse_stock_business_metrics_html,
    "revenue_by_segment": parse_stock_metric_detail_html,
    "gross_profit_by_type": parse_stock_metric_detail_html,
    "gross_margin_by_type": parse_stock_metric_detail_html,
    "revenue_by_geography": parse_stock_metric_detail_html,
    "operating_expense_breakdown": parse_stock_metric_detail_html,
    "dividends": parse_dividend_html,
    "company": parse_stock_company_html,
}

def parse_stock_section_html(section: str, html: str) -> Dict[str, Any]:
    section = section.strip().lower()
    parser = _STOCK_PARSERS.get(section)
    if parser is None:
        raise MomoValidationError(
            f"Unknown stock section: {section!r}. Choices: {', '.join(STOCK_SECTION_ROUTES)}"
        )
    data = parser(html)
    data.setdefault("_section", section)
    return data

def detect_stock_section_from_html(html: str) -> Optional[str]:
    soup = bs(html, "html.parser")
    canonical = soup.find("link", rel="canonical")
    href = canonical.get("href") if canonical else None
    if not href:
        return None
    match = re.search(r"/stocks/[^/]+/(.*)$", href)
    if not match:
        return None
    route = match.group(1).strip("/")
    return _STOCK_ROUTE_TO_SECTION.get(route)

def _ticker_from_stock_html(html: str) -> Optional[str]:
    embedded = _extract_embedded_data(html)
    info = embedded.get("info") or {}
    ticker = info.get("ticker") or info.get("symbol")
    if ticker:
        return str(ticker).upper()
    soup = bs(html, "html.parser")
    canonical = soup.find("link", rel="canonical")
    href = canonical.get("href") if canonical else ""
    match = re.search(r"/stocks/([^/]+)/", href)
    return match.group(1).upper() if match else None


class StockProvider(Protocol):
    name: str
    base_url: str

    def route(self, section: str) -> Optional[str]:
        ...

    def parse(self, section: str, html: str) -> Dict[str, Any]:
        ...


@dataclass(frozen=True)
class StockAnalysisProvider:
    name: str = "stockanalysis"
    base_url: str = "https://stockanalysis.com/stocks"

    def route(self, section: str) -> Optional[str]:
        return STOCK_SECTION_ROUTES.get(section)

    def parse(self, section: str, html: str) -> Dict[str, Any]:
        return parse_stock_section_html(section, html)
