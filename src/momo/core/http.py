from __future__ import annotations
import time
from typing import Optional
import requests as req
from ..core.config import logger
from .config import MomoConfig
from .exceptions import MomoNetworkError

def _fetch_html(session: req.Session, url: str, config: MomoConfig) -> str:
    """Fetch provider HTML with transient retry and anti-block/empty-page detection."""
    last_exc: Optional[Exception] = None
    transient_statuses = {408, 425, 429, 500, 502, 503, 504}
    block_markers = (
        "cf-chl-", "cloudflare", "access denied", "temporarily unavailable",
        "too many requests", "verify you are human", "captcha",
    )
    for attempt in range(1, config.max_retries + 1):
        retry_after: Optional[float] = None
        try:
            response = session.get(url, timeout=config.timeout)
            if response.status_code in transient_statuses:
                header = response.headers.get("Retry-After")
                if header:
                    try:
                        retry_after = max(0.0, min(float(header), 15.0))
                    except (TypeError, ValueError):
                        retry_after = None
                raise req.HTTPError(f"Transient HTTP {response.status_code}", response=response)
            response.raise_for_status()
            html = response.text or ""
            sample = html[:12000].lower()
            if len(html.strip()) < 500:
                raise MomoNetworkError(f"Provider returned an unexpectedly small page ({len(html)} bytes)")
            marker = next((m for m in block_markers if m in sample), None)
            if marker:
                raise MomoNetworkError(f"Provider returned a challenge/block page ({marker})")
            return html
        except (req.RequestException, MomoNetworkError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Permanent 4xx failures should not consume the full retry budget.
            if status is not None and 400 <= int(status) < 500 and int(status) not in transient_statuses:
                break
            logger.debug(
                "Attempt %d/%d failed for %s (%s)", attempt, config.max_retries, url, exc
            )
            if attempt < config.max_retries:
                delay = retry_after if retry_after is not None else config.retry_backoff_base * (2 ** (attempt - 1))
                time.sleep(min(float(delay), 15.0))
    raise MomoNetworkError(
        f"Failed to fetch {url} after {config.max_retries} attempts: {last_exc}"
    ) from last_exc

fetch_html = _fetch_html
