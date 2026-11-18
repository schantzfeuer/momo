"""Thread-safe bounded in-memory cache with per-entry expiration."""
from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Tuple


class TTLCache:
    def __init__(self, ttl: float, max_size: int = 1024):
        if max_size < 1:
            raise ValueError("max_size must be at least one.")
        self.ttl = ttl
        self.max_size = max_size
        self._entries: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._stale: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.RLock()
        self.metrics: Dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "expirations": 0,
        }

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, (created, _) in self._entries.items()
            if now - created > self.ttl
        ]
        for key in expired:
            self._stale[key] = copy.deepcopy(self._entries[key][1])
            self._stale.move_to_end(key)
            del self._entries[key]
        while len(self._stale) > self.max_size:
            self._stale.popitem(last=False)
        self.metrics["expirations"] += len(expired)

    def get(self, key: str) -> Any:
        with self._lock:
            now = time.monotonic()
            entry = self._entries.get(key)
            if entry is None:
                self.metrics["misses"] += 1
                return None
            created, value = entry
            if now - created > self.ttl:
                self._stale[key] = copy.deepcopy(value)
                self._stale.move_to_end(key)
                del self._entries[key]
                self.metrics["expirations"] += 1
                self.metrics["misses"] += 1
                while len(self._stale) > self.max_size:
                    self._stale.popitem(last=False)
                return None
            self._entries.move_to_end(key)
            self.metrics["hits"] += 1
            return copy.deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            if key in self._entries:
                del self._entries[key]
            self._entries[key] = (now, copy.deepcopy(value))
            self._stale[key] = copy.deepcopy(value)
            self._stale.move_to_end(key)
            self._entries.move_to_end(key)
            self.metrics["sets"] += 1
            while len(self._entries) > self.max_size:
                evicted_key, evicted_entry = self._entries.popitem(last=False)
                self._stale[evicted_key] = copy.deepcopy(evicted_entry[1])
                self._stale.move_to_end(evicted_key)
                self.metrics["evictions"] += 1
            while len(self._stale) > self.max_size:
                self._stale.popitem(last=False)

    def get_stale(self, key: str) -> Any:
        """Return the last cached value regardless of TTL for outage fallback."""
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return copy.deepcopy(entry[1])
            stale = self._stale.get(key)
            return copy.deepcopy(stale) if stale is not None else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._stale.clear()
