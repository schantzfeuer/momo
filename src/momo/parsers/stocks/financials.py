from __future__ import annotations
from typing import Any, Dict, List
from ...core.exceptions import MomoParseError
from ...extractors.normalizers import _columnar_to_rows, _normalize_financial_section
from ...extractors.sveltekit import _extract_embedded_data
from .overview import _stock_identity

def parse_stock_financials_overview_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded financial overview data was not found.")
    return {
        "identity": _stock_identity(embedded),
        "statement": embedded.get("statement") or "overview",
        "period": embedded.get("period"),
        "head": embedded.get("head") or {},
        "details": embedded.get("details") or {},
        "sections": [
            _normalize_financial_section(section)
            for section in (embedded.get("sections") or [])
            if isinstance(section, dict)
        ],
        "source": embedded.get("source"),
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_financial_statement_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded financial statement data was not found.")
    financial_data = embedded.get("financialData") or {}
    return {
        "identity": _stock_identity(embedded),
        "statement": embedded.get("statement"),
        "period": embedded.get("period"),
        "head": embedded.get("head") or {},
        "details": embedded.get("details") or {},
        "columns": embedded.get("map") or [],
        "rows": _columnar_to_rows(financial_data),
        "rawData": financial_data,
        "prior": embedded.get("prior"),
        "ttmPrior": embedded.get("ttmPrior"),
        "ttmGrowth": embedded.get("ttmGrowth"),
        "sections": embedded.get("sections"),
        "source": embedded.get("source"),
        "availableSources": embedded.get("availableSources"),
        "defaultSource": embedded.get("defaultSource"),
        "selectedSource": embedded.get("selectedSource"),
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_financial_metrics_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded financial metrics data was not found.")
    financial_data = embedded.get("financialData") or {}
    dates = financial_data.get("datekey") or []
    fiscal_years = financial_data.get("fiscalYear") or []
    fiscal_quarters = financial_data.get("fiscalQuarter") or []
    categories = financial_data.get("categories") or {}
    normalized_categories: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(categories, dict):
        for category, metric_map in categories.items():
            if not isinstance(metric_map, dict):
                continue
            rows: List[Dict[str, Any]] = []
            size = max([len(v) for v in metric_map.values() if isinstance(v, list)] + [len(dates)])
            for i in range(size):
                row: Dict[str, Any] = {
                    "datekey": dates[i] if i < len(dates) else None,
                    "fiscalYear": fiscal_years[i] if i < len(fiscal_years) else None,
                    "fiscalQuarter": fiscal_quarters[i] if i < len(fiscal_quarters) else None,
                }
                for metric_id, values in metric_map.items():
                    if isinstance(values, list):
                        row[metric_id] = values[i] if i < len(values) else None
                rows.append(row)
            normalized_categories[str(category)] = rows
    return {
        "identity": _stock_identity(embedded),
        "statement": embedded.get("statement") or "metrics",
        "period": embedded.get("period"),
        "head": embedded.get("head") or {},
        "details": embedded.get("details") or {},
        "columns": embedded.get("map") or [],
        "categoryNames": financial_data.get("categoryNames") or [],
        "metricOrderByCategory": financial_data.get("metricOrderByCategory") or {},
        "categories": normalized_categories,
        "rawData": financial_data,
        "source": embedded.get("source"),
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }
