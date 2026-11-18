# Provider contracts and provenance

Understand Momo's provider boundaries, normalized payloads, and partial-data behavior.

## Provider access

The default provider host is `stockanalysis.com`. Provider URLs must use HTTPS and an explicitly allowed host. The request client validates DNS results and disables redirects.

Stock and ETF data can be incomplete when a provider page or section is unavailable or changes shape. Momo records section errors and preserves unavailable values instead of substituting zero.

## Canonical payloads

| Asset | Top-level fields |
| --- | --- |
| ETF | `profile`, `quote`, `performance`, `dividends`, `holdings`, `news`, `trust`, `_meta`, `errors` |
| Stock | `ticker`, `assetType`, `profile`, `identity`, `quote`, `fundamentals`, `overview`, `company`, `financials`, `analysis`, `market`, `businessMetrics`, `news`, `trust`, `_meta`, `errors` |

Where applicable, parser provenance is recorded as `embedded_json` or `html_fallback`. Consult `_meta` for metadata and `errors` for section-level failures.

## Symbol validation and resolution

`validate_ticker()` validates syntax only; it does not prove a symbol exists with the provider. `resolve_ticker()` can call an optional resolver and returns:

| Field | Meaning |
| --- | --- |
| `input` | Original normalized symbol. |
| `canonical` | Provider canonical symbol, when resolved. |
| `aliases` | Provider aliases. |
| `resolvable` | Whether the provider resolved the symbol. |

The Python API also exposes `Momo.resolve_symbol()` to use the client-configured resolver.

## Failure and value semantics

- Missing data is represented as unavailable or `null` where appropriate, not as numeric zero.
- Partial parsing or fetch failures remain in the payload's `errors` collection.
- Ticker syntax errors and provider-resolution outcomes are separate concerns.
- Valuations and technical indicators are model outputs; they are not guaranteed outcomes.

## Next Steps

- Review [Architecture](../architecture/overview.md) for request and parsing flow.
- See [Configuration](configuration.md) for host and URL constraints.
- Follow the [Runbook](../operations/runbook.md) when a provider response changes or fails.
