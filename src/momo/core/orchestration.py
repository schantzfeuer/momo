from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from time import monotonic
from typing import Callable, Dict, Iterable, List, Mapping, Any
from .units import find_nested_number
from .exceptions import MomoValidationError

from .exceptions import MomoError


@dataclass
class RunSummary:
    asset_type: str
    attempted: list[str] = field(default_factory=list)
    available: list[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)
    provider_latency_ms: Dict[str, float] = field(default_factory=dict)
    exit_reason: str = "ok"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "assetType": self.asset_type,
            "exitReason": self.exit_reason,
            "attempted": list(self.attempted),
            "available": list(self.available),
            "failed": dict(self.failed),
            "providerLatencyMs": dict(self.provider_latency_ms),
        }


class FetchOrchestrator:
    """Shared bounded parallel fetch orchestration with consistent summaries."""

    def __init__(self, max_workers: int = 6):
        self.max_workers = max(1, int(max_workers))

    def run(
        self,
        asset_type: str,
        tasks: Mapping[str, Callable[[], Any]],
    ) -> tuple[Dict[str, Any], RunSummary]:
        summary = RunSummary(asset_type=asset_type, attempted=list(tasks))
        results: Dict[str, Any] = {}
        if not tasks:
            return results, summary
        with ThreadPoolExecutor(
            max_workers=max(1, min(len(tasks), self.max_workers))
        ) as executor:
            future_map = {
                executor.submit(task): name
                for name, task in tasks.items()
            }
            for future in as_completed(future_map):
                name = future_map[future]
                started = monotonic()
                try:
                    results[name] = future.result()
                    summary.available.append(name)
                except MomoError as exc:
                    summary.failed[name] = str(exc)
                except Exception as exc:
                    raise exc
                finally:
                    summary.provider_latency_ms[name] = round(
                        (monotonic() - started) * 1000.0, 3
                    )
        if summary.failed:
            summary.exit_reason = "partial" if summary.available else "failed"
        return results, summary


def sort_ranked_results(
    results: Iterable[Mapping[str, Any]],
    metrics: Iterable[str],
    order: str = "descending",
) -> List[Dict[str, Any]]:
    metric_names = [str(metric).strip().lower() for metric in metrics]
    direction = order.strip().lower()
    if direction not in {"ascending", "descending"}:
        raise MomoValidationError("order must be 'ascending' or 'descending'.")
    multiplier = -1 if direction == "descending" else 1

    def rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(
            (
                value is None,
                0.0 if value is None else value * multiplier,
            )
            for value in (
                find_nested_number(item, (metric,))
                for metric in metric_names
            )
        )

    return sorted(results, key=rank)
