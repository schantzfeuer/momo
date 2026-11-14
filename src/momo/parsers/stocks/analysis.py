from __future__ import annotations
from typing import Any, Dict
from ...core.exceptions import MomoParseError
from ...extractors.normalizers import _columnar_to_rows, _normalize_statistics_section
from ...extractors.sveltekit import _extract_embedded_data
from .overview import _stock_identity

def parse_stock_statistics_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded statistics data was not found.")
    names = [
        "valuation", "dates", "shares", "ratios", "evRatios", "financialPosition",
        "financialEfficiency", "taxes", "stockPrice", "shortSelling", "incomeStatement",
        "balanceSheet", "cashFlow", "margins", "dividends", "analystForecasts",
        "stockSplits", "scores", "fairValue",
    ]
    sections = {name: _normalize_statistics_section(embedded.get(name)) for name in names}
    return {
        "identity": _stock_identity(embedded),
        "sections": sections,
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_forecast_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded forecast data was not found.")
    estimates = embedded.get("estimates") or {}
    tables = estimates.get("table") or {}
    normalized_tables: Dict[str, Any] = {}
    for period_name, period_data in tables.items():
        normalized_tables[period_name] = {
            "rows": _columnar_to_rows(period_data),
            "rawData": period_data,
        }
    return {
        "identity": _stock_identity(embedded),
        "title": embedded.get("title"),
        "consensus": embedded.get("currentRatings") or {},
        "priceTargets": embedded.get("priceTargets") or {},
        "targetHistory": embedded.get("targets") or {},
        "recommendationHistory": embedded.get("recommendations") or [],
        "recentRatings": embedded.get("ratings") or [],
        "estimates": {
            "stats": estimates.get("stats") or {},
            "tables": normalized_tables,
            "charts": embedded.get("estimatesCharts") or {},
            "source": embedded.get("estimatesSource"),
        },
        "meta": embedded.get("meta") or {},
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_ratings_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded ratings data was not found.")
    return {
        "identity": _stock_identity(embedded),
        "summary": (embedded.get("widget") or {}).get("all") or {},
        "ratings": embedded.get("ratings") or [],
        "meta": embedded.get("meta") or {},
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_market_cap_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded market-cap data was not found.")
    return {
        "identity": _stock_identity(embedded),
        "summary": embedded.get("infoBox"),
        "stats": embedded.get("stats") or {},
        "performance": embedded.get("performance"),
        "history": embedded.get("tables") or {},
        "peers": embedded.get("peers") or [],
        "meta": embedded.get("meta") or {},
        "source": embedded.get("source"),
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_revenue_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded revenue data was not found.")
    return {
        "identity": _stock_identity(embedded),
        "summary": embedded.get("info_box"),
        "stats": embedded.get("stats") or {},
        "history": embedded.get("data") or {},
        "peers": embedded.get("peers") or [],
        "news": embedded.get("news") or [],
        "meta": embedded.get("meta") or {},
        "source": embedded.get("source"),
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }
