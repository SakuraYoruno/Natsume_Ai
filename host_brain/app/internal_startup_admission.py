"""Exact durable startup continuation for governed Internal admissions."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from host_brain.app.governed_control_route_policy import (
    require_governed_control_route_policy,
)
from host_brain.app.internal_task_contracts import (
    INTERNAL_RUNTIME_AUTHORITY_INVALID,
    GovernedInternalRuntimeError,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from natsume_contracts.task import HostCommand, HostCommandSurface


async def execute_internal_startup_admission(
    *,
    record: Mapping[str, Any],
    application: Any,
    accepted_effect_runtime: Any,
) -> dict[str, Any]:
    """Validate and execute one survivor-selected Internal admission."""

    durable = dict(record or {})
    command = durable.get("command")
    work_order = durable.get("work_order")
    assignment = durable.get("assignment")
    graph = durable.get("graph")
    if (
        not isinstance(command, HostCommand)
        or command.surface is not HostCommandSurface.INTERNAL
        or not isinstance(work_order, WorkOrderV3)
        or assignment is None
        or graph is None
    ):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "Operation recovery Internal admission is incomplete or misrouted",
        )
    require_governed_control_route_policy(
        command,
        work_order,
        graph,
        GovernedInternalRuntimeError,
        INTERNAL_RUNTIME_AUTHORITY_INVALID,
    )
    result = await collect_internal_continuation_result(
        application.execute_startup_continuation_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    )
    return await accepted_effect_runtime.dispatch(result, command=command)


async def collect_internal_continuation_result(
    operation: Any,
) -> dict[str, Any]:
    """Resolve an Internal continuation to its single durable terminal result."""

    current = await operation if inspect.isawaitable(operation) else operation
    if isinstance(current, Mapping):
        return dict(current)
    if not hasattr(current, "__aiter__"):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "Internal continuation returned no durable result",
        )
    terminal: dict[str, Any] | None = None
    async for raw_event in current:
        if not isinstance(raw_event, Mapping):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "Internal continuation emitted a non-mapping event",
            )
        event = dict(raw_event)
        if bool(event.get("done")):
            terminal = event
    if terminal is None:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "Internal continuation emitted no durable terminal result",
        )
    return terminal


__all__ = [
    "collect_internal_continuation_result",
    "execute_internal_startup_admission",
]
