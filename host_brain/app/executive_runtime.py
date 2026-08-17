"""已退役 Executive Runtime 的失败关闭导入边界。

任何生产 Host 路由都不得借此构造 Executive 端口、选路、规划或写入第二条分发历史；
保留模块仅为使遗留导入得到与核心兼容保护一致的明确拒绝。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

async def executive_runtime_sse_generator(**kwargs: Any) -> AsyncGenerator[str, None]:
    """拒绝遗留 Executive 入口且不构造服务图。

已声明的业务路由必须转入受治理 Task Runtime，状态读取必须使用持久投影；函数保留
异步生成器形状只是兼容调用签名，绝不会产生 SSE 事件或执行任何计划。
"""

    declared_route = str(
        dict(kwargs.get("intent_draft") or {}).get("route") or ""
    ).strip().lower()
    if declared_route in {"chat", "skill", "delegate", "plan"}:
        raise PermissionError("CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME")
    if declared_route in {"task_progress", "recent_turn"}:
        raise PermissionError("READ_ROUTE_REQUIRES_DURABLE_PROJECTION")
    raise PermissionError("EXECUTIVE_RUNTIME_RETIRED")
    if False:  # pragma: no cover - preserves the async-generator contract.
        yield ""


__all__ = [
    "executive_runtime_sse_generator",
]
