from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from host_brain.orchestration.governed_command import GovernedCommandApplication
from host_brain.orchestration.host_route_contracts import build_direct_host_route_contracts
from natsume_contracts.task import HostCommandSurface
from natsume_contracts.task.v1 import ExecutorAssignment, TaskExecutor


class _Repository:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def admit(self, command: Any, work_order: Any, assignment: Any, graph: Any) -> dict[str, Any]:
        self.calls.append(
            {
                "command": command,
                "work_order": work_order,
                "assignment": assignment,
                "graph": graph,
            }
        )
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

    def validate_parent_binding(self, **_kwargs: Any) -> bool:
        return True

    def derive(self, **kwargs: Any) -> _Batch:
        self.calls.append(dict(kwargs))
        graph = kwargs["task_graph"]
        return _Batch(
            parent_grant_ref=kwargs["parent_grant_ref"],
            task_graph_id=graph.graph_id,
            graph_revision=graph.revision,
            grant_refs={node.node_id: f"node-grant://{node.node_id}" for node in graph.nodes},
        )

    def derive_review_continuation(self, **kwargs: Any) -> _Batch:
        self.calls.append(dict(kwargs))
        graph = kwargs["task_graph"]
        return _Batch(
            parent_grant_ref=kwargs["parent_grant_ref"],
            task_graph_id=graph.graph_id,
            graph_revision=graph.revision,
            grant_refs={
                node.node_id: f"review-node-grant://{node.node_id}"
                for node in graph.nodes
            },
        )


class _Coordinator:
    def __init__(self) -> None:
        self.execution: dict[str, Any] | None = None

    def supports_completion_policy(self, _value: str | None) -> bool:
        return True

    async def execute_stream(self, **kwargs: Any):
        self.execution = dict(kwargs)
        yield {"phase": "COMMIT", "done": True}

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self.execution = dict(kwargs)
        return {"phase": "COMMIT", "done": True}


def _contracts():
    contracts = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-gr05c",
        session_id="session-gr05c",
        principal_id="principal-gr05c",
        ts_ms=1,
        intent_draft={
            "route": "chat",
            "action": "exec_intent",
            "target": "host",
            "task": "answer",
        },
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


@pytest.mark.asyncio
async def test_application_rejects_parent_binding_before_durable_admission() -> None:
    contracts, assignment = _contracts()
    repository = _Repository()

    class _RejectingParent(_NodeGrants):
        def __init__(self) -> None:
            super().__init__()
            self.preflight_calls: list[dict[str, Any]] = []

        def validate_parent_binding(self, **kwargs: Any) -> bool:
            self.preflight_calls.append(dict(kwargs))
            return False

    grants = _RejectingParent()
    application = GovernedCommandApplication(
        command_repository=repository,
        coordinator=_Coordinator(),  # type: ignore[arg-type]
        node_grant_service=grants,
    )

    with pytest.raises(Exception, match="rejected the parent Governor grant"):
        async for _ in application.execute_stream(
            command=contracts.command,
            work_order=contracts.work_order,
            assignment=assignment,
            graph=contracts.graph,
        ):
            pass

    assert repository.calls == []
    assert grants.calls == []
    assert grants.preflight_calls == [
        {
            "parent_grant_ref": contracts.command.governor_grant_ref,
            "request_id": contracts.command.request_id,
            "session_id": contracts.command.session_id,
            "principal_id": contracts.command.principal_id,
        }
    ]


@pytest.mark.asyncio
async def test_application_review_continuation_uses_claim_fenced_renewal() -> None:
    contracts, assignment = _contracts()
    coordinator = _Coordinator()
    grants = _NodeGrants()
    application = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,  # type: ignore[arg-type]
        node_grant_service=grants,
    )
    node_id = contracts.graph.nodes[0].node_id
    claim = {
        "review_id": "task-review-gr05c",
        "review_status": "CONSUMED",
        "graph_id": contracts.graph.graph_id,
        "graph_revision": contracts.graph.revision,
        "node_id": node_id,
    }

    terminal = await application.execute_review_continuation(
        command=contracts.command,
        work_order=contracts.work_order,
        assignment=assignment,
        graph=contracts.graph,
        continuation_claim=claim,
    )

    assert terminal == {"phase": "COMMIT", "done": True}
    assert grants.calls == [{
        "parent_grant_ref": contracts.command.governor_grant_ref,
        "task_graph": contracts.graph,
        "work_order": contracts.work_order,
        "request_id": contracts.command.request_id,
        "session_id": contracts.command.session_id,
        "principal_id": contracts.command.principal_id,
        "continuation_claim": claim,
    }]
    assert coordinator.execution is not None
    assert coordinator.execution["node_grant_refs"] == {
        node_id: f"review-node-grant://{node_id}"
    }


@pytest.mark.asyncio
async def test_application_denied_review_reconciles_without_worker_grant() -> None:
    contracts, assignment = _contracts()
    coordinator = _Coordinator()
    grants = _NodeGrants()
    application = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,  # type: ignore[arg-type]
        node_grant_service=grants,
    )

    await application.execute_review_continuation(
        command=contracts.command,
        work_order=contracts.work_order,
        assignment=assignment,
        graph=contracts.graph,
        continuation_claim={
            "review_id": "task-review-gr05c-denied",
            "review_status": "REJECTED",
            "graph_id": contracts.graph.graph_id,
            "graph_revision": contracts.graph.revision,
            "node_id": contracts.graph.nodes[0].node_id,
        },
    )

    assert grants.calls == []
    assert coordinator.execution is not None
    assert "node_grant_refs" not in coordinator.execution
    assert coordinator.execution["denied_review_node_id"] == contracts.graph.nodes[0].node_id
