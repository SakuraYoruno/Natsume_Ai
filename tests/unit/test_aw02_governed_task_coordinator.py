from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from host_brain.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.orchestration import GovernedCommandApplication
from host_brain.task_runtime import GovernedTaskCoordinator
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    TaskRuntimeMode,
    build_host_command,
)


class _NotFound(RuntimeError):
    reason_code = "TASK_STORE_NOT_FOUND"


class _OutboxFactory:
    @staticmethod
    def create(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


class _Publisher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def publish_pending(self, *, limit: int = 100) -> dict[str, Any]:
        self.calls.append(f"publish:{limit}")
        return {"status": "OK", "published_count": 1}


@dataclass
class _ReservationResult:
    acquired: bool
    reservation: dict[str, Any]


class _FakeTaskStore:
    def __init__(self, graph: TaskGraph, calls: list[str]) -> None:
        node = graph.nodes[0]
        self.calls = calls
        self.graph = {
            "graph_id": graph.graph_id,
            "revision": graph.revision,
            "root_work_order_id": graph.root_work_order_id,
            "status": graph.status.value,
            "status_version": 0,
            "graph": graph.to_dict(),
        }
        self.node = {
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": node.node_id,
            "status": node.status.value,
            "status_version": node.status_version,
            "node": node.to_dict(),
        }
        self.attempts: dict[str, dict[str, Any]] = {}
        self.reservation: dict[str, Any] | None = None

    def get_graph(self, graph_id: str, revision: int) -> dict[str, Any]:
        return dict(self.graph)

    def get_node(self, graph_id: str, revision: int, node_id: str) -> dict[str, Any]:
        return {**self.node, "node": dict(self.node["node"])}

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        return dict(self.attempts[attempt_id])

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any]:
        if self.reservation is None or self.reservation["reservation_id"] != reservation_id:
            raise _NotFound(reservation_id)
        return dict(self.reservation)

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        return {"attempts": [dict(item) for item in self.attempts.values()]}

    def transition_graph(self, graph_id: str, revision: int, *, expected_version: int, target_status: str, outbox: Any) -> dict[str, Any]:
        assert expected_version == self.graph["status_version"]
        self.calls.append(f"graph:{target_status}")
        self.graph["status"] = target_status
        self.graph["status_version"] += 1
        return dict(self.graph)

    def transition_node(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        *,
        expected_version: int,
        target_status: str,
        outbox: Any,
        verification_result: Any = None,
        acceptance_judgement: Any = None,
    ) -> dict[str, Any]:
        assert expected_version == self.node["status_version"]
        self.calls.append(f"node:{target_status}")
        self.node["status"] = target_status
        self.node["status_version"] += 1
        self.node["node"]["status"] = target_status
        self.node["node"]["status_version"] = self.node["status_version"]
        return self.get_node(graph_id, revision, node_id)

    def create_attempt(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("attempt:CREATED")
        attempt = {
            "attempt_id": kwargs["attempt_id"],
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "node_id": kwargs["node_id"],
            "attempt_no": kwargs["attempt_no"],
            "status": "CREATED",
            "status_version": 0,
        }
        self.attempts[attempt["attempt_id"]] = attempt
        return dict(attempt)

    def acquire_lease(self, attempt_id: str, *, owner_id: str, ttl_ms: int, expected_attempt_version: int, outbox: Any) -> dict[str, Any]:
        attempt = self.attempts[attempt_id]
        assert expected_attempt_version == attempt["status_version"]
        self.calls.append("attempt:LEASED")
        attempt["status"] = "LEASED"
        attempt["status_version"] += 1
        self.node["status"] = "LEASED"
        self.node["status_version"] += 1
        self.node["node"]["status"] = "LEASED"
        self.node["node"]["status_version"] = self.node["status_version"]
        return {"lease_id": "lease::one", "fencing_token": 1, "status_version": 0}

    def reserve_operation(self, **kwargs: Any) -> _ReservationResult:
        self.calls.append("operation:RESERVED")
        self.reservation = {
            "reservation_id": kwargs["reservation_id"],
            "attempt_id": kwargs["attempt_id"],
            "status": "RESERVED",
            "status_version": 0,
            "receipt_ref": None,
        }
        return _ReservationResult(True, dict(self.reservation))

    def transition_attempt(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        target_status: str,
        outbox: Any,
        error_ref: str | None = None,
    ) -> dict[str, Any]:
        attempt = self.attempts[attempt_id]
        assert expected_version == attempt["status_version"]
        self.calls.append(f"attempt:{target_status}")
        attempt["status"] = target_status
        attempt["status_version"] += 1
        return dict(attempt)

    def commit_operation(self, reservation_id: str, **kwargs: Any) -> dict[str, Any]:
        assert self.reservation is not None
        assert kwargs["expected_version"] == self.reservation["status_version"]
        self.calls.append("operation:COMMITTED")
        self.reservation["status"] = "COMMITTED"
        self.reservation["status_version"] += 1
        self.reservation["receipt_ref"] = kwargs["receipt_ref"]
        return dict(self.reservation)


class _Repository:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed

    def admit(self, command: Any, work_order: Any, assignment: Any, graph: Any) -> dict[str, Any]:
        return {
            "command": command,
            "work_order": work_order,
            "assignment": assignment,
            "graph": graph,
            "created": not self.replayed,
            "replayed": self.replayed,
        }


class _Worker:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    async def execute_stream(self, **kwargs: Any):
        self.calls.append(f"worker:{self.name}")
        yield {"phase": "RENDER", "chunk": self.name}
        yield {"phase": "COMMIT", "done": True, "decision": "APPROVE", "narrative": self.name}


class _Resolver:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.workers = {
            "canonical": _Worker("canonical", calls),
            "legacy": _Worker("legacy", calls),
        }

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        self.calls.append(f"resolve:{executor}:{worker_route}")
        return self.workers[executor]


class _Verifier:
    def __init__(self, status: str) -> None:
        self.status = status

    def verify(self, *, execution_context: dict[str, Any], evidence_refs: list[str] | tuple[str, ...], **kwargs: Any) -> dict[str, Any]:
        passed = self.status == "PASS"
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": "verify-governed",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": self.status,
            "reason_code": None if passed else "WORKER_EVIDENCE_REJECTED",
            "evidence_refs": list(evidence_refs),
            "checks": [],
            "started_at_ms": 1,
            "finished_at_ms": 2,
        }


class _Acceptance:
    def __init__(self, decision: str = "ACCEPT") -> None:
        self.decision = decision

    def judge(self, *, execution_context: dict[str, Any], evidence_refs: list[str] | tuple[str, ...], **kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": "judge-governed",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": self.decision,
            "reason_code": (
                None
                if self.decision == "ACCEPT"
                else "ACCEPTANCE_REVIEW_REQUIRED"
            ),
            "evidence_refs": list(evidence_refs),
            "criteria": [],
            "critic_candidate": None,
            "started_at_ms": 1,
            "finished_at_ms": 2,
        }


def _contracts(*, executor: TaskExecutor) -> tuple[Any, ...]:
    work_order = AgentWorkOrder(
        work_order_id="wo-governed-1",
        domain="research",
        run_mode="research",
        objective="execute one governed command",
        workspace={"strategy": "temp_workspace"},
    )
    graph = TaskGraph(
        graph_id="graph-governed-1",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://governed/one-node",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        nodes=(
            TaskNode(
                node_id="node-one",
                dependencies=(),
                route="executive_runtime",
                input_refs=(),
                output_contract={},
                retry_policy={"retry_limit": 0, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-governed-1",
        session_id="session-governed-1",
        principal_id="principal-governed-1",
        input_ref="command-input://request-governed-1",
        input_sha256="1" * 64,
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id="executive_runtime",
        business_scope="host-chat-command",
        operation_fingerprint="2" * 64,
        created_at_ms=1,
    )
    mode = TaskRuntimeMode.CANONICAL if executor is TaskExecutor.CANONICAL else TaskRuntimeMode.LEGACY
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=executor,
        mode=mode,
        assignment_id=f"runtime-assignment://{work_order.work_order_id}/{executor.value}",
        assigned_at_ms=1,
        canary_percent=100 if executor is TaskExecutor.CANONICAL else 0,
        canary_selected=executor is TaskExecutor.CANONICAL,
        kill_switch_active=False,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL" if executor is TaskExecutor.CANONICAL else "RUNTIME_NEW_TASK_LEGACY",
    )
    return command, work_order, assignment, graph


def _application(
    *,
    graph: TaskGraph,
    calls: list[str],
    verification_status: str,
    acceptance_decision: str = "ACCEPT",
) -> GovernedCommandApplication:
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier(verification_status),
        acceptance=_Acceptance(acceptance_decision),
        owner_id="worker-one",
    )
    return GovernedCommandApplication(command_repository=_Repository(), coordinator=coordinator)


@pytest.mark.asyncio
async def test_canonical_assignment_drives_full_durable_happy_path_and_single_commit() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(graph=graph, calls=calls, verification_status="PASS")

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert [event["phase"] for event in events] == ["RENDER", "COMMIT"]
    receipt = events[-1]["execution_receipt"]
    assert receipt["accepted"] is True
    assert receipt["executor"] == "canonical"
    assert calls.index("operation:COMMITTED") < calls.index("attempt:SUCCEEDED")
    assert calls.index("attempt:SUCCEEDED") < calls.index("node:EXECUTED")
    assert calls[-2:] == ["graph:COMPLETED", "publish:200"]
    assert "resolve:canonical:executive_runtime" in calls
    assert "worker:canonical" in calls


@pytest.mark.asyncio
async def test_legacy_assignment_is_selected_but_failed_verification_never_emits_commit() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.LEGACY)
    calls: list[str] = []
    app = _application(graph=graph, calls=calls, verification_status="FAIL")

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert [event["phase"] for event in events] == ["RENDER", "TERMINAL"]
    assert events[-1]["decision"] == "REJECT"
    assert events[-1]["execution_receipt"]["status"] == "VERIFICATION_FAILED"
    assert "resolve:legacy:executive_runtime" in calls
    assert "worker:legacy" in calls
    assert calls.index("operation:COMMITTED") < calls.index("node:VERIFICATION_FAILED")
    assert calls[-2:] == ["graph:FAILED", "publish:200"]


@pytest.mark.asyncio
async def test_acceptance_review_replays_committed_receipt_without_reexecution() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    app = _application(
        graph=graph,
        calls=calls,
        verification_status="PASS",
        acceptance_decision="REVIEW",
    )

    first = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]
    replay = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    first_receipt = first[-1]["execution_receipt"]
    replay_receipt = replay[-1]["execution_receipt"]
    assert [event["phase"] for event in replay] == ["TERMINAL"]
    assert first[-1]["decision"] == "REJECT"
    assert first_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert replay[-1]["decision"] == "REJECT"
    assert replay[-1]["replayed"] is True
    assert replay_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert replay_receipt["verification_status"] == "PASS"
    assert replay_receipt["accepted"] is False
    assert replay_receipt["receipt_ref"] == first_receipt["receipt_ref"]
    assert calls.count("resolve:canonical:executive_runtime") == 1
    assert calls.count("worker:canonical") == 1
    assert calls.count("operation:RESERVED") == 1
