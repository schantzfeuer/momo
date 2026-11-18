# Command-line guide

Use the `momo` CLI to fetch stock or ETF data, compare tickers, and write reports.

> **Prerequisites:** Python 3.10 or later, the `cli` extra, and network access to the configured providers. Install the `analysis` extra for price-based or integrated analysis.

## Run a stock report

1. Pass one or more ticker symbols. Momo treats tickers as stocks unless you pass `--etf`.

   ```powershell
   py -m momo NVDA
   py -m momo NVDA MSFT GOOGL --compare
   ```

2. To fetch fundamentals without historical price data, add `--no-price`:

   ```powershell
   py -m momo NVDA --no-price
   ```

## Run an ETF report

1. Select the ETF flow with `--etf`:

   ```powershell
   py -m momo --etf VOO
   ```

2. Add tickers and `--compare` to request comparison output:

   ```powershell
   py -m momo --etf VOO QQQ SCHD --compare
   ```

The ETF flow generates a dedicated dashboard; the CLI's visual output option is therefore implicit for ETFs.

## Select sections and timeframes

Use `--sections` with a comma-separated list. Stock and ETF modes accept different section names; see the [Provider contracts reference](../reference/provider-contracts.md) and check `py -m momo --help` for the installed CLI description.

```powershell
py -m momo NVDA --sections overview,financials
py -m momo --etf VOO --sections overview,holdings,dividends
```

Use `--timeframes` to select one or more stock analysis horizons from `1m`, `3m`, `6m`, and `12m`:

```powershell
py -m momo NVDA --timeframes 1m,6m,12m
```

## Run price-only technical analysis

The `--technical` option runs technical analysis separately from the integrated stock report. It cannot be combined with `--visual`.

```powershell
py -m momo NVDA --technical
py -m momo NVDA --technical --period 10y --interval 1d --timeframes 1m,3m,6m,12m
```

The period and interval options control requested historical OHLCV data. Their accepted provider values are not specified by this project: [TODO: document supported period and interval values].

## Save outputs and inspect failures

1. Add `--json` to save JSON files and `--output-dir` to choose their directory:

   ```powershell
   py -m momo NVDA --json --output-dir output
   ```

2. Add `--visual` for a stock HTML research dashboard:

   ```powershell
   py -m momo NVDA --visual --output-dir output
   ```

3. If a fetch or parse fails, inspect the terminal error and `momo_run_summary.json` in the output directory. To save redacted provider HTML for parser troubleshooting, add `--debug-html`.

The project writes mode-specific files, including ticker-named JSON and HTML reports. Consult the output directory rather than relying on a fixed filename across modes.

## Common request controls

| Option | Default | Purpose |
| --- | --- | --- |
| `--timeout` | `15` seconds | Network request timeout; CLI minimum is `1`. |
| `--retries` | `3` | Number of HTTP retries; CLI range is `1`–`10`. |
| `--workers` | `6` | Parallel stock section workers; CLI range is `1`–`20`. |
| `--cache` / `--no-cache` | enabled | Toggle the in-memory cache. |
| `--verbose` / `-v` | disabled | Enable detailed Momo logging. |
| `--compact` | disabled | Pack stock report sections more tightly without hiding report content. |

Run `py -m momo --help` for the complete current option list.

## Next Steps

- See [Installation](../getting-started/installation.md) for optional dependencies.
- Use the [Python API guide](python-api.md) to integrate Momo in code.
- Use the [Runbook](../operations/runbook.md) to investigate partial results.
