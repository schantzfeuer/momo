from __future__ import annotations
from typing import Any, Dict
from urllib.parse import urlsplit
from bs4 import BeautifulSoup as bs
from ...core.exceptions import MomoParseError
from ...extractors.sveltekit import _extract_embedded_data
from .overview import _stock_identity

def _remove_pro_points(value: Any) -> Any:
    if isinstance(value, list):
        return [
            _remove_pro_points(item)
            for item in value
            if not (
                isinstance(item, dict)
                and isinstance(item.get("y"), str)
                and item["y"].strip() == "[PRO]"
            )
        ]
    if isinstance(value, dict):
        return {key: _remove_pro_points(item) for key, item in value.items()}
    return value

def _extract_metric_pages(html: str, ticker: str) -> list[Dict[str, str]]:
    soup = bs(html, "html.parser")
    prefix = f"/stocks/{ticker.lower()}/metrics/"
    pages = []
    seen_routes = set()
    for menu in soup.select("ul.navmenu.submenu.thin-scrollbar"):
        for link in menu.select("a[href]"):
            path = urlsplit(str(link["href"])).path
            if not path.startswith(prefix):
                continue
            route = path[len(prefix):].strip("/")
            if not route:
                continue
            relative_route = f"metrics/{route}/"
            if relative_route in seen_routes:
                continue
            seen_routes.add(relative_route)
            pages.append({
                "title": link.get_text(" ", strip=True),
                "route": relative_route,
            })
    return pages

def parse_stock_business_metrics_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded business metrics data was not found.")
    data = _remove_pro_points(embedded.get("data") or {})
    identity = _stock_identity(embedded)
    ticker = identity.get("ticker")
    return {
        "identity": identity,
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
        "metricPages": _extract_metric_pages(html, str(ticker)) if ticker else [],
        "sourceLastUpdated": data.get("sourceLastUpdated"),
        "trust": data.get("trust") or embedded.get("trust"),
        "_source": "embedded_json",
    }

def parse_stock_metric_detail_html(html: str) -> Dict[str, Any]:
    embedded = _extract_embedded_data(html)
    if not embedded:
        raise MomoParseError("Embedded metric detail data was not found.")
    data = _remove_pro_points(embedded.get("data") or {})
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
