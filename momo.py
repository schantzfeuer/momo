"""
=============================================================================
 MOMO - YAMAMOMO ETF Data API
=============================================================================

Mengambil data komprehensif sebuah ETF (overview, holdings, dividend, news)
dari stockanalysis.com.

Pemakaian cepat (fungsi module-level, tanpa perlu bikin instance):

    import momo
    data = momo.get_etf_data("VYM")

Pemakaian dengan kontrol lebih (reuse koneksi, cache, config kustom):

    from momo import Momo, MomoConfig

    with Momo(config=MomoConfig(timeout=30, max_retries=5)) as m:
        overview  = m.get_overview("VYM")
        holdings  = m.get_holdings("VYM")
        dividends = m.get_dividends("VYM")
        news      = m.get_news("VYM")
        full      = m.get_etf_data("VYM")          # gabungan semuanya
        cmp_table = m.compare(["VYM", "SCHD"])     # bandingkan beberapa ETF

Debugging: `momo.enable_logging()` menyalakan log ke konsol -- akan
menunjukkan halaman mana yang gagal, kapan fallback HTML dipakai, dll.
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
from typing import Any, Dict, List, Optional

import requests as req
from bs4 import BeautifulSoup as bs

__version__ = "2.0.2"

__all__ = [
    "Momo",
    "MomoConfig",
    "MomoError",
    "MomoNetworkError",
    "MomoParseError",
    "MomoValidationError",
    "enable_logging",
    "assemble_etf_data",
    "parse_overview_html",
    "parse_holdings_html",
    "parse_dividend_html",
    "get_overview",
    "get_holdings",
    "get_dividends",
    "get_news",
    "get_etf_data",
    "compare",
]

logger = logging.getLogger("momo")
logger.addHandler(logging.NullHandler())


def enable_logging(level: int = logging.INFO) -> None:
    """Aktifkan output logging modul momo ke konsol (stderr).

    Berguna saat data yang didapat tidak sesuai ekspektasi: log akan
    menunjukkan halaman mana yang gagal diambil, kapan fallback HTML
    dipakai (indikasi struktur halaman situs berubah), dsb.

        import momo
        momo.enable_logging()
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)


# =============================================================================
# 0. EXCEPTIONS & KONFIGURASI
# =============================================================================

class MomoError(Exception):
    """Kelas dasar untuk semua error yang dilempar oleh momo."""


class MomoNetworkError(MomoError):
    """Gagal mengambil halaman dari jaringan (timeout, HTTP error, dsb)
    setelah seluruh percobaan ulang (retry) habis."""


class MomoParseError(MomoError):
    """Gagal mem-parsing HTML/JSON pada sebuah halaman."""


class MomoValidationError(MomoError):
    """Input tidak valid, misalnya ticker kosong atau berformat aneh."""


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class MomoConfig:
    """Semua pengaturan yang bisa dikustomisasi dikumpulkan di sini --
    daripada tersebar sebagai konstanta modul -- sehingga tiap instance
    `Momo` bisa punya konfigurasi berbeda (mis. saat testing, atau saat
    menjalankan beberapa profil/kredensial jaringan sekaligus)."""

    base_url: str = "https://stockanalysis.com/etf"
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = "en-US,en;q=0.9"
    timeout: float = 15.0
    max_retries: int = 3
    retry_backoff_base: float = 1.0
    use_cache: bool = True
    cache_ttl: float = 300.0  # detik
    debug_dir: Optional[Path] = None

    @property
    def headers(self) -> Dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept-Language": self.accept_language}


_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z]{1,3})?$")


def _validate_ticker(ticker: str) -> str:
    """Normalisasi & validasi ticker. Melempar MomoValidationError jika
    kosong/formatnya tidak masuk akal -- daripada membiarkan permintaan
    HTTP yang jelas-jelas akan gagal terkirim ke server."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise MomoValidationError("Ticker tidak boleh kosong.")
    cleaned = ticker.strip().upper()
    if not _TICKER_RE.match(cleaned):
        raise MomoValidationError(f"Format ticker tampak tidak valid: {ticker!r}")
    return cleaned


# =============================================================================
# 1. LAPISAN JARINGAN (network layer)
# =============================================================================

def _fetch_html(session: req.Session, url: str, config: MomoConfig) -> str:
    """Ambil HTML mentah dari sebuah URL, dengan percobaan ulang otomatis
    (exponential backoff) untuk error jaringan sementara. Melempar
    MomoNetworkError jika seluruh percobaan gagal."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, config.max_retries + 1):
        try:
            response = session.get(url, timeout=config.timeout)
            response.raise_for_status()
            return response.text
        except req.RequestException as e:
            last_exc = e
            logger.debug(
                "Percobaan %d/%d gagal untuk %s (%s)", attempt, config.max_retries, url, e
            )
            if attempt < config.max_retries:
                time.sleep(config.retry_backoff_base * (2 ** (attempt - 1)))
    raise MomoNetworkError(
        f"Gagal mengambil {url} setelah {config.max_retries} percobaan: {last_exc}"
    ) from last_exc


# =============================================================================
# 2. PARSER DATA JSON YANG DISISIPKAN (embedded SvelteKit data) — jalur utama
# =============================================================================
#
# stockanalysis.com menyisipkan data halaman sebagai literal objek JavaScript
# (bukan JSON murni) di dalam <script>, contoh:
#
#   kit.start(app, element, { node_ids: [...], data: [
#       {type:"data", data:{session:null, ...}},
#       {type:"data", data:{info:{...}}},
#       {type:"data", data:{aum:"$81.02B", trust:{...}, performance:{...}, ...}}
#   ]})
#
# Objek ini memakai key tanpa kutip, angka berawalan titik (.02), literal
# `void 0`, dsb — sehingga tidak bisa langsung di-json.loads. Fungsi di bawah
# ini mengekstrak larik `data: [...]` tersebut lalu "menjinakkannya" menjadi
# JSON valid.

def _extract_data_array_text(script_text: str) -> Optional[str]:
    """Cari teks larik `data: [ ... ]` di dalam script SvelteKit, dengan
    penghitungan kurung yang sadar akan string literal (agar '[' / ']' di
    dalam string tidak dihitung)."""
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
    return None  # kurung tak seimbang


_IDENTIFIER_KEY_RE = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')


def _js_object_literal_to_json(js_text: str) -> str:
    """Ubah literal objek JavaScript (longgar) menjadi teks JSON valid.

    Penanganan:
      - `void 0` / `undefined` / `NaN`      -> null
      - key tanpa kutip (mis. `n:"IBM"`)    -> diberi kutip ("n":"IBM")
      - angka berawalan titik (`.02`)       -> diberi 0 di depan (0.02)
      - koma menggantung sebelum `}`/`]`    -> dihapus
    String literal dilewati apa adanya (karakter di dalamnya tak diubah).
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
            out.append('"' if ch == "'" else ch)  # normalkan single -> double quote
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
    # angka berawalan titik setelah ':' '[' atau ',' -> beri leading zero
    result = re.sub(r"([:\[,]\s*)(-?)\.(\d)", r"\1\g<2>0.\3", result)
    # hapus koma menggantung sebelum penutup objek/larik
    result = re.sub(r",(\s*[}\]])", r"\1", result)
    return result


def _extract_embedded_data(html: str) -> Dict[str, Any]:
    """Cari semua <script> yang mengandung `__sveltekit_`, ekstrak larik
    `data:[...]`, jinakkan jadi JSON, lalu satukan (merge) field `.data`
    dari tiap elemen larik menjadi satu dict rata (flat).

    Mengembalikan dict kosong {} jika gagal (pemanggil harus siap fallback
    ke parsing HTML/CSS).
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
        break  # sudah ketemu script yang relevan

    if not merged:
        logger.debug("Data JSON tersemat tidak ditemukan di halaman; akan memakai fallback HTML/CSS.")

    return merged


# =============================================================================
# 3. FALLBACK: PARSER HTML/CSS (diadaptasi dari skrip asli user)
#    Dipakai hanya jika _extract_embedded_data gagal / datanya tak lengkap.
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
    }:
        return _format_date_value(value)
    return value


def _dedupe_news(*news_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gabungkan beberapa daftar berita (format kunci berbeda-beda tergantung
    halaman asalnya) menjadi satu daftar, dedup berdasarkan URL, urut dari
    yang terbaru berdasar teks 'ago' bila ada."""
    seen = {}
    for lst in news_lists:
        for item in lst or []:
            # normalisasi kunci: halaman overview/holdings pakai t/u/n/d/time/ago,
            # halaman dividend (fallback HTML) pakai title/url/date/source
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
# 5. PARSER MURNI PER HALAMAN (menerima HTML string, tanpa I/O jaringan)
#    Dipisah agar mudah diuji unit (unit-test) tanpa perlu koneksi internet.
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

    # Catatan pemetaan field (diverifikasi dengan mencocokkan ke teks yang
    # benar-benar tampil di halaman, bukan tebakan nama field semata):
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
        "raw": quote,  # field asli lengkap, untuk jaga-jaga bila butuh detail lain
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

    # Fallback bila data JSON tak ditemukan sama sekali di halaman
    if not embedded:
        logger.info("parse_overview_html: memakai fallback HTML (struktur halaman mungkin berubah).")
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
        logger.info("parse_holdings_html: memakai fallback HTML (struktur halaman mungkin berubah).")
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
            "trust": embedded.get("trust"),
            "_source": "embedded_json",
        }
    else:
        logger.info("parse_dividend_html: memakai fallback HTML (struktur halaman mungkin berubah).")
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
# 5.5 PENGGABUNG DATA (dipakai bersama oleh Momo.get_etf_data & pemanggil yang
#      hanya butuh sebagian halaman, mis. CLI dengan mode "sections")
# =============================================================================

def assemble_etf_data(
    ticker: str,
    overview: Optional[Dict[str, Any]] = None,
    holdings: Optional[Dict[str, Any]] = None,
    dividends: Optional[Dict[str, Any]] = None,
    news: Optional[List[Dict[str, Any]]] = None,
    errors: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Gabungkan hasil overview/holdings/dividends (yang sudah diambil
    sebelumnya, lewat cara apa pun) menjadi satu struktur data ETF yang
    konsisten -- persis bentuk yang dihasilkan `Momo.get_etf_data`.

    Berguna bila pemanggil hanya butuh sebagian halaman (mis. hanya dividend
    + news) namun tetap ingin bentuk data seragam untuk lapisan presentasi
    (CLI, laporan, dsb). Bila `news` tidak diberikan, akan dihitung ulang
    dari news masing-masing halaman yang tersedia.
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

    data: Dict[str, Any] = {"ticker": ticker, "generatedAt": _now_iso()}

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
# 6. KELAS API UTAMA - MOMO
# =============================================================================

class Momo:
    """API untuk mengambil data komprehensif sebuah ETF dari
    stockanalysis.com.

        with Momo() as momo:
            overview  = momo.get_overview("VYM")
            holdings  = momo.get_holdings("VYM")
            dividends = momo.get_dividends("VYM")
            news      = momo.get_news("VYM")
            full      = momo.get_etf_data("VYM")           # gabungan semuanya
            table     = momo.compare(["VYM", "SCHD"])      # bandingkan ETF

    Kustomisasi lewat `MomoConfig`, atau langsung lewat keyword shorthand:

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
            f"Momo(base_url={self.config.base_url!r}, "
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
        """Kosongkan seluruh cache in-memory instance ini."""
        self._cache.clear()

    # -- fetch + parse per halaman -------------------------------------------------

    def _fetch_and_maybe_save(self, url: str, ticker: str, page: str) -> str:
        html = _fetch_html(self._session, url, self.config)
        if self.save_debug_html:
            debug_dir = self.config.debug_dir or Path.cwd()
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_path = debug_dir / f"{ticker.lower()}_{page}_debug.html"
            debug_path.write_text(html, encoding="utf-8")
            logger.debug("HTML debug disimpan: %s", debug_path)
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
        """Gabungkan & dedup berita dari overview/holdings/dividend. Jika
        salah satu belum di-fetch, argumen boleh dikosongkan (None) dan
        fungsi ini akan mengambilnya sendiri."""
        ticker = _validate_ticker(ticker)
        overview = overview if overview is not None else self.get_overview(ticker)
        holdings = holdings if holdings is not None else self.get_holdings(ticker)
        dividends = dividends if dividends is not None else self.get_dividends(ticker)
        return _dedupe_news(
            overview.get("news") or [],
            holdings.get("news") or [],
            dividends.get("news") or [],
        )

    # -- fungsi utama: data komprehensif (satu ticker) ----------------------------

    def get_etf_data(
        self,
        ticker: str,
        dump_json: bool = False,
        dump_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Hasilkan JSON komprehensif tentang sebuah ETF: overview + performa,
        holdings, dividend, dan news gabungan.

        Ketiga halaman diambil secara PARALEL (bukan berurutan seperti versi
        sebelumnya) untuk mempercepat proses. Tetap mengembalikan data
        parsial + daftar error bila salah satu halaman gagal, alih-alih
        gagal total.

        Jika dump_json=True, data ditulis ke file JSON lewat `save_json`.
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
                    logger.warning("Gagal mengambil %s untuk %s: %s", section, ticker, e)
                    errors.append({"section": section, "error": str(e)})
                except Exception as e:  # pengaman: bug parsing tak terduga tidak boleh menggagalkan semuanya
                    logger.exception("Error tak terduga saat mengambil %s untuk %s", section, ticker)
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

    # -- perbandingan beberapa ticker sekaligus ------------------------------------

    def compare(
        self,
        tickers: List[str],
        dump_json: bool = False,
        dump_folder: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Ambil data komprehensif untuk beberapa ticker sekaligus, paralel
        antar-ticker (dan tiap ticker juga paralel antar-halaman lewat
        `get_etf_data`). Ticker yang gagal total tetap muncul di hasil
        dengan daftar error -- alih-alih membuat seluruh batch gagal.
        Urutan hasil mengikuti urutan `tickers` yang diberikan.
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
                    logger.error("Gagal total mengambil data untuk %s: %s", t, e)
                    results[t] = {
                        "ticker": t.strip().upper(),
                        "errors": [{"section": "all", "error": str(e)}],
                    }
        return [results[t] for t in tickers if t in results]

    # -- ekspor JSON -----------------------------------------------------------------

    def save_json(self, data: Dict[str, Any], dump_path: Optional[Path] = None) -> Path:
        """Simpan dict data ETF ke file JSON. Mengembalikan path file yang
        ditulis (berguna untuk ditampilkan ke user)."""
        output_path = Path(dump_path) if dump_path else Path(f"momo_{data.get('ticker', 'unknown').lower()}_data.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Data disimpan ke %s", output_path)
        return output_path

    # alias lama, dipertahankan agar kode lama yang memanggil nama privat ini
    # (mis. skrip lama sebelum v2) tetap berjalan tanpa perubahan.
    _dump_json_data = save_json


# =============================================================================
# 7. FUNGSI KEMUDAHAN (module-level) -- untuk pemakaian sekali pakai / skrip
#    cepat tanpa perlu membuat instance Momo secara eksplisit.
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


def compare(tickers: List[str], **config_overrides: Any) -> List[Dict[str, Any]]:
    with Momo(**config_overrides) as momo:
        return momo.compare(tickers)