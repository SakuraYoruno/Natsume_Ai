"""Evidence Plane 写入优先级分级枚举（M41-B）。

提供 WritePriority 枚举，将 EvidencePlane 写入方法分为 CRITICAL（同步必写）
和 NON_CRITICAL（可异步）两级。
"""

from __future__ import annotations

from enum import Enum


class WritePriority(Enum):
    """Evidence Plane 写入优先级枚举。

    - CRITICAL：主权链路关键写入，必须同步执行，不可丢失。
      包括：write_route_token、write_branch_manifest、write_barrier_join
    - NON_CRITICAL：非关键写入，可异步队列缓冲。
      包括：write_llm_call、write_subgraph_trace、write_render_event
    """

    CRITICAL = "critical"
    NON_CRITICAL = "non_critical"


# 各 write_* 方法的默认优先级映射
WRITE_METHOD_PRIORITY: dict[str, WritePriority] = {
    "write_route_token": WritePriority.CRITICAL,
    "write_branch_manifest": WritePriority.CRITICAL,
    "write_barrier_join": WritePriority.CRITICAL,
    "write_llm_call": WritePriority.NON_CRITICAL,
    "write_subgraph_trace": WritePriority.NON_CRITICAL,
    "write_render_event": WritePriority.NON_CRITICAL,
    # 兼容现有方法名
    "write_trace": WritePriority.NON_CRITICAL,
    "write_subgraph_summary": WritePriority.NON_CRITICAL,
    "write_clock_events": WritePriority.NON_CRITICAL,
    "write_stream_events": WritePriority.NON_CRITICAL,
    "write_checkpoint": WritePriority.CRITICAL,
}
