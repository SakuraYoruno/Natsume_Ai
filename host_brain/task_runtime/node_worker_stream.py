"""Authority-free worker stream normalization and interruption cleanup."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .coordinator_records import _mapping
from .errors import GOVERNED_WORKER_FAILED, GovernedRuntimeError
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
    "stop_heartbeat_after_interruption",
    "worker_event_stream",
]
