# Python API guide

Use a shared `Momo` client to fetch and inspect stock and ETF data from Python.

> **Prerequisites:** Python 3.10 or later and the base package. Install `analysis` for technical-analysis functions and market-data dependencies.

## Create and close a client

Use `Momo` as a context manager to close its HTTP session when work finishes:

```python
from momo import Momo, MomoConfig

config = MomoConfig(timeout=20, max_retries=3, max_workers=6)

with Momo(config=config) as momo:
    stock = momo.Stock("NVDA")
    overview = stock.get_overview()
    full_stock_data = stock.get_data()
```

`Momo` also accepts configuration keyword overrides, but pass a `MomoConfig` when you want an explicit, reusable configuration object.

## Fetch stock data

```python
with Momo() as momo:
    stock = momo.Stock("NVDA")
    overview = stock.get_overview()
    company = stock.get_company()
    financials = stock.get_financials()
    full_data = stock.get_data()
```

`Stock.get_data()` returns the full assembled stock payload and may also request market quote data through the optional `yfinance` dependency. Section methods return the requested section. `get_section()` without an argument returns full stock data; pass a section name to request one section.

## Fetch ETFs and batches

```python
with Momo() as momo:
    fund = momo.ETF("VOO")
    fund_data = fund.get_data()
    holdings = fund.get_holdings()

    funds = momo.ETFs("VOO", "QQQ", "SCHD")
    comparison = funds.compare()
```

Batch objects also provide `sort(metrics, order="descending")` and `choice(strategy="stable")`. The supported choice strategies are `growth`, `stable`, `dividend`, and `value`. Ranking depends on the metrics available in the fetched data; see errors and missing values in the returned payload.

## Analyze price data

`Momo.Technical()` analyzes historical prices independently of stock and ETF fundamentals. It fetches history by default; pass OHLCV data as `price_data` to analyze an existing DataFrame.

```python
with Momo() as momo:
    report = momo.Technical(
        "VOO",
        period="10y",
        interval="1d",
        auto_fetch_price=True,
        timeframes=["1m", "3m", "6m", "12m"],
    )
```

The `analysis` extra is required for this feature. Technical analysis requires TA-Lib. [TODO: document the accepted values for `period` and `interval` and the required DataFrame columns/index.]

## Save JSON

Asset `get_data()` methods accept `dump_json` and `dump_path`:

```python
from pathlib import Path
from momo import Momo

with Momo() as momo:
    stock = momo.Stock("NVDA")
    payload = stock.get_data(dump_json=True, dump_path=Path("nvda.json"))
```

Inspect the returned payload's `errors` and availability metadata before using derived values. Missing provider data is not equivalent to zero.

## Next Steps

- See the full option list in the [Configuration reference](../reference/configuration.md).
- Read [Provider contracts](../reference/provider-contracts.md) for result shapes and partial-data behavior.
- Review [Architecture](../architecture/overview.md) to understand the request path.
