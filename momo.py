"""
=============================================================================
 MOMO - YAMAMOMO ETFS & STOCKS DATA SCRAPER
=============================================================================

Momo is a Python library for retrieving ETF and stock data 
from stockanalysis.com and returning it as structured JSON. 
It supports both module-level functions for quick access 
and a `Momo` class for more control over network settings, 
caching, and connection reuse.

Quick stock example:

    import momo
    stock = momo.get_stock_data("AAPL")
    print(stock["quote"]["price"])
    print(stock["financials"]["incomeStatement"]["rows"][0])

Quick usage (module-level functions, no instance required):

    import momo
    data = momo.get_etf_data("VYM")

Usage with more control (connection reuse, cache, custom configuration):

    from momo import Momo, MomoConfig

    with Momo(config=MomoConfig(timeout=30, max_retries=5)) as m:
        overview  = m.get_overview("VYM")
        holdings  = m.get_holdings("VYM")
        dividends = m.get_dividends("VYM")
        news      = m.get_news("VYM")
        full      = m.get_etf_data("VYM")          # combine everything
        cmp_table = m.compare(["VYM", "SCHD"])     # compare multiple ETFs

Debugging: `momo.enable_logging()` enables console logging, showing which
pages failed and when the HTML fallback was used.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import requests as req
from bs4 import BeautifulSoup as bs

__version__ = "3.1.2"

__all__ = [
    "Momo",
    "MomoConfig",
    "MomoError",
    "MomoNetworkError",
    "MomoParseError",
    "MomoValidationError",
    "enable_logging",
    "assemble_etf_data",
    "assemble_stock_data",
    "parse_overview_html",
    "parse_holdings_html",
    "parse_dividend_html",
    "parse_stock_overview_html",
    "parse_stock_company_html",
    "parse_stock_financials_overview_html",
    "parse_stock_financial_statement_html",
    "parse_stock_financial_metrics_html",
    "parse_stock_statistics_html",
    "parse_stock_forecast_html",
    "parse_stock_ratings_html",
    "parse_stock_market_cap_html",
    "parse_stock_revenue_html",
    "parse_stock_business_metrics_html",
    "parse_stock_metric_detail_html",
    "parse_stock_section_html",
    "parse_stock_snapshot",
    "parse_stock_directory",
    "detect_stock_section_from_html",
    "STOCK_SECTION_ROUTES",
    "get_overview",
    "get_holdings",
    "get_dividends",
    "get_news",
    "get_etf_data",
    "get_data",
    "get_stock_section",
    "get_stock_data",
    "get_stock_overview",
    "get_stock_company",
    "get_stock_financials",
    "get_stock_statistics",
    "get_stock_forecast",
    "get_stock_ratings",
    "get_stock_market_cap",
    "get_stock_revenue",
    "get_stock_dividends",
    "get_stock_business_metrics",
    "analyze_stock_data",
    "analyze_stock",
    "compare",
    "compare_stocks",
]

logger = logging.getLogger("momo")
logger.addHandler(logging.NullHandler())


def enable_logging(level: int = logging.INFO) -> None:
    """Enable momo module logging output on the console (stderr).

    Useful when retrieved data does not match expectations: logs show which
    pages failed, when the HTML fallback was used, and similar details.

        import momo
        momo.enable_logging()
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)


# =============================================================================
# 0. EXCEPTIONS & CONFIGURATION
# =============================================================================

class MomoError(Exception):
    """Base class for all errors raised by momo."""


class MomoNetworkError(MomoError):
    """Raised when fetching a page fails after all retries are exhausted."""


class MomoParseError(MomoError):
    """Raised when parsing HTML/JSON on a page fails."""


class MomoValidationError(MomoError):
    """Raised when input is invalid, such as an empty or malformed ticker."""


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class MomoConfig:
    """Collect all customizable settings here instead of scattering them as
    module constants, allowing each `Momo` instance to use different settings.
    """

    # `base_url` is retained as the ETF URL base for v2 compatibility.
    base_url: str = "https://stockanalysis.com/etf"
    stock_base_url: str = "https://stockanalysis.com/stocks"
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = "en-US,en;q=0.9"
    timeout: float = 15.0
    max_retries: int = 3
    retry_backoff_base: float = 1.0
    use_cache: bool = True
    cache_ttl: float = 300.0  # seconds
    max_workers: int = 6
    debug_dir: Optional[Path] = None

    @property
    def headers(self) -> Dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept-Language": self.accept_language}


_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z]{1,3})?$")


def _validate_ticker(ticker: str) -> str:
    """Normalize and validate a ticker before sending an HTTP request."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise MomoValidationError("Ticker cannot be empty.")
    cleaned = ticker.strip().upper()
    if not _TICKER_RE.match(cleaned):
        raise MomoValidationError(f"Ticker format appears invalid: {ticker!r}")
    return cleaned


# =============================================================================
# 1. NETWORK LAYER
# =============================================================================

def _fetch_html(session: req.Session, url: str, config: MomoConfig) -> str:
    """Fetch raw HTML from a URL with automatic exponential-backoff retries
    for transient network errors."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response = session.get(url, timeout=config.timeout)
            response.raise_for_status()
            return response.text
        except req.RequestException as e:
            last_exc = e
            logger.debug(
                "Attempt %d/%d failed for %s (%s)", attempt, config.max_retries, url, e
            )
            if attempt < config.max_retries:
                time.sleep(config.retry_backoff_base * (2 ** (attempt - 1)))
    raise MomoNetworkError(
        f"Failed to fetch {url} after {config.max_retries} attempts: {last_exc}"
    ) from last_exc


# =============================================================================
# 2. EMBEDDED JSON DATA PARSER (embedded SvelteKit data) — primary path
# =============================================================================
#
# stockanalysis.com embeds page data as a JavaScript object literal
# (not pure JSON) inside <script>, for example:
#
#   kit.start(app, element, { node_ids: [...], data: [
#       {type:"data", data:{session:null, ...}},
#       {type:"data", data:{info:{...}}},
#       {type:"data", data:{aum:"$81.02B", trust:{...}, performance:{...}, ...}}
#   ]})
#
# This object uses unquoted keys, leading-dot numbers (.02), `void 0`, and
# similar syntax, so it cannot be passed directly to json.loads. The functions
# below extract and normalize the `data: [...]` array into valid JSON.

def _extract_data_array_text(script_text: str) -> Optional[str]:
    """Find `data: [ ... ]` text inside a SvelteKit script, tracking brackets
    while respecting string literals."""
    idx = script_text.find("data:")
    if idx == -1:
        return None
    start = idx + len("data:")
    while start < len(script_text) and script_text[start].isspace():
        start += 1
    if start >= len(script_text) or script_text[start] != "[":
        return None

    depth = 0
    in_str = False
    str_char = ""
    escape = False
    i = start
    n = len(script_text)
    while i < n:
        ch = script_text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_char:
                in_str = False
        else:
            if ch in ("'", '"'):
                in_str = True
                str_char = ch
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return script_text[start:i + 1]
        i += 1
    return None  # unbalanced brackets


_IDENTIFIER_KEY_RE = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')


def _js_object_literal_to_json(js_text: str) -> str:
    """Convert a permissive JavaScript object literal into valid JSON text.

    Penanganan:
      - `void 0` / `undefined` / `NaN`      -> null
      - key tanpa kutip (mis. `n:"IBM"`)    -> diberi kutip ("n":"IBM")
      - angka berawalan titik (`.02`)       -> diberi 0 di depan (0.02)
    - trailing commas before `}`/`]`      -> removed
    String literals are preserved as-is.
    """
    text = re.sub(r"\bvoid\s+0\b", "null", js_text)
    text = re.sub(r"\bundefined\b", "null", text)
    text = re.sub(r"\bNaN\b", "null", text)

    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    str_char = ""
    escape = False

    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_char:
                in_str = False
            i += 1
            continue

        if ch in ("'", '"'):
            in_str = True
            str_char = ch
            out.append('"' if ch == "'" else ch)  # normalize single to double quotes
            i += 1
            continue

        if ch in "{,":
            out.append(ch)
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                out.append(text[j])
                j += 1
            m = _IDENTIFIER_KEY_RE.match(text[j:])
            if m and j + m.end() < n and text[j + m.end()] == ":":
                key = m.group(0)
                out.append(f'"{key}"')
                i = j + len(key)
                continue
            i = j
            continue

        out.append(ch)
        i += 1

    result = "".join(out)
    # Add a leading zero to dot-prefixed numbers after ':', '[' or ','.
    result = re.sub(r"([:\[,]\s*)(-?)\.(\d)", r"\1\g<2>0.\3", result)
    # Remove trailing commas before object/array closers.
    result = re.sub(r",(\s*[}\]])", r"\1", result)
    return result


def _extract_embedded_data(html: str) -> Dict[str, Any]:
    """Find every <script> containing `__sveltekit_`, extract its `data:[...]`
    array, normalize it to JSON, and merge each element's `.data` fields into
    one flat dict.

    Returns an empty dict on failure so callers can use the HTML/CSS fallback.
    """
    soup = bs(html, "html.parser")
    merged: Dict[str, Any] = {}

    for script in soup.find_all("script"):
        text = script.string
        if not text or "__sveltekit_" not in text:
            continue

        arr_text = _extract_data_array_text(text)
        if not arr_text:
            continue

        json_text = _js_object_literal_to_json(arr_text)
        try:
            data_list = json.loads(json_text)
        except json.JSONDecodeError:
            continue

        if not isinstance(data_list, list):
            continue

        for entry in data_list:
            if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
                merged.update(entry["data"])
        break  # relevant script found

    if not merged:
        logger.debug("Embedded JSON data was not found on the page; using the HTML/CSS fallback.")

    return merged


# =============================================================================
# 3. FALLBACK: HTML/CSS PARSER (adapted from the original user script)
#    Used only when _extract_embedded_data fails or data is incomplete.
# =============================================================================

def _fallback_trust_info(soup: bs) -> Dict[str, Any]:
    trust: Dict[str, Any] = {}
    details = soup.find("details", class_=lambda c: c and "border-blue-200" in c)
    if not details:
        return trust
    summary = details.find("summary")
    if summary:
        text = summary.get_text(separator=" ", strip=True)
        if "Last updated:" in text:
            match = re.search(r"Last updated:\s*([^,]+(?:,\s*\d{4})?)", text)
            if match:
                trust["lastUpdated"] = match.group(1).strip()
        a = summary.find("a", href=True)
        if a:
            trust["sourceName"] = a.get_text(strip=True)
            trust["sourceUrl"] = a.get("href")
    dl = details.find("dl")
    if dl:
        for dt in dl.find_all("dt"):
            if "Update frequency:" in dt.get_text():
                dd = dt.find_next("dd")
                if dd:
                    trust["updateFrequency"] = dd.get_text(strip=True)
            if "Last checked:" in dt.get_text():
                dd = dt.find_next("dd")
                if dd:
                    trust["lastChecked"] = dd.get_text(strip=True)
    return trust


def _fallback_summary_and_metrics(soup: bs) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    info_div = soup.find(
        "div", class_=lambda c: c and "rounded" in c and "border-gray-300" in c and "bg-white" in c
    )
    if info_div:
        p = info_div.find("p")
        if p:
            result["summary"] = p.get_text(strip=True)

    grid = soup.find("div", class_=lambda c: c and "grid" in c and "grid-cols-2" in c)
    if grid:
        metrics = {}
        for item in grid.find_all("div", recursive=False):
            value_div = item.find("div", class_=lambda c: c and "mt-0.5" in c)
            if value_div:
                label = item.get_text().replace(value_div.get_text(), "").strip()
                metrics[label] = value_div.get_text(strip=True)
            else:
                parts = item.get_text(separator="\n", strip=True).split("\n")
                if len(parts) >= 2:
                    metrics[parts[0]] = parts[1]
        result["metrics"] = metrics
    return result


def _fallback_history_table(soup: bs) -> List[Dict[str, str]]:
    history = []
    table = soup.find("table", class_=lambda c: c and "svelte-1ptzvif" in c)
    if not table:
        return history
    tbody = table.find("tbody")
    if not tbody:
        return history
    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) >= 4:
            history.append({
                "exDate": cols[0].get_text(strip=True),
                "cashAmount": cols[1].get_text(strip=True),
                "recordDate": cols[2].get_text(strip=True),
                "payDate": cols[3].get_text(strip=True),
            })
    return history


def _fallback_holdings_table(soup: bs) -> List[Dict[str, str]]:
    holdings = []
    table = soup.find("table", class_=lambda c: c and "svelte-1ptzvif" in c)
    if not table:
        return holdings
    tbody = table.find("tbody")
    if not tbody:
        return holdings
    for row in tbody.find_all("tr"):
        cols = row.find_all("td")
        if len(cols) >= 5:
            symbol_tag = cols[1].find("a")
            holdings.append({
                "no": cols[0].get_text(strip=True),
                "symbol": symbol_tag.get_text(strip=True) if symbol_tag else "",
                "name": cols[2].get_text(strip=True),
                "weight": cols[3].get_text(strip=True),
                "shares": cols[4].get_text(strip=True),
            })
    return holdings


def _fallback_news_list(soup: bs) -> List[Dict[str, str]]:
    news = []
    news_div = soup.find("div", class_=lambda c: c and "rounded" in c and "border-gray-200" in c and "p-4" in c)
    if not news_div:
        return news
    ul = news_div.find("ul")
    if not ul:
        return news
    for li in ul.find_all("li", recursive=False):
        a = li.find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        url = a.get("href")
        full_text = li.get_text(separator=" ", strip=True)
        parts = full_text.split(title)
        if len(parts) == 2:
            date = parts[0].strip().replace("-", "").strip()
            source = parts[1].strip().replace("-", "").strip()
        else:
            date, source = "", ""
        news.append({"title": title, "url": url, "date": date, "source": source})
    return news


# =============================================================================
# 4. HELPER KECIL
# =============================================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_date_value(value: Any) -> Any:
    if isinstance(value, (int, float)):
        if abs(value) >= 1_000_000_000_000:
            try:
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return value
        if abs(value) >= 1_000_000_000:
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return value
    if isinstance(value, str) and value.isdigit():
        if len(value) == 13:
            try:
                return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return value
        if len(value) == 10:
            try:
                return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
            except (OverflowError, OSError, ValueError):
                return value
    return value


def _normalize_date_field(key: str, value: Any) -> Any:
    if key in {
        "inception",
        "asOf",
        "tradingDate",
        "exDividendDate",
        "recordDate",
        "payDate",
        "exDate",
        "asOfDate",
        "ipoDate",
    }:
        return _format_date_value(value)
    return value


def _dedupe_news(*news_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge news lists with different source formats into one list, deduplicate
    by URL, and sort by the most recent `ago` text when available."""
    seen = {}
    for lst in news_lists:
        for item in lst or []:
            # Normalize keys: overview/holdings use t/u/n/d/time/ago, while
            # dividend fallback HTML uses title/url/date/source.
            url = item.get("url") or item.get("u")
            if not url or url in seen:
                continue
            seen[url] = {
                "title": item.get("title") or item.get("t"),
                "url": url,
                "source": item.get("source") or item.get("n"),
                "published": item.get("date") or item.get("ago") or item.get("d"),
                "summary": item.get("text"),
                "image": item.get("img"),
                "tickers": item.get("tickers"),
            }
    return list(seen.values())


# =============================================================================
# 5. PURE PER-PAGE PARSERS (accept HTML strings without network I/O)
#    Kept separate for easy unit testing without an internet connection.
# =============================================================================

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
        "nav": embedded.get("nav"),
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
    #   p=harga terkini, o=open, h=high, l=low, cl=previous close,
    #   c=perubahan (dolar), cp=perubahan (persen), u=waktu "at close",
    #   ms=status market, h52/l52=52-week high/low, v=volume, td=tanggal trading
    #   ep=harga after-hours, ec/ecp=perubahan after-hours (dolar/persen),
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
        holdings_list = [
            {
                "no": h.get("no"),
                "symbol": (h.get("s") or "").lstrip("$"),
                "name": h.get("n"),
                "weight": h.get("as"),
                "shares": h.get("sh"),
            }
            for h in embedded.get("holdings", [])
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


# =============================================================================
# 5.5 DATA ASSEMBLY (shared by Momo.get_etf_data and partial-page callers)
# =============================================================================

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

    if errors:
        data["errors"] = errors

    return data



# =============================================================================
# 5.8 STOCK DATA MODEL & PARSER
# =============================================================================

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


def _strip_html_text(value: Any) -> Any:
    if not isinstance(value, str) or "<" not in value:
        return value
    return bs(value, "html.parser").get_text("\n", strip=True)


def _list_table_to_dict(items: Any, key_field: str = "t", value_field: str = "v") -> Dict[str, Any]:
    if not isinstance(items, list):
        return {}
    out: Dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get(key_field)
        if key:
            out[str(key)] = item.get(value_field)
    return out


def _columnar_to_rows(data: Any) -> List[Dict[str, Any]]:
    """Convert a dict of per-field arrays into a list of rows by period.

    Banyak endpoint financial StockAnalysis menyimpan data sebagai:
      {"datekey": [..], "revenue": [..], "eps": [..]}
    Rows are more convenient for downstream analytics and serialization.
    Scalar fields are omitted from rows but remain available in parser `rawData`.
    """
    if not isinstance(data, dict):
        return []
    list_fields = {k: v for k, v in data.items() if isinstance(v, list)}
    if not list_fields:
        return []
    size = max((len(v) for v in list_fields.values()), default=0)
    rows: List[Dict[str, Any]] = []
    for i in range(size):
        row: Dict[str, Any] = {}
        for key, values in list_fields.items():
            row[key] = values[i] if i < len(values) else None
        rows.append(row)
    return rows


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


def _normalize_statistics_section(section: Any) -> Dict[str, Any]:
    if not isinstance(section, dict):
        return {"text": None, "items": [], "values": {}}
    items = section.get("data") if isinstance(section.get("data"), list) else []
    values = {
        item.get("id"): item.get("value")
        for item in items
        if isinstance(item, dict) and item.get("id")
    }
    return {"text": section.get("text"), "items": items, "values": values}


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


def _normalize_financial_section(section: Dict[str, Any]) -> Dict[str, Any]:
    data = section.get("data") or {}
    return {
        "id": section.get("id"),
        "title": section.get("title"),
        "indicators": section.get("indicators"),
        "defaultVisible": section.get("defaultVisible"),
        "defaultExpanded": section.get("defaultExpanded"),
        "rowDefinitions": section.get("rows") or [],
        "rows": _columnar_to_rows(data),
        "rawData": data,
        "ttm": section.get("ttm"),
        "ttmPrior": section.get("ttmPrior"),
        "prior": section.get("prior"),
        "priorFiscalYear": section.get("priorFiscalYear"),
        "priorFiscalQuarter": section.get("priorFiscalQuarter"),
    }


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


def parse_stock_business_metrics_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded business metrics data was not found.")
    data = embedded.get("data") or {}
    return {
        "identity": _stock_identity(embedded),
        "title": data.get("title"),
        "description": data.get("description"),
        "groups": data.get("groups") or [],
        "annualDates": data.get("annualDates") or [],
        "quarterlyDates": data.get("quarterlyDates") or [],
        "trailingDates": data.get("trailingDates") or [],
        "annualMetrics": data.get("annualMetrics") or [],
        "quarterlyMetrics": data.get("quarterlyMetrics") or [],
        "trailingMetrics": data.get("trailingMetrics") or [],
        "singlePages": data.get("singlePages") or [],
        "navigationItems": data.get("navigationItems") or [],
        "subNavigationItems": data.get("subNavigationItems") or [],
        "sourceLastUpdated": data.get("sourceLastUpdated"),
        "trust": data.get("trust") or embedded.get("trust"),
        "_source": "embedded_json",
    }


def parse_stock_metric_detail_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded metric detail data was not found.")
    data = embedded.get("data") or {}
    metric_data = data.get("data") or {}
    return {
        "identity": _stock_identity(embedded),
        "metric": embedded.get("metric"),
        "pageType": data.get("pageType"),
        "title": data.get("title"),
        "type": data.get("type"),
        "valueType": data.get("valueType"),
        "quarterly": metric_data.get("quarterly") or [],
        "annual": metric_data.get("annual") or [],
        "trailing": metric_data.get("trailing") or [],
        "rawData": metric_data,
        "navigationItems": data.get("navigationItems") or [],
        "subNavigationItems": data.get("subNavigationItems") or [],
        "trust": data.get("trust") or embedded.get("trust"),
        "_source": "embedded_json",
    }


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


def parse_stock_snapshot(
    pages: Mapping[str, str],
    ticker: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse a collection of downloaded stock HTML pages without network access.

    `pages` may use MOMO section keys or arbitrary keys/file names; unknown
    keys are detected from the canonical URL.
    """
    parsed: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    inferred_ticker = ticker.upper() if ticker else None

    for key, html in pages.items():
        section = key if key in STOCK_SECTION_ROUTES else detect_stock_section_from_html(html)
        if not section:
            errors.append({"section": str(key), "error": "Could not detect a section from the HTML."})
            continue
        if inferred_ticker is None:
            inferred_ticker = _ticker_from_stock_html(html)
        try:
            parsed[section] = parse_stock_section_html(section, html)
        except MomoError as exc:
            errors.append({"section": section, "error": str(exc)})
        except Exception as exc:
            errors.append({"section": section, "error": f"Unexpected error: {exc}"})

    if not inferred_ticker:
        raise MomoValidationError("Ticker could not be inferred from the HTML snapshot; provide it explicitly.")
    return assemble_stock_data(inferred_ticker, parsed, errors or None)


def parse_stock_directory(directory: Path, ticker: Optional[str] = None) -> Dict[str, Any]:
    """Parse all .html files in a folder produced by the `main.py` downloader."""
    directory = Path(directory)
    if not directory.exists() or not directory.is_dir():
        raise MomoValidationError(f"HTML directory not found: {directory}")
    pages: Dict[str, str] = {}
    for path in sorted(directory.glob("*.html")):
        pages[path.name] = path.read_text(encoding="utf-8", errors="replace")
    if not pages:
        raise MomoValidationError(f"No .html files found in {directory}")
    return parse_stock_snapshot(pages, ticker=ticker)


# =============================================================================
# 6. MAIN MOMO API CLASS
# =============================================================================

class Momo:
    """API for fetching comprehensive ETF data from
    stockanalysis.com.

        with Momo() as momo:
            overview  = momo.get_overview("VYM")
            holdings  = momo.get_holdings("VYM")
            dividends = momo.get_dividends("VYM")
            news      = momo.get_news("VYM")
            full      = momo.get_etf_data("VYM")           # combine everything
            table     = momo.compare(["VYM", "SCHD"])      # compare ETFs

    Customize through `MomoConfig`, or directly with keyword shorthand:

        momo = Momo(config=MomoConfig(timeout=30, max_retries=5))
        momo = Momo(timeout=30, max_retries=5)   # setara, lebih singkat
    """

    def __init__(
        self,
        save_debug_html: bool = False,
        config: Optional[MomoConfig] = None,
        **config_overrides: Any,
    ):
        if config is None:
            config = MomoConfig()
        if config_overrides:
            config = replace(config, **config_overrides)
        self.config = config
        self.save_debug_html = save_debug_html
        self._session = req.Session()
        self._session.headers.update(self.config.headers)
        self._cache: Dict[str, Any] = {}

    # -- context manager (agar koneksi HTTP ditutup rapi) ------------------------

    def __enter__(self) -> "Momo":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Tutup session HTTP (connection pool)."""
        self._session.close()

    def __repr__(self) -> str:
        return (
            f"Momo(etf_base_url={self.config.base_url!r}, "
            f"stock_base_url={self.config.stock_base_url!r}, "
            f"use_cache={self.config.use_cache}, max_retries={self.config.max_retries})"
        )

    # -- cache in-memory sederhana (TTL) ------------------------------------------

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.config.use_cache:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if time.monotonic() - ts > self.config.cache_ttl:
            del self._cache[key]
            return None
        logger.debug("Cache hit: %s", key)
        return data

    def _cache_set(self, key: str, data: Dict[str, Any]) -> None:
        if self.config.use_cache:
            self._cache[key] = (time.monotonic(), data)

    def clear_cache(self) -> None:
        """Clear this instance's entire in-memory cache."""
        self._cache.clear()

    # -- fetch + parse each page ---------------------------------------------------

    def _fetch_and_maybe_save(self, url: str, ticker: str, page: str) -> str:
        html = _fetch_html(self._session, url, self.config)
        if self.save_debug_html:
            debug_dir = self.config.debug_dir or Path.cwd()
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"{ticker.lower()}_{page}_debug.html"
            debug_path.write_text(html, encoding="utf-8")
            logger.debug("HTML debug saved to: %s", debug_path)
        return html

    def get_overview(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:overview"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/{ticker.lower()}/"
        html = self._fetch_and_maybe_save(url, ticker, "overview")
        data = parse_overview_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_holdings(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:holdings"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/{ticker.lower()}/holdings/"
        html = self._fetch_and_maybe_save(url, ticker, "holdings")
        data = parse_holdings_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_dividends(self, ticker: str) -> Dict[str, Any]:
        ticker = _validate_ticker(ticker)
        cache_key = f"{ticker}:dividends"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.base_url}/{ticker.lower()}/dividend/"
        html = self._fetch_and_maybe_save(url, ticker, "dividend")
        data = parse_dividend_html(html)
        self._cache_set(cache_key, data)
        return data

    def get_news(
        self,
        ticker: str,
        overview: Optional[Dict[str, Any]] = None,
        holdings: Optional[Dict[str, Any]] = None,
        dividends: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Merge and deduplicate news from overview/holdings/dividend. If any
        source has not been fetched, its argument may be None and this function
        fetches it itself."""
        ticker = _validate_ticker(ticker)
        overview = overview if overview is not None else self.get_overview(ticker)
        holdings = holdings if holdings is not None else self.get_holdings(ticker)
        dividends = dividends if dividends is not None else self.get_dividends(ticker)
        return _dedupe_news(
            overview.get("news") or [],
            holdings.get("news") or [],
            dividends.get("news") or [],
        )

    # -- main function: comprehensive data for one ticker -------------------------

    def get_etf_data(
        self,
        ticker: str,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Hasilkan JSON komprehensif tentang sebuah ETF: overview + performa,
        holdings, dividend, dan news gabungan.

        All three pages are fetched in parallel to speed up the process. Partial
        data and an error list are returned when a page fails rather than failing
        the entire operation.

        If dump_json=True, data is written to a JSON file through `save_json`.
        """
        ticker = _validate_ticker(ticker)
        errors: List[Dict[str, str]] = []
        results: Dict[str, Dict[str, Any]] = {"overview": {}, "holdings": {}, "dividends": {}}
        fetchers = {
            "overview": self.get_overview,
            "holdings": self.get_holdings,
            "dividends": self.get_dividends,
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_map = {executor.submit(fn, ticker): name for name, fn in fetchers.items()}
            for future in as_completed(future_map):
                name = future_map[future]
                section = "dividend" if name == "dividends" else name
                try:
                    results[name] = future.result()
                except MomoError as e:
                    logger.warning("Failed to fetch %s for %s: %s", section, ticker, e)
                    errors.append({"section": section, "error": str(e)})
                except Exception as e:  # Unexpected parsing bugs must not fail the entire batch.
                    logger.exception("Unexpected error while fetching %s for %s", section, ticker)
                    errors.append({"section": section, "error": f"Unexpected error: {e}"})

        data = assemble_etf_data(
            ticker,
            overview=results["overview"],
            holdings=results["holdings"],
            dividends=results["dividends"],
            errors=errors or None,
        )

        if dump_json:
            self.save_json(data, dump_path)

        return data

    def get_data(
        self,
        ticker: str,
        asset_type: str = "etf",
        sections: Optional[Sequence[str]] = None,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Unified entry point for ETFs and stocks.

        `asset_type` must be `"etf"` or `"stock"`. For stocks, `sections`
        can limit the endpoints that are fetched.
        """
        kind = asset_type.strip().lower()
        if kind == "etf":
            if sections is not None:
                raise MomoValidationError("The sections parameter only applies to asset_type='stock'.")
            return self.get_etf_data(ticker, dump_json=dump_json, dump_path=dump_path)
        if kind == "stock":
            return self.get_stock_data(
                ticker, sections=sections, dump_json=dump_json, dump_path=dump_path
            )
        raise MomoValidationError("asset_type must be 'etf' or 'stock'.")

    # -- STOCK API -------------------------------------------------------------------

    def get_stock_section(self, ticker: str, section: str) -> Dict[str, Any]:
        """Fetch and parse one stock section using `STOCK_SECTION_ROUTES`."""
        ticker = _validate_ticker(ticker)
        section = section.strip().lower()
        route = STOCK_SECTION_ROUTES.get(section)
        if route is None:
            raise MomoValidationError(
                f"Unknown stock section: {section!r}. Choices: {', '.join(STOCK_SECTION_ROUTES)}"
            )
        cache_key = f"stock:{ticker}:{section}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = f"{self.config.stock_base_url.rstrip('/')}/{ticker.lower()}/" + route
        html = self._fetch_and_maybe_save(url, ticker, f"stock_{section}")
        data = parse_stock_section_html(section, html)
        self._cache_set(cache_key, data)
        return data

    def get_stock_overview(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "overview")

    def get_stock_company(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "company")

    def get_stock_financials(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "financials")

    def get_stock_statistics(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "statistics")

    def get_stock_forecast(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "forecast")

    def get_stock_ratings(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "ratings")

    def get_stock_market_cap(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "market_cap")

    def get_stock_revenue(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "revenue")

    def get_stock_dividends(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "dividends")

    def get_stock_business_metrics(self, ticker: str) -> Dict[str, Any]:
        return self.get_stock_section(ticker, "business_metrics")

    def get_stock_data(
        self,
        ticker: str,
        sections: Optional[Sequence[str]] = None,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Fetch comprehensive stock data in parallel.

        By default, fetches every endpoint in `STOCK_SECTION_ROUTES`.
        Use `sections=[...]` for a lightweight mode.
        """
        ticker = _validate_ticker(ticker)
        requested = list(STOCK_SECTION_ROUTES.keys()) if sections is None else list(dict.fromkeys(sections))
        if not requested:
            return assemble_stock_data(ticker, {})
        unknown = [section for section in requested if section not in STOCK_SECTION_ROUTES]
        if unknown:
            raise MomoValidationError(
                f"Unknown stock section: {', '.join(unknown)}. "
                f"Choices: {', '.join(STOCK_SECTION_ROUTES)}"
            )

        results: Dict[str, Dict[str, Any]] = {}
        errors: List[Dict[str, str]] = []
        worker_count = max(1, min(len(requested), self.config.max_workers))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(self.get_stock_section, ticker, section): section
                for section in requested
            }
            for future in as_completed(future_map):
                section = future_map[future]
                try:
                    results[section] = future.result()
                except MomoError as exc:
                    logger.warning("Failed to fetch stock section %s for %s: %s", section, ticker, exc)
                    errors.append({"section": section, "error": str(exc)})
                except Exception as exc:
                    logger.exception("Unexpected error in stock section %s for %s", section, ticker)
                    errors.append({"section": section, "error": f"Unexpected error: {exc}"})

        data = assemble_stock_data(ticker, results, errors or None)
        data["_meta"]["sectionsRequested"] = requested
        if dump_json:
            self.save_json(data, dump_path)
        return data

    def analyze_stock_data(
        self,
        stock_data: Mapping[str, Any],
        price_data: Any = None,
        period: str = "5y",
        interval: str = "1d",
        auto_fetch_price: bool = True,
        timeframes: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """Run the professional analysis engine on a MOMO stock payload.

        Import ``technical`` is lazy so MOMO parsers/fetchers remain usable
        without analytical dependencies (numpy/pandas/yfinance).
        ``price_data`` may be an OHLCV DataFrame; when omitted with
        ``auto_fetch_price=True``, the engine attempts to fetch historical prices.
        """
        if not isinstance(stock_data, Mapping):
            raise MomoValidationError("stock_data must be a mapping/dict returned by get_stock_data().")
        ticker = _validate_ticker(str(stock_data.get("ticker") or ""))
        try:
            from technical import ProfessionalStockAnalyzer
        except ImportError as exc:
            raise MomoError(
                "The stock analysis engine is unavailable. Ensure technical.py is "
                "in the project directory and numpy/pandas are installed."
            ) from exc

        analyzer = ProfessionalStockAnalyzer(
            ticker=ticker,
            price_data=price_data,
            fundamental_data=stock_data,
            period=period,
            interval=interval,
            auto_fetch=auto_fetch_price,
        )
        result = analyzer.analyze(timeframes=timeframes)
        meta = stock_data.get("_meta") or {}
        result["dataContext"] = {
            "source": meta.get("source") or "stockanalysis.com",
            "generatedAt": stock_data.get("generatedAt"),
            "sectionsAvailable": meta.get("sectionsAvailable") or [],
            "sectionsRequested": meta.get("sectionsRequested") or [],
            "sourceErrors": stock_data.get("errors") or [],
        }
        return result

    def analyze_stock(
        self,
        ticker: str,
        sections: Optional[Sequence[str]] = None,
        price_data: Any = None,
        period: str = "5y",
        interval: str = "1d",
        auto_fetch_price: bool = True,
        timeframes: Optional[Sequence[Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch fundamental stock data lalu jalankan technical + valuation engine."""
        stock_data = self.get_stock_data(ticker, sections=sections)
        return self.analyze_stock_data(
            stock_data,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )

    def compare_stocks(
        self,
        tickers: Sequence[str],
        sections: Optional[Sequence[str]] = None,
        dump_json: bool = False,
        dump_folder: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch multiple stocks in parallel using the `get_stock_data` schema."""
        if not tickers:
            return []
        normalized = [_validate_ticker(t) for t in tickers]
        results: Dict[str, Dict[str, Any]] = {}
        outer_workers = max(1, min(len(normalized), 4))
        with ThreadPoolExecutor(max_workers=outer_workers) as executor:
            future_map = {}
            for ticker in normalized:
                dump_path = (dump_folder / f"momo_{ticker.lower()}_stock.json") if dump_folder else None
                future = executor.submit(
                    self.get_stock_data, ticker, sections, dump_json, dump_path
                )
                future_map[future] = ticker
            for future in as_completed(future_map):
                ticker = future_map[future]
                try:
                    results[ticker] = future.result()
                except Exception as exc:
                    results[ticker] = {
                        "ticker": ticker,
                        "assetType": "stock",
                        "errors": [{"section": "all", "error": str(exc)}],
                    }
        return [results[ticker] for ticker in normalized]

    # -- compare multiple tickers ---------------------------------------------------

    def compare(
        self,
        tickers: List[str],
        dump_json: bool = False,
        dump_folder: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch comprehensive data for multiple tickers in parallel. Tickers
        that fail completely remain in the result with an error list, and the
        result order follows the supplied `tickers` order.
        """
        if not tickers:
            return []
        results: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(len(tickers), 4)) as executor:
            future_map = {}
            for t in tickers:
                dump_path = (dump_folder / f"momo_{t.strip().lower()}_data.json") if dump_folder else None
                future_map[executor.submit(self.get_etf_data, t, dump_json, dump_path)] = t
            for future in as_completed(future_map):
                t = future_map[future]
                try:
                    results[t] = future.result()
                except Exception as e:
                    logger.error("Failed to fetch all data for %s: %s", t, e)
                    results[t] = {
                        "ticker": t.strip().upper(),
                        "errors": [{"section": "all", "error": str(e)}],
                    }
        return [results[t] for t in tickers if t in results]

    # -- ekspor JSON -----------------------------------------------------------------

    def save_json(self, data: Dict[str, Any], dump_path: Optional[Path] = None) -> Path:
        """Save ETF/stock data to a JSON file and return its path."""
        output_path = Path(dump_path) if dump_path else Path(f"momo_{data.get('ticker', 'unknown').lower()}_data.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Data saved to %s", output_path)
        return output_path

    # Legacy alias retained so older code calling this private name continues
    # to work without changes.
    _dump_json_data = save_json


# =============================================================================
# 7. CONVENIENCE FUNCTIONS (module-level) -- for one-off use or quick scripts
#    without explicitly creating a Momo instance.
# =============================================================================

def get_overview(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_overview(ticker)


def get_holdings(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_holdings(ticker)


def get_dividends(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_dividends(ticker)


def get_news(ticker: str, **config_overrides: Any) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.get_news(ticker)


def get_etf_data(
    ticker: str,
    dump_json: bool = False,
    dump_path: Optional[Path] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_etf_data(ticker, dump_json=dump_json, dump_path=dump_path)


def get_data(
    ticker: str,
    asset_type: str = "etf",
    sections: Optional[Sequence[str]] = None,
    dump_json: bool = False,
    dump_path: Optional[Path] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_data(
            ticker, asset_type=asset_type, sections=sections,
            dump_json=dump_json, dump_path=dump_path
        )


def get_stock_section(ticker: str, section: str, **config_overrides: Any) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_stock_section(ticker, section)


def get_stock_data(
    ticker: str,
    sections: Optional[Sequence[str]] = None,
    dump_json: bool = False,
    dump_path: Optional[Path] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    with Momo(**config_overrides) as momo:
        return momo.get_stock_data(ticker, sections=sections, dump_json=dump_json, dump_path=dump_path)


def get_stock_overview(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "overview", **config_overrides)


def get_stock_company(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "company", **config_overrides)


def get_stock_financials(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "financials", **config_overrides)


def get_stock_statistics(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "statistics", **config_overrides)


def get_stock_forecast(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "forecast", **config_overrides)


def get_stock_ratings(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "ratings", **config_overrides)


def get_stock_market_cap(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "market_cap", **config_overrides)


def get_stock_revenue(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "revenue", **config_overrides)


def get_stock_dividends(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "dividends", **config_overrides)


def get_stock_business_metrics(ticker: str, **config_overrides: Any) -> Dict[str, Any]:
    return get_stock_section(ticker, "business_metrics", **config_overrides)


def analyze_stock_data(
    stock_data: Mapping[str, Any],
    price_data: Any = None,
    period: str = "5y",
    interval: str = "1d",
    auto_fetch_price: bool = True,
    timeframes: Optional[Sequence[Any]] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    """Analyze an existing stock payload without refetching fundamentals."""
    with Momo(**config_overrides) as momo:
        return momo.analyze_stock_data(
            stock_data,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )


def analyze_stock(
    ticker: str,
    sections: Optional[Sequence[str]] = None,
    price_data: Any = None,
    period: str = "5y",
    interval: str = "1d",
    auto_fetch_price: bool = True,
    timeframes: Optional[Sequence[Any]] = None,
    **config_overrides: Any,
) -> Dict[str, Any]:
    """Fetch data saham lalu hasilkan laporan analisis multi-timeframe lengkap."""
    with Momo(**config_overrides) as momo:
        return momo.analyze_stock(
            ticker,
            sections=sections,
            price_data=price_data,
            period=period,
            interval=interval,
            auto_fetch_price=auto_fetch_price,
            timeframes=timeframes,
        )


def compare_stocks(
    tickers: Sequence[str],
    sections: Optional[Sequence[str]] = None,
    **config_overrides: Any,
) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.compare_stocks(tickers, sections=sections)


def compare(tickers: List[str], **config_overrides: Any) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.compare(tickers)