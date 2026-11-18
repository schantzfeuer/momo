from __future__ import annotations
import random
import threading
import time
from typing import Dict, Optional
import requests as req
from requests.adapters import HTTPAdapter
from urllib.parse import urlsplit
from ..core.config import logger
from .config import MomoConfig, validate_provider_url
from .exceptions import MomoNetworkError, ProviderNetworkError, RateLimitError
from .observability import increment_metric

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.failures = 0
        self.state = "closed"
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow_request(self) -> bool:
        with self._lock:
            if self.state == "open":
                if time.monotonic() - self.opened_at >= self.reset_seconds:
                    self.state = "half-open"
                    return True
                return False
            return True

    def on_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = "closed"

    def on_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = "open"
                self.opened_at = time.monotonic()


class ManagedSession:
    """Thread-local requests sessions with bounded connection pools."""

    def __init__(self, config: MomoConfig):
        self.config = config
        self._local = threading.local()
        self._sessions: Dict[int, req.Session] = {}
        self._lock = threading.Lock()

    def get(self) -> req.Session:
        ident = threading.get_ident()
        with self._lock:
            session = self._sessions.get(ident)
            if session is None:
                session = req.Session()
                session.headers.update(self.config.headers)
                pool = max(1, self.config.max_workers)
                adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool, pool_block=True)
                session.mount("http://", adapter)
                session.mount("https://", adapter)
                self._sessions[ident] = session
            return session

    def close(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                session.close()
            self._sessions.clear()


BREAKERS: Dict[str, CircuitBreaker] = {}
BREAKERS_LOCK = threading.Lock()
IO_SEMAPHORE_LOCK = threading.Lock()
IO_SEMAPHORES: Dict[int, threading.BoundedSemaphore] = {}


def io_semaphore(limit: int) -> threading.BoundedSemaphore:
    with IO_SEMAPHORE_LOCK:
        return IO_SEMAPHORES.setdefault(max(1, limit), threading.BoundedSemaphore(max(1, limit)))


def circuit_breaker(provider: str = "stockanalysis") -> CircuitBreaker:
    with BREAKERS_LOCK:
        return BREAKERS.setdefault(provider, CircuitBreaker())


class TokenBucket:
    def __init__(self, rate: float, burst: int):
        self.rate = rate
        self.capacity = float(max(1, burst))
        self.tokens = self.capacity
        self.updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self.updated_at)
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.updated_at = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                delay = (1.0 - self.tokens) / self.rate
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(delay, 0.05))


HOST_BUCKETS: Dict[str, TokenBucket] = {}
HOST_COOLDOWNS: Dict[str, float] = {}
HOST_LIMIT_LOCK = threading.Lock()


def host_bucket(host: str, config: MomoConfig) -> TokenBucket:
    key = host.lower().rstrip(".")
    with HOST_LIMIT_LOCK:
        bucket = HOST_BUCKETS.get(key)
        if bucket is None:
            bucket = TokenBucket(config.rate_limit_per_second, config.rate_limit_burst)
            HOST_BUCKETS[key] = bucket
        return bucket


def wait_for_host_budget(host: str, config: MomoConfig) -> None:
    key = host.lower().rstrip(".")
    with HOST_LIMIT_LOCK:
        cooldown_until = HOST_COOLDOWNS.get(key, 0.0)
    remaining = cooldown_until - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    if not host_bucket(key, config).acquire():
        raise RateLimitError("Host-wide request budget timeout exceeded.")


def set_host_cooldown(host: str, seconds: float) -> None:
    key = host.lower().rstrip(".")
    with HOST_LIMIT_LOCK:
        until = time.monotonic() + max(0.0, seconds)
        if until > HOST_COOLDOWNS.get(key, 0.0):
            HOST_COOLDOWNS[key] = until


def _fetch_html(session: req.Session | ManagedSession, url: str, config: MomoConfig) -> str:
    """Fetch provider HTML with transient retry and anti-block/empty-page detection."""
    last_exc: Optional[Exception] = None
    transient_statuses = {408, 425, 429, 500, 502, 503, 504}
    block_markers = (
        "cf-chl-", "cloudflare", "access denied", "temporarily unavailable",
        "too many requests", "verify you are human", "captcha",
    )
    increment_metric("fetches")
    breaker = circuit_breaker(urlsplit(url).netloc)
    if not breaker.allow_request():
        raise MomoNetworkError("Circuit breaker is open; stale cached data should be used")
    for attempt in range(1, config.max_retries + 1):
        retry_after: Optional[float] = None
        try:
            wait_for_host_budget(urlsplit(url).hostname or urlsplit(url).netloc, config)
            managed = session.get() if isinstance(session, ManagedSession) else session
            with io_semaphore(config.max_workers):
                response = managed.get(url, timeout=config.timeout, allow_redirects=False)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise MomoNetworkError("Provider returned a redirect without a Location header")
                from urllib.parse import urljoin
                redirect_url = urljoin(url, location)
                try:
                    redirect_url = validate_provider_url(redirect_url, config.allowed_hosts)
                except ValueError as exc:
                    raise MomoNetworkError(
                        f"Provider redirect was rejected by the URL security policy."
                    ) from exc
                raise MomoNetworkError(
                    f"Provider redirect was rejected; redirects are disabled: {redirect_url}"
                )
            if response.status_code in transient_statuses:
                if response.status_code == 429:
                    increment_metric("rate_limits")
                header = response.headers.get("Retry-After")
                if header:
                    try:
                        retry_after = max(0.0, min(float(header), 15.0))
                    except (TypeError, ValueError):
                        retry_after = None
                if retry_after is not None:
                    set_host_cooldown(urlsplit(url).hostname or urlsplit(url).netloc, retry_after)
                raise req.HTTPError(f"Transient HTTP {response.status_code}", response=response)
            response.raise_for_status()
            html = response.text or ""
            sample = html[:12000].lower()
            if len(html.strip()) < 500:
                raise MomoNetworkError(f"Provider returned an unexpectedly small page ({len(html)} bytes)")
            marker = next((m for m in block_markers if m in sample), None)
            if marker:
                raise MomoNetworkError(f"Provider returned a challenge/block page ({marker})")
            breaker.on_success()
            return html
        except (req.RequestException, MomoNetworkError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and (int(status) == 429 or int(status) >= 500):
                breaker.on_failure()
            elif status is None:
                breaker.on_failure()
            # Permanent 4xx failures should not consume the full retry budget.
            if status is not None and 400 <= int(status) < 500 and int(status) not in transient_statuses:
                break
            logger.debug(
                "Attempt %d/%d failed for %s (%s)", attempt, config.max_retries, url, exc
            )
            if attempt < config.max_retries:
                delay = retry_after if retry_after is not None else config.retry_backoff_base * (2 ** (attempt - 1))
                delay = random.uniform(0.0, min(float(delay), 15.0))
                time.sleep(delay)
    status = getattr(getattr(last_exc, "response", None), "status_code", None)
    detail = f" (HTTP {status})" if status is not None else ""
    status_code = getattr(getattr(last_exc, "response", None), "status_code", None)
    increment_metric("errors")
    error_type = RateLimitError if status_code == 429 else ProviderNetworkError
    raise error_type(
        f"Unable to retrieve the requested financial data after {config.max_retries} attempts{detail}."
    ) from last_exc

fetch_html = _fetch_html
