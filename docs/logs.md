# Development update log

## 3.1.7a1

- Fixed AUM value selection and formatting consistency between ETF CLI and visual reports.
- Fixed stock visual percentage scaling and analyst rating action/rating display.

## 3.1.7a0

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