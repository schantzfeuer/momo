from __future__ import annotations

class MomoError(Exception):
    """Base class for all errors raised by momo."""

class MomoNetworkError(MomoError):
    """Raised when fetching a page fails after all retries are exhausted."""

class MomoParseError(MomoError):
    """Raised when parsing HTML/JSON on a page fails."""

class MomoValidationError(MomoError):
    """Raised when input is invalid, such as an empty or malformed ticker."""


class ProviderNetworkError(MomoNetworkError):
    """Raised for provider HTTP and transport failures."""


class RateLimitError(ProviderNetworkError):
    """Raised when provider throttling remains unresolved after retries."""


class SchemaMismatchError(MomoParseError):
    """Raised when provider markup or embedded JSON violates an expected contract."""


class DataValidationError(MomoValidationError):
    """Raised when parsed data cannot satisfy a canonical schema."""


class MomoClosedError(MomoError):
    """Raised when an operation is attempted after Momo has been closed."""
