from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


LOG_CONTEXT = ContextVar("momo_log_context", default={})
METRICS_LOCK = threading.Lock()
METRICS = {
    "fetches": 0,
    "errors": 0,
    "rate_limits": 0,
}


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = dict(LOG_CONTEXT.get())
        for name in ("request_id", "ticker", "provider", "duration_ms", "status_code"):
            value = getattr(record, name, None)
            if value is not None:
                fields[name] = value
        if not getattr(record, "_momo_formatted", False):
            record._momo_formatted = True
            record.msg = f"{record.getMessage()} | " + " ".join(
                f"{key}={value}" for key, value in sorted(fields.items())
            )
            record.args = ()
        return super().format(record)


def increment_metric(name: str, amount: int = 1) -> None:
    with METRICS_LOCK:
        if name in METRICS:
            METRICS[name] += amount


def get_metrics() -> dict[str, int]:
    with METRICS_LOCK:
        return dict(METRICS)


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    current = dict(LOG_CONTEXT.get())
    current.update({key: value for key, value in fields.items() if value is not None})
    token = LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        LOG_CONTEXT.reset(token)


def new_request_context(ticker: str | None = None, provider: str | None = None) -> dict[str, object]:
    return {
        "request_id": uuid.uuid4().hex,
        "ticker": ticker,
        "provider": provider,
    }


@contextmanager
def logging_context(level: int = logging.INFO) -> Iterator[None]:
    """Temporarily enable momo logging and always remove the temporary handler."""
    from ..client import enable_logging, disable_logging
    enable_logging(level)
    try:
        yield
    finally:
        disable_logging()
