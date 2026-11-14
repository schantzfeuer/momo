from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class MomoConfig:
    """Collect all customizable settings here instead of scattering them as
    module constants, allowing each `Momo` instance to use different settings.
    """

    # `base_url` is retained as the ETF URL base for v2 compatibility.
    base_url: str = "https://stockanalysis.com/etf"
    stock_base_url: str = "https://stockanalysis.com/stocks"
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = "en-US,en;q=0.9"
    timeout: float = 15.0
    max_retries: int = 3
    retry_backoff_base: float = 1.0
    use_cache: bool = True
    cache_ttl: float = 300.0  # seconds
    max_workers: int = 6
    debug_dir: Optional[Path] = None

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
