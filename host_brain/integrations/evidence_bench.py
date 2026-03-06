"""Evidence Plane 写入延迟基准测量工具（M41-A）。

提供 EvidenceBench 类，用于测量 EvidencePlane 各 write_* 方法的 P50/P95/P99 延迟。
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Callable


class EvidenceBench:
    """Evidence Plane 写入延迟基准测量工具。

    用于测量 EvidencePlane 各 write_* 方法的延迟分布，
    为分级写入策略提供数据依据。
    """

    def measure_write_latency(
        self,
        method: Callable[..., Any],
        n: int = 1000,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, float]:
        """测量指定写入方法的延迟分布。

        Args:
            method: 要测量的写入方法（callable）
            n: 测量次数，默认 1000
            *args: 传递给 method 的位置参数
            **kwargs: 传递给 method 的关键字参数

        Returns:
            包含 p50/p95/p99/mean/max 的延迟统计字典（单位：毫秒）
        """
        if n <= 0:
            raise ValueError("n 必须大于 0")

        latencies: list[float] = []
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                method(*args, **kwargs)
            except Exception:
                pass
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)

        latencies_sorted = sorted(latencies)
        total = len(latencies_sorted)

        def _percentile(data: list[float], pct: float) -> float:
            idx = int(pct / 100.0 * total)
            idx = min(idx, total - 1)
            return data[idx]

        return {
            "p50": _percentile(latencies_sorted, 50),
            "p95": _percentile(latencies_sorted, 95),
            "p99": _percentile(latencies_sorted, 99),
            "mean": statistics.mean(latencies),
            "max": max(latencies),
        }
