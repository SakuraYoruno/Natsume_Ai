from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from host_brain.orchestration.governed_command import GovernedCommandApplication
from host_brain.orchestration.host_route_contracts import build_direct_host_route_contracts
from natsume_contracts.task import HostCommandSurface
from natsume_contracts.task.v1 import ExecutorAssignment, TaskExecutor


class _Repository:
    def admit(self, command: Any, work_order: Any, assignment: Any, graph: Any) -> dict[str, Any]:
        return {
            "command": command,
            "work_order": work_order,
            "assignment": assignment,
            "graph": graph,
            "replayed": False,
        }


@dataclass(frozen=True)
class _Batch:
    parent_grant_ref: str
    task_graph_id: str
    graph_revision: int
    grant_refs: dict[str, str]


class _NodeGrants:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def derive(self, **kwargs: Any) -> _Batch:
        self.calls.append(dict(kwargs))
        graph = kwargs["task_graph"]
        return _Batch(
            parent_grant_ref=kwargs["parent_grant_ref"],
            task_graph_id=graph.graph_id,
            graph_revision=graph.revision,
            grant_refs={node.node_id: f"node-grant://{node.node_id}" for node in graph.nodes},
        )


class _Coordinator:
    def __init__(self) -> None:
        self.execution: dict[str, Any] | None = None

    async def execute_stream(self, **kwargs: Any):
        self.execution = dict(kwargs)
        yield {"phase": "COMMIT", "done": True}


def _contracts():
    contracts = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-gr05c",
        session_id="session-gr05c",
        principal_id="principal-gr05c",
        ts_ms=1,
        intent_draft={"route": "chat", "task": "answer"},
        governor_result={"decision": "APPROVE", "intent_id": "intent-gr05c"},
        sanitized_message="hello",
        person_id="person-gr05c",
    )
    assignment = ExecutorAssignment(
        work_order_id=contracts.work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://gr05c/canonical",
        assigned_at_ms=1,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return contracts, assignment


@pytest.mark.asyncio
async def test_application_derives_exact_node_grants_after_durable_admission() -> None:
    contracts, assignment = _contracts()
    coordinator = _Coordinator()
    grants = _NodeGrants()
    application = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,  # type: ignore[arg-type]
        node_grant_service=grants,
    )

    events = [
        event
        async for event in application.execute_stream(
            command=contracts.command,
            work_order=contracts.work_order,
            assignment=assignment,
            graph=contracts.graph,
        )
    ]

    assert events == [{"phase": "COMMIT", "done": True}]
    assert len(grants.calls) == 1
    assert grants.calls[0]["task_graph"] is contracts.graph
    assert grants.calls[0]["work_order"] is contracts.work_order
    assert coordinator.execution is not None
    assert coordinator.execution["node_grant_refs"] == {
        contracts.graph.nodes[0].node_id: f"node-grant://{contracts.graph.nodes[0].node_id}"
    }


@pytest.mark.asyncio
async def test_application_rejects_partial_node_grant_batch() -> None:
    contracts, assignment = _contracts()

    class _Partial(_NodeGrants):
        def derive(self, **kwargs: Any) -> _Batch:
            graph = kwargs["task_graph"]
            return _Batch(
                parent_grant_ref=kwargs["parent_grant_ref"],
                task_graph_id=graph.graph_id,
                graph_revision=graph.revision,
                grant_refs={},
            )

    application = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=_Coordinator(),  # type: ignore[arg-type]
        node_grant_service=_Partial(),
    )

    with pytest.raises(Exception, match="does not exactly cover"):
        async for _ in application.execute_stream(
            command=contracts.command,
            work_order=contracts.work_order,
            assignment=assignment,
            graph=contracts.graph,
        ):
            pass
