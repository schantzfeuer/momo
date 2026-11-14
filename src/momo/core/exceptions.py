from __future__ import annotations

class MomoError(Exception):
    """Base class for all errors raised by momo."""

class MomoNetworkError(MomoError):
    """Raised when fetching a page fails after all retries are exhausted."""

class MomoParseError(MomoError):
    """Raised when parsing HTML/JSON on a page fails."""

class MomoValidationError(MomoError):
    """Raised when input is invalid, such as an empty or malformed ticker."""
