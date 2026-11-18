from __future__ import annotations

import logging
import ipaddress
import socket
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Callable, Any
from urllib.parse import urlsplit

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

LIMITS = {
    "timeout": (0.1, 300.0),
    "max_retries": (1, 20),
    "retry_backoff_base": (0.0, 60.0),
    "rate_limit_per_second": (0.01, 100.0),
    "rate_limit_burst": (1, 100),
    "cache_ttl": (0.0, 86_400.0),
    "cache_max_size": (1, 100_000),
    "max_workers": (1, 64),
    "debug_max_files": (1, 1_000),
}


def effective_worker_limit(requested: int) -> int:
    available = max(1, os.cpu_count() or 1)
    host_limit = min(64, available * 4)
    return min(host_limit, max(1, int(requested)))


def validate_provider_url(url: str, allowed_hosts: FrozenSet[str]) -> str:
    """Normalize and validate a provider base URL against an explicit host policy."""
    try:
        parts = urlsplit(str(url).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("provider URL is malformed.") from exc
    if parts.scheme.lower() != "https":
        raise ValueError("provider URLs must use the https scheme.")
    if not parts.hostname:
        raise ValueError("provider URL must include a hostname.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("provider URLs cannot contain embedded credentials.")
    try:
        if parts.port is not None:
            raise ValueError("provider URLs cannot include explicit ports.")
    except ValueError as exc:
        raise ValueError("provider URL has an invalid port.") from exc
    if parts.query or parts.fragment:
        raise ValueError("provider URLs cannot contain query strings or fragments.")

    host = parts.hostname.lower().rstrip(".")
    if host not in allowed_hosts:
        raise ValueError(f"provider hostname is not allowlisted: {host}")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError(f"unable to resolve provider hostname: {host}") from exc
    if not addresses:
        raise ValueError(f"provider hostname resolved to no addresses: {host}")
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        ):
            raise ValueError(f"provider hostname resolved to a prohibited address: {host}")

    path = parts.path.rstrip("/")
    return f"https://{host}{path}"


@dataclass
class MomoConfig:
    """Collect all customizable settings here instead of scattering them as
    module constants, allowing each `Momo` instance to use different settings.
    """

    etf_base_url: str = "https://stockanalysis.com/etf"
    stock_base_url: str = "https://stockanalysis.com/stocks"
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = "en-US,en;q=0.9"
    timeout: float = 15.0
    max_retries: int = 3
    retry_backoff_base: float = 1.0
    rate_limit_per_second: float = 2.0
    rate_limit_burst: int = 4
    use_cache: bool = True
    cache_ttl: float = 300.0  # seconds
    cache_max_size: int = 1024
    max_workers: int = 6
    debug_dir: Optional[Path] = None
    debug_max_files: int = 20
    allowed_hosts: FrozenSet[str] = frozenset({"stockanalysis.com"})
    ticker_resolver: Optional[Callable[[str], Any]] = None

    def __post_init__(self) -> None:
        numeric_fields = (
            "timeout",
            "max_retries",
            "retry_backoff_base",
            "rate_limit_per_second",
            "rate_limit_burst",
            "cache_ttl",
            "cache_max_size",
            "max_workers",
            "debug_max_files",
        )
        for name in numeric_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric.")
        for name in numeric_fields:
            value = float(getattr(self, name))
            minimum, maximum = LIMITS[name]
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"{name} must be finite.")
            if value < minimum or value > maximum:
                raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
        object.__setattr__(self, "max_workers", effective_worker_limit(self.max_workers))
        normalized_hosts = frozenset(str(host).strip().lower().rstrip(".") for host in self.allowed_hosts)
        if not normalized_hosts:
            raise ValueError("allowed_hosts cannot be empty.")
        object.__setattr__(self, "allowed_hosts", normalized_hosts)
        self.etf_base_url = validate_provider_url(self.etf_base_url, normalized_hosts)
        self.stock_base_url = validate_provider_url(self.stock_base_url, normalized_hosts)

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
        }

logger = logging.getLogger("momo")
logger.addHandler(logging.NullHandler())
