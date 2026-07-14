"""Application service for atomic command admission and governed execution."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Mapping
from typing import Any

from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand

from host_brain.task_runtime.coordinator import (
    AdmittedExecution,
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_ASSIGNMENT_CONFLICT,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
)
from host_brain.task_runtime.ports import CommandRepositoryPort


class GovernedCommandApplication:
    """Bind command admission to the only allowed Task Runtime entrypoint."""

    def __init__(
        self,
        *,
        command_repository: CommandRepositoryPort,
        coordinator: GovernedTaskCoordinator,
    ) -> None:
        self._commands = command_repository
        self._coordinator = coordinator

    async def execute(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> dict[str, Any]:
        admitted, persisted_command, persisted_work_order = await self._admit(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
        return await self._coordinator.execute(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
        )

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        admitted, persisted_command, persisted_work_order = await self._admit(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
        async for event in self._coordinator.execute_stream(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
        ):
            yield event

    async def _admit(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> tuple[AdmittedExecution, HostCommand, Any]:
        if not isinstance(command, HostCommand):
            raise GovernedRuntimeError(GOVERNED_ADMISSION_INVALID, "command must be HostCommand")
        if not isinstance(assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "assignment must be ExecutorAssignment",
            )
        self._require_ready_one_node_graph(graph)
        record_raw = self._commands.admit(command, work_order, assignment, graph)
        if inspect.isawaitable(record_raw):
            record_raw = await record_raw
        if not isinstance(record_raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "command repository admission must return a durable record",
            )
        record = dict(record_raw)
        persisted_command = record.get("command", command)
        persisted_assignment = record.get("assignment", assignment)
        persisted_graph = record.get("graph", graph)
        persisted_work_order = record.get("work_order", work_order)
        if not isinstance(persisted_command, HostCommand):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "durable admission did not return a typed HostCommand",
            )
        if not isinstance(persisted_assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "durable admission did not return a typed ExecutorAssignment",
            )
        if persisted_command.to_dict() != command.to_dict():
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "durable HostCommand replay differs from the requested command",
            )
        if persisted_assignment.to_dict() != assignment.to_dict():
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "durable admission attempted to change the frozen executor assignment",
            )
        self._require_one_node_graph(
            persisted_graph,
            require_admission_state=not bool(record.get("replayed")),
        )
        admitted = AdmittedExecution.from_contracts(
            command=persisted_command,
            assignment=persisted_assignment,
            graph=persisted_graph,
            replayed=bool(record.get("replayed")),
        )
        return admitted, persisted_command, persisted_work_order

    @staticmethod
    def _require_ready_one_node_graph(graph: Any) -> None:
        GovernedCommandApplication._require_one_node_graph(
            graph,
            require_admission_state=True,
        )

    @staticmethod
    def _require_one_node_graph(graph: Any, *, require_admission_state: bool) -> None:
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        if len(nodes) != 1:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Host command admission requires exactly one TaskNode",
            )
        graph_status = str(getattr(getattr(graph, "status", None), "value", getattr(graph, "status", ""))).upper()
        node_status = str(
            getattr(getattr(nodes[0], "status", None), "value", getattr(nodes[0], "status", ""))
        ).upper()
        if require_admission_state and (graph_status != "CREATED" or node_status != "READY"):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"admission graph must be CREATED with READY node, got {graph_status}/{node_status}",
            )


__all__ = ["GovernedCommandApplication"]
