# Configuration reference

Use `MomoConfig` or CLI options to control provider requests, caching, and output behavior.

## Prerequisites

No separate setup is required to read this reference. To use these settings, install Momo; install optional extras for CLI or analysis functionality.

## Python configuration

`MomoConfig` is exported from `momo`. Its current defaults are defined in `src/momo/core/config.py`.

| Setting | Default | Bounds or behavior |
| --- | --- | --- |
| `etf_base_url` | `https://stockanalysis.com/etf` | HTTPS URL on an allowlisted host; no explicit port, credentials, query, or fragment. |
| `stock_base_url` | `https://stockanalysis.com/stocks` | HTTPS URL on an allowlisted host; no explicit port, credentials, query, or fragment. |
| `allowed_hosts` | `{"stockanalysis.com"}` | Non-empty set of hostnames; hostnames are normalized to lowercase. |
| `user_agent` | Browser-like user-agent string | Used in the `User-Agent` request header. |
| `accept_language` | `en-US,en;q=0.9` | Used in the `Accept-Language` request header. |
| `timeout` | `15.0` seconds | `0.1`–`300`. |
| `max_retries` | `3` | `1`–`20`. |
| `retry_backoff_base` | `1.0` seconds | `0`–`60`. |
| `rate_limit_per_second` | `2.0` | `0.01`–`100`. |
| `rate_limit_burst` | `4` | `1`–`100`. |
| `use_cache` | `True` | Enables the client cache. |
| `cache_ttl` | `300.0` seconds | `0`–`86,400`. |
| `cache_max_size` | `1024` | `1`–`100,000` entries. |
| `max_workers` | `6` | Requested range `1`–`64`; effective value is additionally capped by four times CPU count and 64. |
| `debug_dir` | `None` | Optional base directory for debug HTML artifacts. |
| `debug_max_files` | `20` | `1`–`1,000` retained debug files. |
| `ticker_resolver` | `None` | Optional callable used by ticker resolution. |

The numeric settings must be finite. Invalid settings raise `ValueError`. Provider URLs are resolved and checked when a `MomoConfig` instance is created; configuration therefore requires working DNS resolution for the configured provider host.

## CLI options

The CLI has a subset of Python configuration values. Current CLI defaults and limits:

| Option | Default | CLI constraint |
| --- | --- | --- |
| `--timeout` | `15.0` seconds | Minimum `1.0`. |
| `--retries` | `3` | `1`–`10`. |
| `--workers` | `6` | `1`–`20`. |
| `--cache` / `--no-cache` | enabled | Toggle cache use. |
| `--output-dir`, `-o` | `.` | Output directory for JSON and debug HTML. |
| `--debug-html` | disabled | Save redacted raw HTML for troubleshooting. |
| `--verbose`, `-v` | disabled | Enable detailed logging. |

Other report and analysis options are listed by `py -m momo --help` and described in the [CLI guide](../guides/cli.md).

## Environment variables

No Momo-specific environment variables are defined in the inspected source.

## Next Steps

- Use the [CLI guide](../guides/cli.md) to apply command-line settings.
- Follow the [Python API guide](../guides/python-api.md) for client examples.
- Read [Provider contracts](provider-contracts.md) before changing provider configuration.
