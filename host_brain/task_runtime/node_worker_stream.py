"""Authority-free worker stream normalization and interruption cleanup."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .coordinator_records import _mapping
from .errors import (
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_TIME_BUDGET_EXCEEDED,
    GOVERNED_WORKER_FAILED,
    GovernedRuntimeError,
)
from .heartbeat import DurableExecutionHeartbeat
from .node_execution_records import _is_terminal_commit


async def worker_event_stream(
    worker: Any,
    *,
    command: HostCommand,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> AsyncIterator[Mapping[str, Any]]:
    stream_method = getattr(worker, "execute_stream", None)
    if callable(stream_method):
        stream = stream_method(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
        )
        if inspect.isawaitable(stream):
            stream = await stream
        if hasattr(stream, "__aiter__"):
            async for event in stream:
                yield event
            return
        if isinstance(stream, Sequence) and not isinstance(
            stream,
            (str, bytes, bytearray),
        ):
            for event in stream:
                yield event
            return
        raise GovernedRuntimeError(
            GOVERNED_WORKER_FAILED,
            "worker stream is not iterable",
        )

    execute_method = getattr(worker, "execute", None)
    if not callable(execute_method):
        raise GovernedRuntimeError(
            GOVERNED_WORKER_FAILED,
            "selected worker exposes neither execute_stream nor execute",
        )
    result = execute_method(
        command=command,
        work_order=work_order,
        execution_context=execution_context,
    )
    result = await _maybe_await(result)
    payload = _mapping(result, field_name="worker result")
    if not _is_terminal_commit(payload):
        payload = {**payload, "phase": "COMMIT", "done": True}
    yield payload


async def bounded_worker_event_stream(
    worker: Any,
    *,
    command: HostCommand,
    work_order: Any,
    execution_context: Mapping[str, Any],
    time_budget_ms: int | None,
) -> AsyncIterator[Mapping[str, Any]]:
    """Yield one worker stream while enforcing a durable remaining time budget.

    The caller owns the Operation reservation and must treat a timeout as an
    unknown-effect recovery boundary.  This helper only interrupts the local
    worker task; it does not decide whether an external effect happened.
    """

    if time_budget_ms is None:
        async for event in worker_event_stream(
            worker,
            command=command,
            work_order=work_order,
            execution_context=execution_context,
        ):
            yield event
        return
    if (
        isinstance(time_budget_ms, bool)
        or not isinstance(time_budget_ms, int)
        or time_budget_ms < 1
    ):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            "worker time budget must be a positive integer when configured",
        )
    timeout = asyncio.timeout(time_budget_ms / 1000.0)
    try:
        async with timeout:
            async for event in worker_event_stream(
                worker,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
            ):
                yield event
    except TimeoutError as exc:
        if not timeout.expired():
            raise
        raise GovernedRuntimeError(
            GOVERNED_TIME_BUDGET_EXCEEDED,
            f"worker exceeded its durable remaining time budget of {time_budget_ms}ms",
        ) from exc


def _worker_elapsed_ms(started_ns: int, *, minimum_ms: int = 0) -> int:
    """Return monotonic worker elapsed time without weakening a timeout charge."""

    return max(
        int(minimum_ms),
        max(0, (time.monotonic_ns() - int(started_ns) + 999_999) // 1_000_000),
    )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def stop_heartbeat_after_interruption(
    heartbeat: DurableExecutionHeartbeat,
) -> None:
    """Best-effort join without replacing the caller's original failure."""

    try:
        await heartbeat.stop()
    except BaseException:
        return


__all__ = [
    "_maybe_await",
    "_worker_elapsed_ms",
    "bounded_worker_event_stream",
    "stop_heartbeat_after_interruption",
    "worker_event_stream",
]
