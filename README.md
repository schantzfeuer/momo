# MOMO — ETF Data API

*Aku cinta my Yamamomo><*

**Versi:** `2.0.2`

---

## Daftar Isi

1. [Fitur Utama](#fitur-utama)
2. [Instalasi](#instalasi)
3. [Mulai Cepat](#mulai-cepat)
4. [Dua Gaya Pemakaian](#dua-gaya-pemakaian)
5. [Referensi API](#referensi-api)
   - [`MomoConfig`](#momoconfig)
   - [`Momo` (class utama)](#momo-class-utama)
   - [Fungsi kemudahan (module-level)](#fungsi-kemudahan-module-level)
   - [Fungsi parser murni](#fungsi-parser-murni)
   - [Exceptions](#exceptions)
   - [Logging / Debugging](#logging--debugging)
6. [Struktur Data (Skema JSON)](#struktur-data-skema-json)
7. [Contoh Lengkap](#contoh-lengkap)
8. [Penanganan Error](#penanganan-error)

---

## Fitur Utama

- Mengambil 4 jenis data ETF: **overview** (profil, harga, performa), **holdings** (isi portofolio, sektor, negara), **dividends** (riwayat & metrik dividen), dan **news** (berita gabungan dari ketiga halaman, sudah di-dedup).
- Pengambilan data paralel (multi-thread) untuk `get_etf_data()` dan `compare()` agar lebih cepat.
- Parsing dua lapis: mengambil JSON tersemat di halaman (jalur utama, cepat & akurat) dengan **fallback otomatis ke parsing HTML/CSS** bila struktur JSON tidak ditemukan.
- Retry otomatis dengan *exponential backoff* untuk error jaringan sementara.
- Cache in-memory sederhana (TTL) agar permintaan berulang untuk ticker yang sama tidak selalu memukul jaringan.
- Bisa dipakai sebagai *context manager* (`with Momo() as m:`) agar koneksi HTTP ditutup rapi.
- Data parsial tetap dikembalikan (beserta daftar error) jika salah satu halaman gagal diambil — tidak langsung gagal total.
- Ekspor hasil ke file JSON (`dump_json=True` atau `save_json()`).
- Fungsi parser (`parse_overview_html`, dll.) dipisah dari I/O jaringan sehingga mudah di-unit-test tanpa koneksi internet.

## Instalasi

Modul ini bergantung pada dua *library* pihak ketiga:

```bash
pip install requests beautifulsoup4
```

Lalu cukup letakkan `momo.py` di direktori proyek Anda (atau di `PYTHONPATH`) dan import seperti modul biasa:

```python
import momo
```

Membutuhkan **Python 3.8+**.

## Mulai Cepat

Cara tercepat: gunakan fungsi module-level, tanpa perlu membuat instance apa pun.

```python
import momo

data = momo.get_etf_data("VYM")
print(data["profile"]["name"])
print(data["quote"]["price"])
print(data["dividends"]["yield"])
```

Setiap panggilan fungsi module-level di atas membuka lalu menutup koneksi HTTP-nya sendiri secara otomatis — cocok untuk skrip sekali pakai.

## Dua Gaya Pemakaian

### 1. Fungsi module-level (sekali pakai / skrip cepat)

Praktis untuk pemakaian sesekali. Setiap panggilan membuat instance `Momo` sementara di baliknya.

```python
import momo

overview  = momo.get_overview("VYM")
holdings  = momo.get_holdings("VYM")
dividends = momo.get_dividends("VYM")
news      = momo.get_news("VYM")
full      = momo.get_etf_data("VYM")
```

### 2. Class `Momo` (kontrol penuh, reuse koneksi & cache)

Direkomendasikan bila Anda memanggil banyak ticker, ingin mengatur *timeout*/*retry*, atau ingin memakai cache in-memory antar-panggilan.

```python
from momo import Momo, MomoConfig

with Momo(config=MomoConfig(timeout=30, max_retries=5)) as m:
    overview  = m.get_overview("VYM")
    holdings  = m.get_holdings("VYM")
    dividends = m.get_dividends("VYM")
    news      = m.get_news("VYM")
    full      = m.get_etf_data("VYM")            # gabungan semuanya
    cmp_table = m.compare(["VYM", "SCHD", "QQQ"])  # bandingkan beberapa ETF sekaligus
```

Konfigurasi juga bisa diberikan langsung sebagai keyword, tanpa membuat `MomoConfig` eksplisit — keduanya setara:

```python
m1 = Momo(config=MomoConfig(timeout=30, max_retries=5))
m2 = Momo(timeout=30, max_retries=5)   # lebih singkat, hasil sama
```

---

## Referensi API

### `MomoConfig`

`dataclass` berisi semua pengaturan yang bisa dikustomisasi.

| Parameter | Tipe | Default | Keterangan |
|---|---|---|---|
| `base_url` | `str` | `"https://stockanalysis.com/etf"` | Basis URL sumber data |
| `user_agent` | `str` | Chrome desktop UA | Header `User-Agent` untuk tiap request |
| `accept_language` | `str` | `"en-US,en;q=0.9"` | Header `Accept-Language` |
| `timeout` | `float` | `15.0` | Timeout per request (detik) |
| `max_retries` | `int` | `3` | Jumlah percobaan ulang saat error jaringan |
| `retry_backoff_base` | `float` | `1.0` | Basis waktu tunggu antar-retry (exponential backoff) |
| `use_cache` | `bool` | `True` | Aktif/nonaktifkan cache in-memory |
| `cache_ttl` | `float` | `300.0` | Masa berlaku cache (detik) |
| `debug_dir` | `Optional[Path]` | `None` | Folder untuk menyimpan HTML mentah jika `save_debug_html=True` |

```python
from momo import MomoConfig

config = MomoConfig(timeout=30, max_retries=5, use_cache=False)
```

### `Momo` (class utama)

```python
Momo(save_debug_html: bool = False, config: Optional[MomoConfig] = None, **config_overrides)
```

Mendukung *context manager* (`with ... as m:`) agar `close()` terpanggil otomatis di akhir blok.

| Method | Return | Keterangan |
|---|---|---|
| `get_overview(ticker)` | `dict` | Profil, kutipan harga (quote), performa historis, cuplikan dividen & holdings |
| `get_holdings(ticker)` | `dict` | Daftar lengkap holdings, alokasi sektor & negara |
| `get_dividends(ticker)` | `dict` | Metrik dividen & riwayat pembayaran |
| `get_news(ticker, overview=None, holdings=None, dividends=None)` | `list[dict]` | Berita gabungan & sudah di-dedup dari 3 halaman. Bisa dikirim data yang sudah diambil sebelumnya agar tidak fetch ulang |
| `get_etf_data(ticker, dump_json=False, dump_path=None)` | `dict` | Data komprehensif (overview+holdings+dividends+news), diambil **paralel** |
| `compare(tickers, dump_json=False, dump_folder=None)` | `list[dict]` | `get_etf_data` untuk beberapa ticker sekaligus, paralel antar-ticker |
| `save_json(data, dump_path=None)` | `Path` | Menyimpan dict data ke file `.json` |
| `clear_cache()` | `None` | Mengosongkan cache in-memory instance ini |
| `close()` | `None` | Menutup HTTP session (dipanggil otomatis oleh `__exit__`) |

Semua method `get_*` melempar `MomoValidationError` bila ticker tidak valid, dan `MomoNetworkError` bila seluruh percobaan pengambilan gagal.

### Fungsi kemudahan (module-level)

Wrapper tipis di atas `Momo` — membuat instance sementara, menjalankan satu operasi, lalu menutupnya. Menerima `**config_overrides` yang sama dengan `MomoConfig`.

```python
momo.get_overview(ticker, **config_overrides)   -> dict
momo.get_holdings(ticker, **config_overrides)   -> dict
momo.get_dividends(ticker, **config_overrides)  -> dict
momo.get_news(ticker, **config_overrides)       -> list[dict]
momo.get_etf_data(ticker, dump_json=False, dump_path=None, **config_overrides) -> dict
momo.compare(tickers, **config_overrides)       -> list[dict]
```

Contoh dengan konfigurasi kustom:

```python
data = momo.get_etf_data("SCHD", timeout=20, max_retries=5)
```

### Fungsi parser murni

Fungsi ini **tidak melakukan I/O jaringan** — hanya menerima string HTML dan mengembalikan dict. Berguna untuk unit test (bisa diuji dengan HTML statis) atau untuk memproses ulang HTML yang sudah pernah disimpan.

```python
momo.parse_overview_html(html: str)  -> dict
momo.parse_holdings_html(html: str)  -> dict
momo.parse_dividend_html(html: str)  -> dict
momo.assemble_etf_data(ticker, overview=None, holdings=None, dividends=None, news=None, errors=None) -> dict
```

`assemble_etf_data` berguna bila Anda hanya butuh sebagian halaman (misal cuma dividend + news) tapi tetap ingin bentuk data akhir yang seragam dengan `get_etf_data`.

### Exceptions

```
MomoError                 # kelas dasar semua error momo
├── MomoNetworkError      # gagal fetch setelah semua retry habis
├── MomoParseError        # gagal mem-parsing HTML/JSON
└── MomoValidationError   # ticker kosong / format tidak valid
```

### Logging / Debugging

Modul ini "diam" secara default (`NullHandler`). Nyalakan log ke konsol saat data tidak sesuai ekspektasi — log akan menunjukkan halaman mana yang gagal, dan kapan fallback HTML dipakai (indikasi struktur situs sumber berubah):

```python
import momo
momo.enable_logging()          # default level INFO
# atau: momo.enable_logging(level=logging.DEBUG) untuk detail lebih lengkap
```

Untuk menyimpan HTML mentah tiap halaman yang diambil (berguna saat parser perlu di-debug):

```python
from momo import Momo
with Momo(save_debug_html=True, debug_dir="./debug_html") as m:
    m.get_overview("VYM")   # menulis ./debug_html/vym_overview_debug.html
```

---

## Struktur Data (Skema JSON)

Bentuk yang dikembalikan `get_etf_data()` / `compare()` / `assemble_etf_data()`:

```jsonc
{
  "ticker": "VYM",
  "generatedAt": "2026-08-05T12:00:00Z",

  "profile": {
    "name": "...", "type": "...", "subtype": "...",
    "exchange": "...", "provider": "...", "providerPage": "...",
    "inception": "YYYY-MM-DD", "expenseRatio": "...", "aum": "...",
    "nav": "...", "peRatio": "...", "sharesOutstanding": "...",
    "beta": "...", "holdingsCount": "...", "description": "...",
    "infoTable": { "...": "..." }, "website": "..."
  },

  "quote": {
    "price": 0.0, "open": 0.0, "high": 0.0, "low": 0.0,
    "previousClose": 0.0, "change": 0.0, "changePercent": 0.0,
    "asOf": "...", "tradingDate": "YYYY-MM-DD", "marketStatus": "...",
    "week52High": 0.0, "week52Low": 0.0, "volume": 0,
    "afterHours": { "price": 0.0, "change": 0.0, "changePercent": 0.0, "session": "...", "asOf": "..." },
    "raw": { "...": "field mentah lengkap dari sumber, sebagai cadangan" }
  },

  "performance": {
    "totalReturn": { "...": "..." },
    "historicalPriceLevels": { "...": "..." }
  },

  "dividends": {
    "yield": "...", "summary": "...",
    "metrics": {
      "yield": "...", "annualDividend": "...", "exDividendDate": "YYYY-MM-DD",
      "payoutFrequency": "...", "payoutRatio": "...", "dividendGrowth1Y": "..."
    },
    "history": [
      { "exDate": "YYYY-MM-DD", "cashAmount": "...", "recordDate": "YYYY-MM-DD", "payDate": "YYYY-MM-DD" }
    ]
  },

  "holdings": {
    "asOfDate": "YYYY-MM-DD", "totalHoldingsCount": "...",
    "summary": "...", "infoTable": { "...": "..." },
    "topHoldings": [
      { "no": "1", "symbol": "MSFT", "name": "Microsoft Corp", "weight": "...", "shares": "..." }
    ],
    "sectors": { "...": "..." },
    "countries": { "...": "..." }
  },

  "news": [
    { "title": "...", "url": "...", "source": "...", "published": "...", "summary": "...", "image": "...", "tickers": [] }
  ],

  "trust": {
    "overview": { "lastUpdated": "...", "sourceName": "...", "sourceUrl": "...", "updateFrequency": "...", "lastChecked": "..." },
    "holdings": { "..." : "..." },
    "dividend": { "..." : "..." }
  },

  "errors": [
    { "section": "holdings", "error": "pesan error di sini" }
  ]
}
```

> Catatan: field `errors` hanya muncul jika ada bagian yang gagal diambil (data parsial tetap dikembalikan untuk bagian lain yang berhasil). Field `quote.raw` disertakan sebagai jaring pengaman berisi data mentah lengkap, untuk kasus Anda butuh field yang belum dipetakan eksplisit.

Hasil `get_overview()`, `get_holdings()`, dan `get_dividends()` secara individual punya struktur yang sedikit berbeda (lebih mentah, per-halaman) — lihat isi fungsi `parse_overview_html`, `parse_holdings_html`, `parse_dividend_html` bila butuh detail per field.

---

## Contoh Lengkap

```python
import json
import momo
from momo import Momo, MomoConfig, MomoError

# 1) Aktifkan log untuk memantau proses (opsional)
momo.enable_logging()

# 2) Ambil data komprehensif satu ETF, dengan konfigurasi kustom
with Momo(config=MomoConfig(timeout=20, max_retries=4)) as m:
    try:
        data = m.get_etf_data("VYM", dump_json=True)  # otomatis disimpan ke momo_vym_data.json
    except MomoError as e:
        print("Gagal mengambil data:", e)
    else:
        print(f"{data['ticker']} — {data['profile']['name']}")
        print("Harga saat ini :", data["quote"]["price"])
        print("Dividend yield :", data["dividends"]["yield"])
        print("Jumlah holdings:", data["holdings"]["totalHoldingsCount"])
        if "errors" in data:
            print("Bagian yang gagal:", data["errors"])

    # 3) Bandingkan beberapa ETF sekaligus
    table = m.compare(["VYM", "SCHD", "QQQ"])
    for etf in table:
        print(etf["ticker"], "->", (etf.get("quote") or {}).get("price"))
```

Menyimpan hasil ke file JSON secara manual:

```python
data = momo.get_etf_data("VYM")
path = momo.Momo().save_json(data, dump_path="output/vym.json")
print("Tersimpan di:", path)
```

---

## Penanganan Error

Semua exception yang dilempar `momo` merupakan turunan dari `MomoError`, sehingga bisa ditangkap secara umum atau spesifik:

```python
from momo import Momo, MomoValidationError, MomoNetworkError, MomoParseError, MomoError

with Momo() as m:
    try:
        data = m.get_overview("VYM")
    except MomoValidationError:
        print("Ticker tidak valid.")
    except MomoNetworkError:
        print("Gagal menghubungi server setelah beberapa kali percobaan.")
    except MomoParseError:
        print("Struktur halaman berubah / gagal di-parsing.")
    except MomoError as e:
        print("Error momo lainnya:", e)
```

Untuk `get_etf_data()` dan `compare()`, kegagalan pada satu bagian/ticker **tidak menghentikan** proses secara keseluruhan — cukup periksa key `"errors"` pada hasilnya.

---
