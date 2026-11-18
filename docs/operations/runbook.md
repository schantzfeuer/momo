# Operational runbook

Use the generated run summary and payload errors to investigate incomplete CLI runs.

> **Prerequisites:** A completed CLI stock or ETF run and access to its output directory. Use `--output-dir` to choose that directory; `--debug-html` enables parser-debug artifacts.

## Inspect a run

1. Open `momo_run_summary.json` in the output directory.
2. Review `assetType`, `exitReason`, `attempted`, `available`, and `failed`.
3. Inspect the affected ticker payload's `errors` array for section-specific failures.

Stock and ETF pipeline summaries use these `exitReason` values:

| Value | Meaning |
| --- | --- |
| `ok` | Results exist and no tickers failed. |
| `partial` | Some results exist and some tickers failed. |
| `failed` | No ticker produced a result. |

`providerLatencyMs` is also present in the summary; the CLI pipeline currently writes an empty object for that field.

## Troubleshoot provider or parser failures

1. Confirm the ticker and mode. Use `--etf` for ETFs.
2. Check network access and DNS resolution for the configured HTTPS provider.
3. Re-run with `--verbose` to enable Momo's detailed logs.
4. If the provider is returning partial data, inspect `errors` and `_meta` rather than treating missing values as zero.
5. If parser behavior is the suspected cause, run with `--debug-html` and inspect the generated redacted HTML under `momo-debug/` within the configured output directory.
6. For repeated provider throttling, retry later or reduce parallelism with `--workers`. Increasing `--timeout` may help when responses are slow.

Debug files are retention-bounded by `debug_max_files`. The client attempts restrictive directory and file permissions where supported.

## Next Steps

- Read [Provider contracts](../reference/provider-contracts.md) to interpret partial data.
- Review [Configuration](../reference/configuration.md) for request and concurrency bounds.
- Use the [CLI guide](../guides/cli.md) to select output and diagnostics options.
