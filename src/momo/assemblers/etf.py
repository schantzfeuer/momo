from __future__ import annotations
from typing import Any, Dict, List, Optional
from ..extractors.normalizers import _dedupe_news, _now_iso

def assemble_etf_data(
    ticker: str,
    overview: Optional[Dict[str, Any]] = None,
    holdings: Optional[Dict[str, Any]] = None,
    dividends: Optional[Dict[str, Any]] = None,
    news: Optional[List[Dict[str, Any]]] = None,
    errors: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Merge previously fetched overview/holdings/dividends results into one
    consistent ETF data structure matching `Momo.get_etf_data`.

    Useful when callers need only some pages but still require a uniform
    structure for presentation. If `news` is omitted, it is rebuilt from
    available page news.
    """
    ticker = ticker.upper()
    overview = overview or {}
    holdings = holdings or {}
    dividends = dividends or {}

    if news is None:
        news = _dedupe_news(
            overview.get("news") or [],
            holdings.get("news") or [],
            dividends.get("news") or [],
        )

    data: Dict[str, Any] = {"ticker": ticker, "assetType": "etf", "generatedAt": _now_iso()}

    data.update({
        "profile": overview.get("profile"),
        "quote": overview.get("quote"),
        "performance": {
            "totalReturn": overview.get("performanceTotalReturn"),
            "historicalPriceLevels": overview.get("priceChangesHistorical"),
        },
        "dividends": {
            "yield": (overview.get("dividendsPreview") or {}).get("yield")
                     or (dividends.get("metrics") or {}).get("yield"),
            "summary": dividends.get("summary"),
            "metrics": dividends.get("metrics"),
            "history": dividends.get("history"),
        },
        "holdings": {
            "asOfDate": holdings.get("asOfDate"),
            "totalHoldingsCount": holdings.get("totalHoldingsCount"),
            "summary": holdings.get("summary"),
            "infoTable": holdings.get("infoTable"),
            "topHoldings": holdings.get("holdings"),
            "sectors": holdings.get("sectors"),
            "countries": holdings.get("countries"),
        },
        "news": news,
        "trust": {
            "overview": overview.get("trust"),
            "holdings": holdings.get("trust"),
            "dividend": dividends.get("trust"),
        },
    })

    available_components = [
        name for name, payload in (("overview", overview), ("holdings", holdings), ("dividends", dividends))
        if isinstance(payload, dict) and bool(payload)
    ]
    data["_meta"] = {
        "source": "stockanalysis.com",
        "componentsRequested": ["overview", "holdings", "dividends"],
        "componentsAvailable": available_components,
        "componentCoveragePct": round(len(available_components) / 3.0 * 100.0, 1),
        "sourceErrorCount": len(errors or []),
    }
    if errors:
        data["errors"] = errors

    return data
