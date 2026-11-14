from __future__ import annotations
import re
from typing import Any, Dict, List
from bs4 import BeautifulSoup as bs

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
    for row in tbody.find_all("tr")[:25]:
        cols = row.find_all("td")
        if len(cols) >= 5:
            symbol_tag = cols[1].find("a")
            holdings.append({
                "no": cols[0].get_text(strip=True),
                "symbol": symbol_tag.get_text(strip=True) if symbol_tag else "",
                "name": cols[2].get_text(strip=True),
                "company": cols[2].get_text(strip=True),
                "weight": cols[3].get_text(strip=True),
                "shares": cols[4].get_text(strip=True),
                "price": cols[5].get_text(strip=True) if len(cols) > 5 else "",
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
