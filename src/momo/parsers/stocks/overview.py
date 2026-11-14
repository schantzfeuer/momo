from __future__ import annotations
from typing import Any, Dict
from ...core.exceptions import MomoParseError
from ...extractors.normalizers import _list_table_to_dict, _normalize_date_field, _strip_html_text
from ...extractors.sveltekit import _extract_embedded_data

def _stock_identity(embedded: Dict[str, Any]) -> Dict[str, Any]:
    info = embedded.get("info") or {}
    return {
        "symbol": info.get("symbol") or info.get("ticker"),
        "ticker": info.get("ticker") or info.get("symbol"),
        "name": info.get("nameFull") or info.get("name"),
        "shortName": info.get("name"),
        "commonName": info.get("common_name"),
        "exchange": info.get("exchange"),
        "currency": info.get("curr"),
        "type": info.get("type"),
        "subtype": info.get("subtype"),
        "cik": info.get("cik"),
        "ipoDate": _normalize_date_field("ipoDate", info.get("ipoDate")),
    }

def _stock_quote(embedded: Dict[str, Any]) -> Dict[str, Any]:
    info = embedded.get("info") or {}
    quote = info.get("quote") or {}
    return {
        "price": quote.get("p"),
        "open": quote.get("o"),
        "high": quote.get("h"),
        "low": quote.get("l"),
        "previousClose": quote.get("cl"),
        "change": quote.get("c"),
        "changePercent": quote.get("cp"),
        "asOf": _normalize_date_field("asOf", quote.get("u")),
        "tradingDate": _normalize_date_field("tradingDate", quote.get("td")),
        "marketStatus": quote.get("ms"),
        "week52High": quote.get("h52"),
        "week52Low": quote.get("l52"),
        "volume": quote.get("v"),
        "afterHours": {
            "price": quote.get("ep"),
            "change": quote.get("ec"),
            "changePercent": quote.get("ecp"),
            "session": quote.get("es"),
            "asOf": _normalize_date_field("asOf", quote.get("eu")),
        },
        "raw": quote,
    }

def parse_stock_overview_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded stock overview data was not found.")

    analyst_target = embedded.get("analystTarget") or {}
    analyst_chart = embedded.get("analystChart") or {}
    info_table = embedded.get("infoTable") or []
    news = embedded.get("news")
    if isinstance(news, dict):
        news = news.get("data") or []

    return {
        "identity": _stock_identity(embedded),
        "quote": _stock_quote(embedded),
        "facts": _list_table_to_dict(info_table),
        "infoTable": info_table,
        "fundamentals": {
            "marketCap": embedded.get("marketCap"),
            "marketCapGrowth": embedded.get("marketCapGrowth"),
            "revenue": embedded.get("revenue"),
            "revenueType": embedded.get("revenue_type"),
            "revenueGrowth": embedded.get("revenueGrowth"),
            "netIncome": embedded.get("netIncome"),
            "netIncomeGrowth": embedded.get("netIncomeGrowth"),
            "sharesOutstanding": embedded.get("sharesOut"),
            "eps": embedded.get("eps"),
            "epsGrowth": embedded.get("epsGrowth"),
            "peRatio": embedded.get("peRatio"),
            "forwardPE": embedded.get("forwardPE"),
            "beta": embedded.get("beta"),
        },
        "dividendPreview": {
            "dividend": embedded.get("dividend"),
            "exDividendDate": _normalize_date_field("exDividendDate", embedded.get("exDividendDate")),
        },
        "analyst": {
            "consensus": embedded.get("analysts"),
            "targetText": embedded.get("target"),
            "priceTarget": analyst_target,
            "ratingsBreakdown": analyst_chart,
            "intro": embedded.get("analystIntro"),
        },
        "earningsDate": embedded.get("earningsDate"),
        "description": embedded.get("description"),
        "financialIntro": embedded.get("financialIntro"),
        "financialHistory": embedded.get("financialChart") or [],
        "performance": embedded.get("changes") or {},
        "priceChart": embedded.get("chart"),
        "news": news or [],
        "trust": embedded.get("trust"),
        "source": embedded.get("source"),
        "_source": "embedded_json",
    }

def parse_stock_company_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded company data was not found.")
    contact = dict(embedded.get("contact") or {})
    if "address" in contact:
        contact["address"] = _strip_html_text(contact["address"])
    return {
        "identity": _stock_identity(embedded),
        "profile": embedded.get("profile") or {},
        "description": _strip_html_text(embedded.get("description")),
        "descriptionHtml": embedded.get("description"),
        "contact": contact,
        "details": embedded.get("details") or {},
        "logo": embedded.get("logoURL"),
        "executives": embedded.get("executives") or [],
        "filings": embedded.get("filings") or [],
        "trust": embedded.get("trust"),
        "_source": "embedded_json",
    }
