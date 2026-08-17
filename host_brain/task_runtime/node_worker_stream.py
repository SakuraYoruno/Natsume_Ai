"""无授权决策的 worker 流规范化及中断清理。

该层只统一不同 worker 的流式与一次性接口，并施加本地时间上限；是否能重试、
是否已发生外部副作用仍由持有 Operation 预约的上层运行时判断。
"""

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
    """归一化 worker 的流式或一次性执行接口为事件异步迭代器。"""
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
    # 兼容一次性 worker：若未显式发终态 COMMIT，则仅在本地协议层补齐流结束标记。
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
    """在持久化剩余时间预算内产出 worker 流。

    调用方拥有 Operation 预约，必须把超时视为“外部效果未知”的恢复边界；本函数
    只中断本地协程，绝不据此断言远端副作用没有发生。
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
    """按单调时钟计算 worker 耗时，且不降低已记录的超时扣费。"""

    return max(
        int(minimum_ms),
        max(0, (time.monotonic_ns() - int(started_ns) + 999_999) // 1_000_000),
    )


async def _maybe_await(value: Any) -> Any:
    """兼容同步或异步适配器返回值，保持上层调用形式一致。"""
    return await value if inspect.isawaitable(value) else value


async def stop_heartbeat_after_interruption(
    heartbeat: DurableExecutionHeartbeat,
) -> None:
    """尽力等待心跳停止，但不以清理异常覆盖调用方原有的中断原因。"""

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
