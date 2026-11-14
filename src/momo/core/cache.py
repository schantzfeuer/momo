"""Thread-safe in-memory cache with per-entry expiration."""
from __future__ import annotations
import threading, time
from typing import Any, Dict, Tuple

class TTLCache:
    def __init__(self, ttl: float):
        self.ttl=ttl
        self._entries: Dict[str, Tuple[float, Any]]={}
        self._lock=threading.RLock()
    def get(self,key: str) -> Any:
        with self._lock:
            entry=self._entries.get(key)
            if entry is None:return None
            created,value=entry
            if time.monotonic()-created>self.ttl:
                del self._entries[key]
                return None
            return value
    def set(self,key: str,value: Any) -> None:
        with self._lock:self._entries[key]=(time.monotonic(),value)
    def clear(self) -> None:
        with self._lock:self._entries.clear()
