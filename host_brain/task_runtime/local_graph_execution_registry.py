"""用于加速持久化图取消的进程内执行句柄。

registry 永远不是取消权威：它只持有协程任务句柄与进程内锁；持久化 Store 状态仍是
执行围栏。本地取消仅帮助尽快释放本进程，不能替代跨进程的取消决定。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import math
from typing import Any


_MIN_CANCEL_WAIT_SECONDS = 0.1
_MAX_CANCEL_WAIT_SECONDS = 5.0
_GraphKey = tuple[str, int]


class LocalGraphExecutionRegistry:
    """追踪本地运行的图执行协程，不保留其 Coordinator 或业务授权。"""

    __slots__ = ("_lock", "_tasks")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[_GraphKey, set[asyncio.Task[Any]]] = {}

    @asynccontextmanager
    async def track(
        self,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> AsyncIterator[None]:
        """登记当前协程，并无论正常结束、异常或取消都在退出时注销。"""

        graph_key = _graph_key(graph_id, graph_revision)
        current = asyncio.current_task()
        if current is None:
            yield
            return

        registered = False
        async with self._lock:
            active = self._tasks.setdefault(graph_key, set())
            if current not in active:
                active.add(current)
                registered = True
        try:
            yield
        finally:
            # finally 清理防止已终结任务残留；残留会导致以后取消误伤无关的执行者。
            if registered:
                async with self._lock:
                    active = self._tasks.get(graph_key)
                    if active is not None:
                        active.discard(current)
                        if not active:
                            self._tasks.pop(graph_key, None)

    async def cancel_and_join(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        intent_ref: str,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        """取消同图的其他本地活跃执行者，并返回限时仍未退出的任务名。

        调用此函数的前提是持久化取消意图已建立；它不修改 durable state，也不把
        超时等待解释为远端执行已经停止。
        """

        graph_key = _graph_key(graph_id, graph_revision)
        intent = str(intent_ref or "").strip()
        if not intent:
            raise ValueError("intent_ref is required")
        bounded_timeout = _bounded_timeout_seconds(timeout_seconds)
        current = asyncio.current_task()

        async with self._lock:
            tasks = tuple(
                task
                for task in self._tasks.get(graph_key, ())
                if task is not current and not task.done()
            )

        if not tasks:
            return ()
        for task in tasks:
            task.cancel(f"durable graph cancellation: {intent}")
        done, pending = await asyncio.wait(tasks, timeout=bounded_timeout)
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        return tuple(sorted(task.get_name() for task in pending))


def _graph_key(graph_id: str, graph_revision: int) -> _GraphKey:
    """构造带修订号的图键，避免不同版本的恢复/取消互相干扰。"""
    graph = str(graph_id or "").strip()
    if not graph:
        raise ValueError("graph_id is required")
    if (
        isinstance(graph_revision, bool)
        or not isinstance(graph_revision, int)
        or graph_revision < 1
    ):
        raise ValueError("graph_revision must be a positive integer")
    return graph, graph_revision


def _bounded_timeout_seconds(value: float) -> float:
    """验证并夹紧本地 join 时限，避免取消清理无限阻塞调度主链。"""
    if isinstance(value, bool):
        raise ValueError("timeout_seconds must be a positive finite number")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "timeout_seconds must be a positive finite number"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")
    return min(
        _MAX_CANCEL_WAIT_SECONDS,
        max(_MIN_CANCEL_WAIT_SECONDS, timeout),
    )


__all__ = ["LocalGraphExecutionRegistry"]
