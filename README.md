# MOMO — Stock and ETF Research CLI

MOMO collects public stock and ETF data and presents it as terminal reports or
JSON. Stock analysis combines business and financial data with technical
indicators, valuation estimates, and multi-timeframe scenarios. ETFs are
available through a separate workflow.

**Version:** `3.1.6a0` · **Python:** `3.10+`

MOMO is a research aid, not a trading system or personalized investment advice.
Valuations and projections are model-based estimates, not promises about future
prices.

## Installation

Create and activate a virtual environment, then install the package with its
analysis and CLI features:

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[analysis,cli]"
```

On macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[analysis,cli]"
```

Alternatively, install the complete runtime dependency set from
[`requirements.txt`](requirements.txt), then install the project for Python
imports:

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
```

The base package supports data collection and parsing. The `analysis` extra
installs NumPy, pandas, and yfinance; the `cli` extra installs Rich and Typer.
Analysis and CLI features require those extras.

## CLI usage

Run commands from the project folder. Stocks are the default; pass `--etf` to
select the ETF workflow.

```bash
# Analyze one stock
python main.py AAPL

# Compare stocks
python main.py AAPL MSFT NVDA --compare

# Fetch an ETF report
python main.py --etf VOO

# Compare ETFs
python main.py --etf VOO QQQ SCHD --compare

# Run a fundamental-only stock analysis without historical price downloads
python main.py AAPL --no-price

# Choose analysis timeframes
python main.py AAPL --timeframes 1m,3m,6m,12m

# Create and open a visual stock dashboard
python main.py AAPL --visual

# Request selected stock sections
python main.py AAPL --sections overview,financials,forecast

# Save stock data, analysis, and report JSON files
python main.py AAPL --json --output-dir output

# Save fetched HTML pages for parser debugging or offline parsing
python main.py AAPL --debug-html --output-dir downloaded_html

# Show all options
python main.py --help
```

### CLI options

| Option | Description |
| --- | --- |
| `TICKER...` | One or more ticker symbols. Stocks are assumed unless `--etf` is supplied. |
| `--etf` | Use the ETF workflow. |
| `--compare`, `-c` | Display a comparison for the supplied tickers. |
| `--sections`, `-s` | Comma-separated sections to fetch. Available sections depend on the selected asset type. |
| `--timeframes`, `-t` | Stock analysis horizons: any combination of `1m`, `3m`, `6m`, and `12m`. |
| `--period` | Historical price period for technical analysis; default `5y`. |
| `--interval` | Historical price interval; default `1d`. |
| `--price` / `--no-price` | Enable or disable automatic historical price retrieval. |
| `--visual` | Generate and open a standalone HTML stock dashboard with price, volume, momentum, and timeframe views. Requires historical price data; stocks only. |
| `--json` | Save JSON output. |
| `--output-dir`, `-o` | Directory for JSON and debug HTML output; default is the current directory. |
| `--cache` / `--no-cache` | Enable or disable in-memory caching. |
| `--timeout` | HTTP timeout in seconds; default `15`. |
| `--retries` | Number of HTTP retries; default `3`. |
| `--workers` | Maximum parallel stock-section requests; default `6`. |
| `--debug-html` | Save fetched HTML pages for parser debugging. |
| `--compact` | Use a more compact stock report layout without hiding report content. |
| `--verbose`, `-v` | Enable detailed logging. |

Stock section names include `overview`, `company`, `financials`,
`income_statement`, `balance_sheet`, `cash_flow`, `ratios`,
`financial_metrics`, `forecast`, `ratings`, `statistics`, `market_cap`,
`revenue`, `dividends`, `business_metrics`, `revenue_by_segment`,
`gross_profit_by_type`, `gross_margin_by_type`, `revenue_by_geography`, and
`operating_expense_breakdown`.

ETF sections include `overview`, `performance`, `holdings`, `sectors`,
`countries`, `dividends`, and `news`.

With the default stock section selection, MOMO discovers the business-metric
detail pages available for that ticker. Supplying `--sections` requests only
the selected stock sections.

When `--json` is used for a stock, output files include
`momo_<ticker>_stock.json`, `momo_<ticker>_analysis.json`, and
`momo_<ticker>_report.json`. For example:

```text
output/
  momo_aapl_stock.json
  momo_aapl_analysis.json
  momo_aapl_report.json
```

With `--visual`, MOMO writes `momo_<ticker>_visual.html` to the current
directory or the directory supplied with `--output-dir`, then opens it in the
default browser. The self-contained dashboard includes candlesticks, moving
averages, Bollinger Bands, support/resistance, volume, RSI, MACD, and
multi-timeframe projections.

### Stock analysis timeframes

Technical and fundamental weights vary by horizon:

| Key | Approximate horizon | Technical weight | Fundamental weight |
| --- | ---: | ---: | ---: |
| `1m` | 21 trading days | 80% | 20% |
| `3m` | 63 trading days | 65% | 35% |
| `6m` | 126 trading days | 45% | 55% |
| `12m` | 252 trading days | 25% | 75% |

Technical indicators are recalculated for each timeframe. Available analysis
includes moving averages, RSI, MACD, Stochastic, Bollinger Bands, ATR, VWAP,
OBV, Ichimoku, volatility, trend regression, and support/resistance levels.

## Python API usage

Install the package into the active environment with
`python -m pip install -e ".[analysis]"` for stock analysis. Data collection
and parsing use the core dependencies; running the command-line interface also
requires the `cli` extra.

### Quick start

Module-level functions are convenient for one-off requests:

```python
import momo

stock = momo.get_stock_data("AAPL")
print(stock["ticker"])
print(stock["quote"])

report = momo.analyze_stock("AAPL", timeframes=["1m", "6m", "12m"])
print(report["executiveSummary"])
print(report["fairValue"])

etf = momo.get_etf_data("VOO")
print(etf["holdings"])
```

`analyze_stock` fetches stock data and runs the integrated analysis. To analyze
an existing payload instead, use `analyze_stock_data`:

```python
import momo

stock = momo.get_stock_data("MSFT")
report = momo.analyze_stock_data(
    stock,
    auto_fetch_price=False,
    timeframes=["1m", "3m", "6m", "12m"],
)
```

Set `auto_fetch_price=False` when you do not want MOMO to retrieve historical
prices. Fundamental analysis remains available, while technical results need
price data.

### Reuse a client and configure requests

Use `Momo` for workflows that make multiple requests. It manages the HTTP
session and per-client cache; the context manager closes the session
automatically.

```python
from momo import Momo, MomoConfig

config = MomoConfig(
    timeout=30,
    max_retries=3,
    use_cache=True,
    max_workers=4,
)

with Momo(config=config) as client:
    stock = client.get_stock_data("AAPL")
    report = client.analyze_stock_data(stock)
```

`Momo` also accepts configuration overrides directly:

```python
with Momo(timeout=30, max_workers=4, use_cache=False) as client:
    overview = client.get_stock_overview("AAPL")
```

`MomoConfig` supports URL bases, request headers, timeout, retries and retry
backoff, cache settings, worker count, and a debug-output directory.

### Data retrieval APIs

The public module-level API provides:

| Purpose | Functions |
| --- | --- |
| ETF data | `get_overview`, `get_holdings`, `get_dividends`, `get_news`, `get_etf_data` |
| Unified entry point | `get_data(ticker, asset_type="etf" or "stock")` |
| Stock data | `get_stock_section`, `get_stock_data`, `get_stock_overview`, `get_stock_company`, `get_stock_financials`, `get_stock_statistics`, `get_stock_forecast`, `get_stock_ratings`, `get_stock_market_cap`, `get_stock_revenue`, `get_stock_dividends`, `get_stock_business_metrics` |
| Analysis | `analyze_stock`, `analyze_stock_data` |
| Comparisons | `compare` for ETFs, `compare_stocks` for stocks |

For example, retrieve only selected sections:

```python
import momo

stock = momo.get_stock_data(
    "NVDA",
    sections=["overview", "financials", "forecast", "ratings"],
)
```

With `sections=None` (the default), the stock data workflow requests the
standard sections and discovers available metric-detail pages. Providing an
explicit section list limits the request to those sections.

Use `dump_json=True` to write a data payload to JSON. `dump_path` can specify
the destination path:

```python
from pathlib import Path
import momo

stock = momo.get_stock_data(
    "AAPL",
    dump_json=True,
    dump_path=Path("output/aapl.json"),
)
```

### Analysis options and results

`analyze_stock` and `analyze_stock_data` accept `price_data`, `period`,
`interval`, `auto_fetch_price`, and `timeframes`. `analyze_stock` also accepts
`sections` to limit the stock data fetch. For example, pass your own
pandas-compatible OHLCV data and disable automatic price retrieval:

```python
import pandas as pd
import momo

stock = momo.get_stock_data("AAPL")
prices = pd.read_csv("aapl_ohlcv.csv", index_col=0, parse_dates=True)
report = momo.analyze_stock_data(
    stock,
    price_data=prices,
    auto_fetch_price=False,
)
```

An integrated analysis result includes:

- `executiveSummary` and `professionalReport`
- `timeframes`, with technical and blended results for each requested horizon
- `fairValue`, with fundamental, technical-reference, and blended estimates
- `fundamental`, including quality, business context, valuation methods, and consensus
- `technical`, with per-timeframe indicators and state
- `catalysts`, `risks`, `methodology`, and `disclaimer`

Values depend on source-data availability. Missing source data is not treated
as a zero-valued metric.

### Offline HTML parsing

Parse saved pages without making a network request:

```python
from pathlib import Path
import momo

stock = momo.parse_stock_directory(Path("downloaded_html"), ticker="AAPL")
# Or parse a mapping of section names to HTML strings:
stock = momo.parse_stock_snapshot({
    "overview": Path("downloaded_html/overview.html").read_text(encoding="utf-8"),
})
```

Parser helpers are also exported for individual ETF and stock page types,
including `parse_stock_section_html`, `parse_stock_overview_html`,
`parse_stock_financial_statement_html`, `parse_stock_forecast_html`,
`parse_stock_business_metrics_html`, and `parse_stock_metric_detail_html`.

### Legacy analysis API

The legacy technical analyzer remains available:

```python
from momo.engine import StockAnalyzer

result, dataframe = StockAnalyzer("AAPL").analyze_all()
print(result["Trend"])
print(result["RSI (14)"])
```

## Data sources and limitations

- Fundamentals, company data, and ETF pages are parsed from
  StockAnalysis.com.
- Historical stock prices are fetched through yfinance when automatic price
  retrieval is enabled.
- Sections may be unavailable for some tickers. MOMO reports available
  sections and source errors in the returned payload rather than implying
  missing data is zero.
- Valuation methods use estimates and fallback assumptions when inputs are
  incomplete. Review the report's confidence and methodology alongside the
  results.
- Results are for informational and research purposes only, not investment
  advice.

## Development and tests

Install the test extra and run the offline suite:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

Tests use local fixtures and mocks for data-fetching workflows; the suite does
not require live market-data requests.

## Project layout

```text
pyproject.toml
requirements.txt
main.py
interface/            Rich/Typer CLI and report rendering
src/momo/
  api.py              Module-level convenience functions
  client.py           HTTP client, caching, fetching, and JSON output
  core/               Configuration, exceptions, validation, HTTP, cache
  extractors/         Embedded-page extraction and normalization
  parsers/             ETF and stock page parsers
  assemblers/          Stable ETF and stock payload schemas
  engine/              Optional technical, fundamental, and report analysis
tests/                 Offline parser, client, and analysis tests
```
