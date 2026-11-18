from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Callable, Optional, Any, Mapping
from .exceptions import MomoValidationError

_TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9^]*(?:[.\-:/][A-Z0-9.^:/-]{1,15})?$")


@dataclass(frozen=True)
class ResolvedSymbol:
    input: str
    canonical: str
    aliases: tuple[str, ...] = ()
    resolvable: bool = True

def _validate_ticker(ticker: str) -> str:
    """Normalize and validate a ticker before sending an HTTP request."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise MomoValidationError("Ticker cannot be empty.")
    cleaned = ticker.strip().upper()
    if len(cleaned) > 18:
        raise MomoValidationError(f"Ticker format appears invalid: {ticker!r}")
    if "-" in cleaned and len(cleaned) > 12:
        raise MomoValidationError(f"Ticker format appears invalid: {ticker!r}")
    if not _TICKER_RE.match(cleaned):
        raise MomoValidationError(f"Ticker format appears invalid: {ticker!r}")
    return cleaned


def resolve_ticker(
    ticker: str,
    resolver: Optional[Callable[[str], Any]] = None,
) -> ResolvedSymbol:
    """Separate syntax validation from provider-side canonicalization."""
    cleaned = _validate_ticker(ticker)
    if resolver is None:
        return ResolvedSymbol(input=cleaned, canonical=cleaned, aliases=())
    resolved = resolver(cleaned)
    if isinstance(resolved, str):
        canonical = _validate_ticker(resolved)
        return ResolvedSymbol(input=cleaned, canonical=canonical, aliases=(cleaned,) if canonical != cleaned else ())
    if isinstance(resolved, Mapping):
        canonical_value = resolved.get("canonical") or resolved.get("symbol") or cleaned
        canonical = _validate_ticker(str(canonical_value))
        raw_aliases = resolved.get("aliases") or ()
        aliases = tuple(sorted({_validate_ticker(str(alias)) for alias in raw_aliases if str(alias).strip()}))
        return ResolvedSymbol(
            input=cleaned,
            canonical=canonical,
            aliases=aliases,
            resolvable=bool(resolved.get("resolvable", True)),
        )
    raise MomoValidationError("Provider ticker resolver returned an invalid result.")

validate_ticker = _validate_ticker
