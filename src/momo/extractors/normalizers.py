from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List
from bs4 import BeautifulSoup as bs

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
