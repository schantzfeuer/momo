from __future__ import annotations
import re
from .exceptions import MomoValidationError

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}(\.[A-Z]{1,3})?$")

def _validate_ticker(ticker: str) -> str:
    """Normalize and validate a ticker before sending an HTTP request."""
    if not isinstance(ticker, str) or not ticker.strip():
        raise MomoValidationError("Ticker cannot be empty.")
    cleaned = ticker.strip().upper()
    if not _TICKER_RE.match(cleaned):
        raise MomoValidationError(f"Ticker format appears invalid: {ticker!r}")
    return cleaned

validate_ticker = _validate_ticker
