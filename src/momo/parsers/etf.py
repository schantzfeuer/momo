from __future__ import annotations
import re
from typing import Any, Dict, Mapping
from bs4 import BeautifulSoup as bs
from ..core.config import logger
from ..extractors.html_fallback import _fallback_history_table, _fallback_holdings_table, _fallback_news_list, _fallback_summary_and_metrics, _fallback_trust_info
from ..extractors.normalizers import _normalize_date_field
from ..extractors.sveltekit import _extract_embedded_data

def _nav_from_profile(embedded: Mapping[str, Any], info: Mapping[str, Any]) -> Any:
    keys = ("nav", "netAssetValue", "netAssetValuePerShare", "net_asset_value", "net_asset_value_per_share")
    for source in (embedded, info):
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value

    info_table = embedded.get("infoTable")
    if isinstance(info_table, Mapping):
        rows = list(info_table.items())
    elif isinstance(info_table, list):
        rows = info_table
    else:
        rows = []

    for row in rows:
        if isinstance(row, Mapping):
            label = row.get("label") or row.get("name") or row.get("key")
            value = row.get("value")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            label, value = row[0], row[1]
        else:
            continue
        normalized_label = re.sub(r"[^a-z]", "", str(label or "").lower())
        if normalized_label in {
            "nav",
            "navpershare",
            "netassetvalue",
            "netassetvaluepershare",
            "netassetvaluenav",
            "netassetvaluenavpershare",
        }:
            if value is not None and value != "":
                return value
    return None

def parse_overview_html(html: str) -> Dict[str, Any]:
    soup = bs(html, "html.parser")
    embedded = _extract_embedded_data(html)

    info = embedded.get("info") or {}
    quote = info.get("quote") or {}

    profile = {
        "name": info.get("name"),
        "type": info.get("type"),
        "subtype": info.get("subtype"),
        "exchange": info.get("exchange"),
        "provider": info.get("provider"),
        "providerPage": embedded.get("provider_page"),
        "inception": _normalize_date_field("inception", embedded.get("inception") or info.get("inception")),
        "expenseRatio": embedded.get("expenseRatio"),
        "aum": embedded.get("aum"),
        "nav": _nav_from_profile(embedded, info),
        "peRatio": embedded.get("peRatio"),
        "sharesOutstanding": embedded.get("sharesOut"),
        "beta": embedded.get("beta"),
        "holdingsCount": embedded.get("holdings"),
        "description": embedded.get("description"),
        "infoTable": embedded.get("infoTable"),
        "website": embedded.get("etf_website"),
    }

    # Field mapping note (verified by matching text actually displayed on the
    # page rather than guessing field names):
    #   p=current price, o=open, h=high, l=low, cl=previous close,
    #   c=change (dollars), cp=change (percent), u=timestamp "at close",
    #   ms=status market, h52/l52=52-week high/low, v=volume, td=tanggal trading
    #   ep=after-hours price, ec/ecp=after-hours change (dollars/percent),
    #   es=label sesi ("After-hours"), eu=waktu after-hours
    quote_out = {
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
        "raw": quote,  # Preserve the complete original field for future details.
    }

    dividends_preview = {
        "yield": embedded.get("dividendYield"),
        "annualDividend": embedded.get("dps"),
        "exDividendDate": _normalize_date_field("exDividendDate", embedded.get("exDivDate")),
        "payoutFrequency": embedded.get("payoutFrequency"),
        "payoutRatio": embedded.get("payoutRatio"),
        "recentHistory": embedded.get("dividendTable"),
    }

    holdings_preview = embedded.get("holdingsTable") or {}

    result: Dict[str, Any] = {
        "profile": profile,
        "quote": quote_out,
        "priceChangesHistorical": embedded.get("changes"),
        "performanceTotalReturn": embedded.get("performance"),
        "dividendsPreview": dividends_preview,
        "holdingsPreview": holdings_preview,
        "news": embedded.get("news", {}).get("data") if isinstance(embedded.get("news"), dict) else embedded.get("news"),
        "trust": embedded.get("trust"),
        "_source": "embedded_json" if embedded else "html_fallback",
    }

    # Fallback when no JSON data is found on the page.
    if not embedded:
        logger.info("parse_overview_html: using the HTML fallback (the page structure may have changed).")
        html_fallback = _fallback_summary_and_metrics(soup)
        result["profile"]["description"] = result["profile"].get("description") or html_fallback.get("summary")
        result["profile"]["metrics_fallback"] = html_fallback.get("metrics")
        result["trust"] = result["trust"] or _fallback_trust_info(soup)

    return result

def parse_holdings_html(html: str) -> Dict[str, Any]:
    soup = bs(html, "html.parser")
    embedded = _extract_embedded_data(html)

    if embedded:
        holdings_table = embedded.get("holdingsTable") or {}
        table_rows = holdings_table.get("data", []) if isinstance(holdings_table, Mapping) else []
        holdings_source = table_rows if isinstance(table_rows, list) and table_rows else embedded.get("holdings")
        if not isinstance(holdings_source, list):
            holdings_source = []
        holdings_list = [
            {
                "no": h.get("no"),
                "symbol": (h.get("s") or h.get("symbol") or "").lstrip("$"),
                "name": h.get("n") or h.get("name"),
                "company": h.get("n") or h.get("company") or h.get("name"),
                "weight": h.get("holdingWeight", h.get("as", h.get("weight"))),
                "shares": h.get("holdingShares", h.get("sh", h.get("shares"))),
                "price": h.get("price", h.get("p")),
            }
            for h in holdings_source[:25]
            if isinstance(h, Mapping)
        ]
        result: Dict[str, Any] = {
            "asOfDate": _normalize_date_field("asOfDate", embedded.get("date")),
            "totalHoldingsCount": embedded.get("count"),
            "infoTable": embedded.get("infoTable"),
            "summary": embedded.get("infoBox"),
            "holdings": holdings_list,
            "sectors": embedded.get("sectors"),
            "countries": embedded.get("countries"),
            "allocationChartData": embedded.get("allocationChartData"),
            "news": embedded.get("news"),
            "trust": embedded.get("trust"),
            "_source": "embedded_json",
        }
    else:
        logger.info("parse_holdings_html: using the HTML fallback (the page structure may have changed).")
        summary = _fallback_summary_and_metrics(soup)
        result = {
            "asOfDate": None,
            "totalHoldingsCount": (summary.get("metrics") or {}).get("Total Holdings"),
            "infoTable": summary.get("metrics"),
            "summary": summary.get("summary"),
            "holdings": _fallback_holdings_table(soup),
            "sectors": None,
            "countries": None,
            "allocationChartData": None,
            "news": None,
            "trust": _fallback_trust_info(soup),
            "_source": "html_fallback",
        }

    return result

def parse_dividend_html(html: str) -> Dict[str, Any]:
    soup = bs(html, "html.parser")
    embedded = _extract_embedded_data(html)

    if embedded:
        info_table = embedded.get("infoTable") or {}
        result: Dict[str, Any] = {
            "summary": embedded.get("infoBox"),
            "metrics": {
                "yield": info_table.get("yield"),
                "annualDividend": info_table.get("annual"),
                "exDividendDate": _normalize_date_field("exDividendDate", info_table.get("exdiv")),
                "payoutFrequency": info_table.get("frequency"),
                "payoutRatio": info_table.get("payoutRatio"),
                "dividendGrowth1Y": info_table.get("growth"),
            },
            "history": [
                {
                    "exDate": _normalize_date_field("exDate", h.get("dt")),
                    "cashAmount": h.get("amt"),
                    "recordDate": _normalize_date_field("recordDate", h.get("record")),
                    "payDate": _normalize_date_field("payDate", h.get("pay")),
                }
                for h in embedded.get("history", [])
            ],
            "news": embedded.get("news"),
            "chartData": embedded.get("chartData"),
            "chartOptions": embedded.get("chartOptions"),
            "meta": embedded.get("meta"),
            "source": embedded.get("source"),
            "availableSources": embedded.get("availableSources"),
            "trust": embedded.get("trust"),
            "_source": "embedded_json",
        }
    else:
        logger.info("parse_dividend_html: using the HTML fallback (the page structure may have changed).")
        result = {
            "summary": _fallback_summary_and_metrics(soup).get("summary"),
            "metrics": _fallback_summary_and_metrics(soup).get("metrics"),
            "history": _fallback_history_table(soup),
            "news": _fallback_news_list(soup),
            "trust": _fallback_trust_info(soup),
            "_source": "html_fallback",
        }

    return result
