# Momo: Stock and ETF Research

Momo collects public stock and ETF data and presents it as Python objects, terminal reports, JSON, or standalone HTML dashboards. It gives developers a consistent way to inspect provider data and run fundamental or price-based analysis. Estimates and indicators are research outputs, not investment advice or guaranteed forecasts.

## Quickstart

**Prerequisites:** Python 3.10 or later, pip, and network access to the configured data providers. The analysis extra includes TA-Lib, which may need a platform-specific native prerequisite; see [Installation](docs/getting-started/installation.md).

From the project directory:

```powershell
py -m pip install -e ".[analysis,cli]"
py -m momo AAPL
```

The first command installs the editable package with analysis and CLI dependencies. The second runs a stock report. Use `py -m momo --help` to see the CLI options.

## Core usage

### Research stocks and compare tickers

```powershell
py -m momo NVDA
py -m momo NVDA MSFT GOOGL --compare
```

### Research ETFs

Pass `--etf` to select the ETF flow:

```powershell
py -m momo --etf VOO
py -m momo --etf VOO QQQ SCHD --compare
```

### Save JSON or create a dashboard

```powershell
py -m momo NVDA --json --output-dir output
py -m momo NVDA --visual --output-dir output
```

The CLI writes generated files to the selected output directory. Output file names depend on the selected mode and ticker; see the [CLI guide](docs/guides/cli.md).

### Use the Python API

```python
from momo import Momo, MomoConfig

with Momo(config=MomoConfig(timeout=20, max_retries=3)) as momo:
    stock = momo.Stock("NVDA")
    overview = stock.get_overview()
    full_stock_data = stock.get_data()

    stocks = momo.Stocks("NVDA", "MSFT", "GOOGL")
    comparison = stocks.compare()
```

The client owns the shared HTTP session and cache; use it as a context manager so it closes cleanly. See the [Python API guide](docs/guides/python-api.md) for ETF, batch, and technical-analysis examples.

## Development update log

### 3.1.7a0

- Added an object-oriented Python API for stocks, ETFs, and ticker batches.
- Added `python -m momo` as the supported module entry point.
- Replaced single-purpose convenience modules with shared `Momo` client workflows.
- Added first-class valuation helpers and structured stock/ETF result contracts.
- Added ticker normalization and provider-symbol resolution.
- Added circuit breaking, host-level throttling, bounded I/O, retry cooldowns, and partial-run summaries.
- Added cache copy isolation, cache size limits, stale-cache behavior, and atomic JSON writes.
- Added structured logging, metrics, lifecycle and closed-client errors, and logging contexts.
- Added dependency upper bounds, a dependency lock file, strict test markers, and CI coverage.
- Added offline tests for parser behavior, valuation, normalization, networking, caching, CLI entry points, and batch operations.

## API migration from 3.1.6

Version 3.1.7a0 changes the recommended Python API from module-level convenience functions to reusable `Momo` objects. `src/momo/api.py` and the module-level helper exports from 3.1.6 are not part of the 3.1.7a0 public API.

The new API creates assets and batches through a context-managed client:

```python
from momo import Momo, MomoConfig

with Momo(config=MomoConfig(timeout=20, max_retries=3)) as momo:
    stock = momo.Stock("NVDA")
    overview = stock.get_overview()
    full_stock_data = stock.get_data()

    stocks = momo.Stocks("NVDA", "MSFT", "GOOGL")
    comparison = stocks.compare()
```

Replace common 3.1.6 calls as follows:

| 3.1.6 module-level call | 3.1.7a0 equivalent |
| --- | --- |
| `momo.get_stock_data("AAPL", sections=...)` | `momo.Momo().Stock("AAPL").get_data()` or `momo.Momo().get_stock_data("AAPL", sections=...)` |
| `momo.get_stock_overview("AAPL")` | `with momo.Momo() as client: client.Stock("AAPL").get_overview()` |
| `momo.get_stock_section("AAPL", "financials")` | `client.Stock("AAPL").get_section("financials")` |
| `momo.get_stock_company("AAPL")` | `client.Stock("AAPL").get_company()` |
| `momo.get_stock_financials("AAPL")` | `client.Stock("AAPL").get_financials()` |
| `momo.get_stock_forecast("AAPL")` | `client.Stock("AAPL").get_forecast()` |
| `momo.get_stock_statistics("AAPL")` | `client.Stock("AAPL").get_statistics()` |
| `momo.compare_stocks(["AAPL", "MSFT"])` | `client.Stocks("AAPL", "MSFT").compare()` |
| `momo.get_overview("VOO")` | `client.ETF("VOO").get_overview()` |
| `momo.get_holdings("VOO")` | `client.ETF("VOO").get_holdings()` |
| `momo.get_etf_data("VOO")` | `client.ETF("VOO").get_data()` |
| `momo.compare(["VOO", "QQQ"])` | `client.ETFs("VOO", "QQQ").compare()` |
| `momo.analyze_stock("AAPL", ...)` | `client.analyze_stock("AAPL", ...)` or `client.Technical("AAPL", ...)` |

For direct section and data access, the underlying `Momo` methods remain available:

```python
from momo import Momo

with Momo() as client:
    overview = client.get_stock_overview("AAPL")
    financials = client.get_stock_financials("AAPL")
    stock_data = client.get_stock_data("AAPL")
    etf_data = client.get_etf_data("VOO")
```

Always use `Momo` as a context manager when it manages a complete workflow, and call `close()` explicitly when not using the context manager. Calls made after `close()` raise `MomoClosedError`. This release also adds `ProviderNetworkError`, `RateLimitError`, `SchemaMismatchError`, `DataValidationError`, `resolve_ticker`, `get_metrics`, `logging_context`, and typed asset/batch classes.

## Configuration

There are no project-specific environment variables documented in the codebase. Configure the Python client with `MomoConfig`, or use the corresponding CLI options:

| Setting | Default | Description |
| --- | --- | --- |
| `timeout` / `--timeout` | `15` seconds | Network request timeout. |
| `max_retries` / `--retries` | `3` | HTTP retry count. |
| `max_workers` / `--workers` | `6` | Maximum parallel section-fetch workers; the effective limit is also bounded by host CPU count. |
| `use_cache` / `--cache` | `True` | Enable the in-memory cache. |
| `cache_ttl` | `300` seconds | Python API cache lifetime. |
| `debug_dir` / `--output-dir` | current directory | Base directory for CLI output and debug HTML. |

For provider URL restrictions, validation bounds, and the full settings list, see [Configuration reference](docs/reference/configuration.md).

## Documentation

### Getting started

- [Installation](docs/getting-started/installation.md) — install package extras and handle analysis dependencies.

### Guides

- [CLI guide](docs/guides/cli.md) — select modes, sections, output, and analysis options.
- [Python API guide](docs/guides/python-api.md) — fetch stock and ETF data with reusable clients.

### Architecture and concepts

- [Architecture](docs/architecture/overview.md) — follow a request through fetch, parsing, assembly, and presentation.
- [ADR 0001: Parser and fetch orchestration](docs/architecture/adr/0001-parser-orchestration.md) — understand the accepted orchestration decision.

### Reference

- [Configuration](docs/reference/configuration.md) — review `MomoConfig` values, defaults, and bounds.
- [Provider contracts](docs/reference/provider-contracts.md) — understand provider behavior, result shapes, and symbol resolution.

### Operations

- [Runbook](docs/operations/runbook.md) — diagnose incomplete runs and inspect generated summaries.
- [Release and rollback](docs/operations/release.md) — follow the repository's CI-backed build and recovery steps.

## Development and contributing

Install the project's test dependencies and run the test suite:

```powershell
py -m pip install -c requirements.lock -e ".[analysis,cli,test]"
py -m pytest -q
```

The CI workflow also compiles the source and runs coverage-enabled tests. No lint command or contributor submission policy is defined in the repository; [TODO: document the preferred lint tool and contribution workflow].

Momo is released under the [Unlicense](LICENSE).
