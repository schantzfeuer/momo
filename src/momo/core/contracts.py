from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class ETFOverviewContract:
    profile: Dict[str, Any] = field(default_factory=dict)
    quote: Dict[str, Any] = field(default_factory=dict)
    source: str = "html_fallback"


@dataclass(frozen=True)
class ETFHoldingsContract:
    as_of_date: Any = None
    total_holdings_count: Any = None
    holdings: tuple = ()
    sectors: Any = None
    countries: Any = None
    source: str = "html_fallback"
