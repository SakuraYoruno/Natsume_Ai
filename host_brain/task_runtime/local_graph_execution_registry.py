"""Process-local execution handles for durable graph cancellation acceleration.

The registry is never cancellation authority.  It owns only task handles and
an in-process lock; durable Store state remains the execution fence.
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
    """Track local graph executors without retaining their coordinator."""

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
        """Track the current task and always unregister it on scope exit."""

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
        """Cancel other live local executors and return bounded pending names."""

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
