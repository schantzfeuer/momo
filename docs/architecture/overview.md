# Architecture

Follow a request through Momo's shared client, provider fetchers, parsers, and presentation layers.

## Request flow

`Momo` is the shared orchestration entry point. It owns configuration, HTTP sessions, caching, lifecycle state, and run summaries. Asset classes adapt that client for one or more tickers.

1. `core.validation` checks ticker syntax.
2. `core.config` validates provider URLs against the allowed-host policy.
3. `core.http` performs requests using shared host limits and retry controls.
4. `extractors.sveltekit` or the HTML fallback extracts provider payloads.
5. `parsers/` converts provider-specific data into normalized sections.
6. `assemblers/` builds the canonical stock or ETF payload.
7. `objects.py` exposes stock, ETF, and batch interfaces.
8. `interface/` renders terminal output and standalone HTML dashboards.

## Subsystem ownership

| Area | Responsibility |
| --- | --- |
| `core/` | Configuration, HTTP lifecycle, cache, exceptions, validation, orchestration, persistence, and observability. |
| `extractors/` and `parsers/` | Provider compatibility and normalization. |
| `assemblers/` | Stable stock and ETF payload shapes. |
| `engine/` | Fundamental, technical, and integrated calculations. |
| `interface/` | CLI flow, terminal presentation, and HTML dashboards. |

## Shared invariants

- `core.units.normalize_financial()` is the production financial normalization path.
- `core.orchestration.FetchOrchestrator` is the shared parallel-fetch core.
- Cache reads and writes use defensive deep copies.
- JSON exports use same-directory temporary files and atomic replacement.
- Debug HTML is opt-in and written to a retention-bounded directory.
- Closed `Momo` instances raise `MomoClosedError` when asked to perform operations.
- Missing values remain unavailable rather than being converted to zero; partial errors are retained in results.

## Next Steps

- Review [ADR 0001](adr/0001-parser-orchestration.md) for the orchestration decision.
- Read [Provider contracts](../reference/provider-contracts.md) for the payload interfaces.
- Use the [Runbook](../operations/runbook.md) for operational troubleshooting.
