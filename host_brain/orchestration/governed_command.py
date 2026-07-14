"""Application service for atomic command admission and governed execution."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Mapping
from typing import Any

from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand

from host_brain.contracts.agent_runtime.task_graph import (
    validate_task_graph_admission_state,
)
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
        node_grant_service: Any | None = None,
    ) -> None:
        self._commands = command_repository
        self._coordinator = coordinator
        self._node_grants = node_grant_service

    async def execute(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> dict[str, Any]:
        admitted, persisted_command, persisted_work_order, persisted_graph = await self._admit(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
        if self._node_grants is None:
            return await self._coordinator.execute(
                admitted=admitted,
                command=persisted_command,
                work_order=persisted_work_order,
            )
        node_grant_refs = await self._derive_node_grant_refs(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            graph=persisted_graph,
        )
        return await self._coordinator.execute(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            node_grant_refs=node_grant_refs,
        )

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        admitted, persisted_command, persisted_work_order, persisted_graph = await self._admit(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
        execution_kwargs: dict[str, Any] = {
            "admitted": admitted,
            "command": persisted_command,
            "work_order": persisted_work_order,
        }
        if self._node_grants is not None:
            execution_kwargs["node_grant_refs"] = await self._derive_node_grant_refs(
                admitted=admitted,
                command=persisted_command,
                work_order=persisted_work_order,
                graph=persisted_graph,
            )
        async for event in self._coordinator.execute_stream(**execution_kwargs):
            yield event

    async def _admit(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> tuple[AdmittedExecution, HostCommand, Any, Any]:
        if not isinstance(command, HostCommand):
            raise GovernedRuntimeError(GOVERNED_ADMISSION_INVALID, "command must be HostCommand")
        if not isinstance(assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "assignment must be ExecutorAssignment",
            )
        self._require_ready_graph(graph)
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
        self._require_graph(
            persisted_graph,
            require_admission_state=not bool(record.get("replayed")),
        )
        admitted = AdmittedExecution.from_contracts(
            command=persisted_command,
            assignment=persisted_assignment,
            graph=persisted_graph,
            replayed=bool(record.get("replayed")),
        )
        return admitted, persisted_command, persisted_work_order, persisted_graph

    async def _derive_node_grant_refs(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        graph: Any,
    ) -> dict[str, str]:
        parent_ref = str(command.governor_grant_ref or "").strip()
        if not parent_ref:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "governed TaskGraph has no parent Governor grant",
            )
        derived = self._node_grants.derive(
            parent_grant_ref=parent_ref,
            task_graph=graph,
            work_order=work_order,
            request_id=command.request_id,
            session_id=command.session_id,
            principal_id=command.principal_id,
        )
        if inspect.isawaitable(derived):
            derived = await derived
        raw_refs = getattr(derived, "grant_refs", None)
        refs = dict(raw_refs) if isinstance(raw_refs, Mapping) else {}
        expected_nodes = {binding.node_id for binding in admitted.nodes}
        if (
            set(refs) != expected_nodes
            or any(not str(value or "").strip() for value in refs.values())
            or str(getattr(derived, "parent_grant_ref", "") or "") != parent_ref
            or str(getattr(derived, "task_graph_id", "") or "") != admitted.graph_id
            or int(getattr(derived, "graph_revision", 0) or 0) != admitted.graph_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "node grant batch does not exactly cover the admitted TaskGraph",
            )
        return {str(node_id): str(ref) for node_id, ref in refs.items()}

    @staticmethod
    def _require_ready_graph(graph: Any) -> None:
        GovernedCommandApplication._require_graph(
            graph,
            require_admission_state=True,
        )

    @staticmethod
    def _require_graph(graph: Any, *, require_admission_state: bool) -> None:
        validator = getattr(graph, "validate", None)
        if not callable(validator):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Host command admission requires a validated TaskGraph",
            )
        try:
            validator()
        except Exception as exc:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"TaskGraph dependency topology is invalid: {exc}",
            ) from exc
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        if not nodes:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Host command admission requires at least one TaskNode",
            )
        if require_admission_state:
            try:
                validate_task_graph_admission_state(graph)
            except Exception as exc:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    f"TaskGraph admission state is invalid: {exc}",
                ) from exc


__all__ = ["GovernedCommandApplication"]
