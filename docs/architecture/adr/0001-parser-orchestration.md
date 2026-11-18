# ADR 0001: Shared parser and fetch orchestration

This decision centralizes normalization and parallel provider fetching across Momo's data paths.

## Status

Accepted.

## Context

ETF, stock, batch, parser, ranking, and CLI paths had duplicated normalization and failure behavior.

## Decision

- Centralize financial normalization in `core.units`.
- Centralize parallel provider fetches in `core.orchestration.FetchOrchestrator`.
- Keep asset classes as thin adapters.
- Use typed Momo exceptions and machine-readable run summaries to report failures.

## Consequences

Provider or parser failures are retained instead of silently discarded. Callers receive consistent partial-section shapes and can distinguish unavailable provider data from malformed application data.

## Next Steps

- Read [Architecture](../overview.md) for the end-to-end request flow.
- Review [Provider contracts](../../reference/provider-contracts.md) for partial-result semantics.
