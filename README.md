# MOMO — Stock and ETF Research Tool

MOMO turns public company and market data into a readable research report. Give it a stock ticker such as `AAPL` or an ETF ticker such as `VOO`, and it can collect the available data, calculate common indicators, and organize the results in the terminal or as JSON files.

It is designed to answer practical research questions:

- What does this company or fund do?
- How has its price behaved over different time horizons?
- What do its financial results, ratios, and analyst estimates suggest?
- Which risks, catalysts, support levels, and resistance levels deserve attention?
- How do several stocks or ETFs compare?

MOMO is a research aid, not a trading system or personalized investment advice. Fair values and projections are model-based estimates, not promises about future prices.

**Version:** `3.1.2`


## What MOMO analyzes

### Stocks

The stock workflow combines company information, financial statements, ratios, forecasts, analyst estimates, business context, technical indicators, quality scoring, and several valuation methods.

### ETFs

The ETF workflow provides fund profile, quote, performance, holdings, sectors, countries, dividends, and news data. ETFs are explicitly selected with `--etf` so they are not confused with companies.

### Multi-timeframe technical state

Technical state is recomputed for each horizon rather than reusing the same daily snapshot. Default sampling: **1M = 1D bars**, **3M = 2D bars**, **6M = 5D bars**, **12M = 10D bars**. RSI, MACD, moving averages, volatility, trend regression, support/resistance, and technical scores can therefore differ materially between timeframes.

Technical analysis is recalculated for each horizon instead of reusing one daily snapshot. The default horizons are:

| Key | Approximate horizon | Technical weight | Fundamental weight |
| --- | ---: | ---: | ---: |
| `1m` | 21 trading days | 80% | 20% |
| `3m` | 63 trading days | 65% | 35% |
| `6m` | 126 trading days | 45% | 55% |
| `12m` | 252 trading days | 25% | 75% |

Shorter horizons give more influence to price behavior; longer horizons give more influence to business fundamentals.

## Features

- Remains compatible with the legacy ETF API: overview, holdings, dividend, news, `get_etf_data()`, and `compare()`.
- Supports 20 stock sections: overview, company, financial overview, income statement, balance sheet, cash flow, ratios, financial metrics, forecast, analyst ratings, statistics, market cap, revenue, dividends, and various business metrics.
- Parses offline HTML snapshots through `parse_stock_directory()` / `parse_stock_snapshot()`.
- Normalizes financial statements into one row per period for analytics.
- Technical engine without the `ta` package: SMA/EMA, RSI, MACD, Stochastic, Bollinger Bands, ATR, VWAP, OBV, Ichimoku, support/resistance, volatility, and log-price regression.
- Multi-timeframe analysis default: **1m, 3m, 6m, 12m**.
- Fundamental quality score: growth, profitability, financial health, and shareholder return.
- Fundamental fair value using several methods: **DCF proxy**, **forward earnings multiple**, **FCF multiple**, and **analyst consensus**.
- Technical equilibrium/reference value and scenario band per timeframe.
- Blended fair value: shorter timeframes emphasize technical data; longer timeframes emphasize fundamentals.
- Business-context analysis using segment revenue, geography, margin by type, and R&D/SG&A growth.
- Professional deterministic report: executive summary, valuation view, timeframe outlook, catalysts, risks, tactical levels, and data-quality assessment.
- Graceful degradation: fundamental analysis remains available when the historical price provider is unavailable.

## Quick start for non-programmers

### 1. Install Python

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/). On Windows, enable **Add Python to PATH** during installation.

### 2. Open the project folder

Open a terminal in the folder containing `main.py`, or move there with:

```bash
cd path/to/Momo
```

### 3. Install MOMO

```bash
pip install -r requirements.txt
```

Core dependencies for parsing/analytics:

```bash
pip install requests beautifulsoup4 pandas numpy
```

For automatic historical OHLCV:

```bash
pip install yfinance curl_cffi
```

### 4. Run your first analysis

Stocks are the default:

```bash
python main.py AAPL
```

Replace `AAPL` with the ticker you want to research. The report includes key metrics, valuation information, technical state, risks, catalysts, and data-quality notes.

## Command-line usage

`main.py` is **stock-first**. Without a flag, tickers are treated as stocks and run through the fundamental, technical, and multi-timeframe valuation pipeline. ETFs use the explicit `--etf` flag.

```bash
# Stocks are the default
python main.py AAPL

# ETFs must be explicit
python main.py --etf VOO

# Multi-stock + comparison matrix
python main.py AAPL MSFT NVDA --compare

# Multi-ETF
python main.py --etf VOO QQQ SCHD --compare

# Fundamental-only when historical OHLCV is not needed
python main.py AAPL --no-price

# Custom horizon
python main.py AAPL --timeframes 1m,3m,6m,12m

# Save raw data and analysis results
python main.py AAPL --json --output-dir output

# Select stock data sections
python main.py AAPL --sections overview,financials,forecast

# Save downloaded HTML for later offline parsing
python main.py AAPL --debug-html --output-dir downloaded_html

# See every available option
python main.py --help
```

`--no-price` is useful when you only need fundamental information or when historical-price downloads are unavailable. Full technical analysis requires historical OHLCV data, either downloaded automatically or supplied by your own Python code.

The CLI uses `typer` for command parsing and `rich` for readable terminal reports.

With `--json --output-dir output`, MOMO writes files such as `momo_aapl_stock.json`, `momo_aapl_analysis.json`, and `momo_aapl_report.json`. These are ordinary JSON files that can be opened in a text editor or processed by another tool.

`technical.py` no longer requires the `ta` package.

## Quick start — ETF

```python
import momo

etf = momo.get_etf_data("VYM")
print(etf["profile"])
print(etf["quote"])
```

## Quick start — stock data

```python
import momo

stock = momo.get_stock_data("AAPL")
print(stock["quote"]["price"])
print(stock["financials"]["incomeStatement"]["rows"][0])
print(stock["analysis"]["forecast"]["priceTargets"])
```

## End-to-end professional stock analysis

```python
import momo

analysis = momo.analyze_stock("AAPL")

print(analysis["executiveSummary"])
print(analysis["fairValue"])
print(analysis["timeframes"]["1m"])
print(analysis["timeframes"]["12m"])
print(analysis["professionalReport"])
```

Default horizon:

| Key | Horizon | Technical Weight | Fundamental Weight |
|---|---:|---:|---:|
| `1m` | 21 trading days | 80% | 20% |
| `3m` | 63 trading days | 65% | 35% |
| `6m` | 126 trading days | 45% | 55% |
| `12m` | 252 trading days | 25% | 75% |

These weights can be replaced with `technical.TimeframeSpec` or a custom mapping.

## Offline Analysis from Local HTML + OHLCV

This workflow works with the `main.py` downloader:

```python
from pathlib import Path
import pandas as pd
import momo

stock_data = momo.parse_stock_directory(Path("downloaded_html"), ticker="AAPL")
price_data = pd.read_csv("aapl_ohlcv.csv", index_col=0, parse_dates=True)

analysis = momo.analyze_stock_data(
    stock_data,
    price_data=price_data,
    auto_fetch_price=False,
)
```

This prevents network requests during analysis.

## Main Analysis API

### `momo.analyze_stock(...)`

Fetches stock fundamental data and runs integrated analysis.

```python
momo.analyze_stock(
    ticker,
    price_data=None,
    sections=None,
    period="5y",
    timeframes=None,
    auto_fetch_price=True,
    include_raw_data=False,
    dump_json=False,
    dump_path=None,
)
```

### `momo.analyze_stock_data(...)`

Analyzes an existing stock payload. Suitable for offline snapshots or custom pipelines.

```python
momo.analyze_stock_data(
    stock_data,
    price_data=None,
    ticker=None,
    period="5y",
    timeframes=None,
    auto_fetch_price=True,
)
```

### `technical.ProfessionalStockAnalyzer`

Can be used directly without the MOMO network layer:

```python
from technical import ProfessionalStockAnalyzer

engine = ProfessionalStockAnalyzer(
    "AAPL",
    price_data=my_ohlcv_dataframe,
    fundamental_data=stock_data,
    auto_fetch=False,
)
report = engine.analyze(["1m", "3m", "6m", "12m"])
```

## Integrated Analysis Result Structure

```text
analysis
├── executiveSummary
├── professionalReport
├── timeframes
│   ├── 1m
│   ├── 3m
│   ├── 6m
│   └── 12m
├── fairValue
│   ├── fundamental
│   ├── technicalReferenceByTimeframe
│   └── blendedByTimeframe
├── fundamental
│   ├── quality
│   ├── businessContext
│   ├── methods
│   │   ├── dcf
│   │   ├── earningsMultiple
│   │   ├── fcfMultiple
│   │   └── analystConsensus
│   └── consensus
├── technical
│   └── timeframes
├── catalysts
├── risks
├── methodology
└── disclaimer
```

## Fair Value Interpretation

MOMO distinguishes three concepts:

1. **Fundamental fair value** — intrinsic/reference value estimated from cash flow, earnings, multiples, and analyst estimates.
2. **Technical reference value** — equilibrium/projection price based on trend, moving averages, VWAP, regression, support/resistance, momentum, and volatility.
3. **Blended fair value** — a combination of both according to the horizon. Technical data has more weight for shorter horizons; fundamentals have more weight for longer horizons.

Because valuation methods can produce different numbers, MOMO reports a **low/base/high range**, method dispersion, and confidence rather than a single target price.

## `technical.py` Backward Compatibility

The legacy API continues to work:

```python
from technical import StockAnalyzer

result, df = StockAnalyzer("AAPL").analyze_all()
print(result["Trend"])
print(result["RSI (14)"])
print(result["Ichimoku Signal"])
print(result["Multi Timeframe"])
```

`Ichimoku Signal` is now actually called by `analyze_all()`.

## Testing

The repository may include a project-specific test file named `test_momo_analysis.py`. When that file is present, run it from the project folder with:

```bash
pytest -q test_momo_analysis.py
```

The test suite covers the indicator engine, multi-timeframe analysis, JSON serialization, backward compatibility, the MOMO bridge, and the AAPL snapshot when HTML fixtures are available. If the test file is not present in your copy, use the CLI examples above as a smoke test and add your own automated tests around the public APIs.

## Methodology Notes

- MOMO DCF is a valuation proxy based on unlevered FCF when available; the model uses growth fade, WACC, terminal growth, and net cash.
- The earnings/FCF multiple uses historical multiple distributions when data is available.
- Analyst targets are used as an additional method, not as a single source of truth.
- Lynch/Graham fair value remains reference-only when available and is not given primary weight in consensus fair value.
- The technical scenario range uses trend regression and a realized-volatility envelope. The range is not a calibrated statistical confidence interval.
- All output is analytical model output, not a price guarantee or personalized investment advice.
