from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional
from ..core.validation import _validate_ticker
from ..extractors.normalizers import _dedupe_news, _now_iso

def assemble_stock_data(
    ticker: str,
    sections: Mapping[str, Dict[str, Any]],
    errors: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Combine all stock sections into a stable hierarchical schema."""
    ticker = _validate_ticker(ticker)
    overview = sections.get("overview") or {}
    company = sections.get("company") or {}
    dividends = sections.get("dividends") or {}
    revenue = sections.get("revenue") or {}

    news = _dedupe_news(
        overview.get("news") or [],
        revenue.get("news") or [],
        dividends.get("news") or [],
    )

    trust = {
        name: data.get("trust")
        for name, data in sections.items()
        if isinstance(data, dict) and data.get("trust") is not None
    }

    data: Dict[str, Any] = {
        "ticker": ticker,
        "assetType": "stock",
        "generatedAt": _now_iso(),
        "profile": company.get("profile") or overview.get("identity"),
        "identity": overview.get("identity") or company.get("identity"),
        "quote": overview.get("quote"),
        "fundamentals": overview.get("fundamentals"),
        "overview": overview,
        "company": company,
        "financials": {
            "overview": sections.get("financials"),
            "incomeStatement": sections.get("income_statement"),
            "balanceSheet": sections.get("balance_sheet"),
            "cashFlow": sections.get("cash_flow"),
            "ratios": sections.get("ratios"),
            "metrics": sections.get("financial_metrics"),
        },
        "analysis": {
            "statistics": sections.get("statistics"),
            "forecast": sections.get("forecast"),
            "ratings": sections.get("ratings"),
        },
        "market": {
            "marketCap": sections.get("market_cap"),
            "revenue": revenue,
            "dividends": dividends,
        },
        "businessMetrics": {
            "overview": sections.get("business_metrics"),
            "revenueBySegment": sections.get("revenue_by_segment"),
            "grossProfitByType": sections.get("gross_profit_by_type"),
            "grossMarginByType": sections.get("gross_margin_by_type"),
            "revenueByGeography": sections.get("revenue_by_geography"),
            "operatingExpenseBreakdown": sections.get("operating_expense_breakdown"),
            "additionalMetrics": {
                key: value
                for key, value in sections.items()
                if isinstance(value, dict) and value.get("_metric_detail")
            },
        },
        "news": news,
        "trust": trust,
        "_meta": {
            "source": "stockanalysis.com",
            "sectionsAvailable": sorted(sections.keys()),
            "sectionsRequested": list(sections.keys()),
        },
    }
    if errors:
        data["errors"] = errors
    return data
