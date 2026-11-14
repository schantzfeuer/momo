"""Small shared analytical helpers that do not depend on other engine modules."""
from __future__ import annotations

from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
