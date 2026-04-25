from __future__ import annotations

import time
from typing import Dict

from host_brain.app.schemas import MetricsResponse


class RuntimeMetrics:
    """Lightweight per-process runtime counters for host main."""

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.total_requests: int = 0
        self.total_chat_requests: int = 0
        self.total_rejections: int = 0
        self.total_errors: int = 0
        self.reason_code_counts: Dict[str, int] = {}

    def inc_request(self) -> None:
        self.total_requests += 1

    def inc_chat(self) -> None:
        self.total_chat_requests += 1

    def inc_rejection(self, reason_code: str) -> None:
        self.total_rejections += 1
        self.reason_code_counts[reason_code] = self.reason_code_counts.get(reason_code, 0) + 1

    def inc_error(self) -> None:
        self.total_errors += 1

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self.start_time)


def build_metrics_response(metrics: RuntimeMetrics) -> MetricsResponse:
    return MetricsResponse(
        uptime_seconds=metrics.uptime_seconds,
        total_requests=metrics.total_requests,
        total_chat_requests=metrics.total_chat_requests,
        total_rejections=metrics.total_rejections,
        total_errors=metrics.total_errors,
        reason_code_distribution=dict(metrics.reason_code_counts),
        timestamp=int(time.time() * 1000),
    )


__all__ = ["RuntimeMetrics", "build_metrics_response"]
