from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration import GovernedCommandApplication
from host_brain.task_runtime import (
    AdmittedExecution,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
)
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
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


@dataclass
class _BudgetResult:
    accepted: bool
    reservation: dict[str, Any]
    exceeded_dimensions: tuple[str, ...] = ()
    replayed: bool = False


class _FakeTaskStore:
    def __init__(
        self,
        graph: TaskGraph,
        calls: list[str],
        *,
        reservation_acquired: bool = True,
    ) -> None:
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
            "retry_not_before_ms": None,
            "node": node.to_dict(),
        }
        self.attempts: dict[str, dict[str, Any]] = {}
        self.reservation: dict[str, Any] | None = None
        self.budget: dict[str, Any] | None = None
        self.budget_usage: dict[str, _BudgetResult] = {}
        self.reservation_acquired = bool(reservation_acquired)

    def assert_budget_usage_ready(self) -> None:
        return None

    def assert_dag_scheduling_ready(self) -> None:
        return None

    def assert_parallel_settlement_decision_ready(self) -> None:
        return None

    def assert_graph_cancellation_ready(self) -> None:
        return None

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> None:
        del graph_id, revision
        return None

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

    def reconcile_operation_reservation_state(self, reservation_id: str) -> dict[str, Any]:
        return self.get_operation_reservation(reservation_id)

    def get_committed_operation_checkpoint(self, reservation_id: str) -> dict[str, Any]:
        reservation = self.get_operation_reservation(reservation_id)
        attempt = dict(self.attempts[reservation["attempt_id"]])
        receipt_ref = str(reservation["receipt_ref"])
        digest_ref = "sha256://execution/" + receipt_ref.rsplit("/", 1)[-1]
        return {
            "reservation": reservation,
            "attempt": attempt,
            "lease": {
                "lease_id": reservation["lease_id"],
                "attempt_id": reservation["attempt_id"],
                "owner_id": reservation["owner_id"],
                "fencing_token": reservation["fencing_token"],
            },
            "outbox": {
                "aggregate_type": "OPERATION_RESERVATION",
                "aggregate_id": reservation_id,
                "aggregate_version": reservation["status_version"],
                "event_type": "OPERATION_COMMITTED",
                "attempt_id": reservation["attempt_id"],
                "evidence_refs": [receipt_ref, digest_ref],
            },
            "checkpoint_digest_ref": digest_ref,
        }

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        return {
            "observed_ts_ms": 1,
            "graph_record": dict(self.graph),
            "attempts": [dict(item) for item in self.attempts.values()],
            "active_leases": [],
            "budgets": [dict(self.budget)] if self.budget else [],
        }

    def create_budget_reservation(self, reservation: Any, **kwargs: Any) -> dict[str, Any]:
        self.budget = {
            **reservation.to_dict(),
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "node_id": None,
            "attempt_id": None,
            "attempted_consumed": reservation.consumed.to_dict(),
            "exceeded_dimensions": [],
        }
        return dict(self.budget)

    def transition_budget(self, budget_id: str, *, expected_version: int, target_status: str, outbox: Any) -> dict[str, Any]:
        assert self.budget is not None and expected_version == self.budget["status_version"]
        self.budget["status"] = target_status
        self.budget["status_version"] += 1
        return dict(self.budget)

    def reconcile_budget(self, budget_id: str) -> dict[str, Any]:
        if self.budget is None:
            raise _NotFound(budget_id)
        remaining = {
            key: self.budget["reserved"][key] - self.budget["consumed"][key]
            for key in self.budget["reserved"]
        }
        return {**self.budget, "remaining": remaining, "balanced": True}

    def consume_budget_once(self, budget_id: str, **kwargs: Any) -> _BudgetResult:
        usage_id = kwargs["usage_id"]
        if usage_id in self.budget_usage:
            prior = self.budget_usage[usage_id]
            return _BudgetResult(prior.accepted, dict(self.budget or {}), replayed=True)
        assert self.budget is not None
        delta = kwargs["delta"].to_dict()
        attempted = {
            key: self.budget["consumed"][key] + delta[key]
            for key in self.budget["consumed"]
        }
        dimensions = tuple(
            key for key, value in attempted.items() if value > self.budget["reserved"][key]
        )
        accepted = not dimensions
        if accepted:
            self.budget["consumed"] = attempted
        else:
            self.budget["status"] = "EXCEEDED"
            self.budget["exceeded_dimensions"] = list(dimensions)
        self.budget["status_version"] += 1
        result = _BudgetResult(accepted, dict(self.budget), dimensions)
        self.budget_usage[usage_id] = result
        return result

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

    def commit_node_acceptance(
        self,
        graph_id: str,
        revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: Any,
        acceptance_judgement: Any,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Any,
        effect_intents: Any = (),
    ) -> dict[str, Any]:
        del outbox, acceptance_judgement, worker_output
        node = self.transition_node(
            graph_id,
            revision,
            node_id,
            expected_version=expected_version,
            target_status="ACCEPTED",
            outbox=None,
        )
        return {
            "node": node,
            "artifact": {
                "graph_id": graph_id,
                "graph_revision": revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "receipt_ref": receipt_ref,
            },
            "effect_intents": tuple(effect_intents),
            "replayed": False,
        }

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

    def claim_ready_node(self, **kwargs: Any) -> dict[str, Any]:
        assert self.budget is not None
        assert kwargs["budget_id"] == self.budget["budget_id"]
        assert kwargs["expected_attempt_count"] == len(self.attempts)
        self.calls.append("attempt:CREATED")
        attempt = {
            "attempt_id": kwargs["attempt_id"],
            "graph_id": kwargs["graph_id"],
            "graph_revision": kwargs["graph_revision"],
            "node_id": kwargs["node_id"],
            "attempt_no": len(self.attempts) + 1,
            "status": "LEASED",
            "status_version": 1,
        }
        self.attempts[attempt["attempt_id"]] = attempt
        self.calls.append("attempt:LEASED")
        self.node["status"] = "LEASED"
        self.node["status_version"] += 1
        self.node["retry_not_before_ms"] = None
        self.node["node"]["status"] = "LEASED"
        self.node["node"]["status_version"] = self.node["status_version"]
        return {
            "attempt": dict(attempt),
            "lease": {
                "lease_id": "lease::one",
                "attempt_id": attempt["attempt_id"],
                "fencing_token": 1,
                "status_version": 0,
            },
            "node": self.get_node(
                kwargs["graph_id"],
                kwargs["graph_revision"],
                kwargs["node_id"],
            ),
        }

    def promote_retry_due(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        assert self.node["status"] == "RETRY_PENDING"
        self.node["status"] = "READY"
        self.node["status_version"] += 1
        self.node["retry_not_before_ms"] = None
        self.node["node"]["status"] = "READY"
        self.node["node"]["status_version"] = self.node["status_version"]
        return self.get_node(args[0], args[1], args[2])

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
            "business_scope": kwargs["business_scope"],
            "operation_fingerprint": kwargs["operation_fingerprint"],
            "attempt_id": kwargs["attempt_id"],
            "lease_id": kwargs["lease_id"],
            "owner_id": kwargs["owner_id"],
            "fencing_token": kwargs["fencing_token"],
            "status": "RESERVED" if self.reservation_acquired else "RECOVERY_REQUIRED",
            "status_version": 0,
            "receipt_ref": None,
        }
        return _ReservationResult(self.reservation_acquired, dict(self.reservation))

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


class _TracingRepository(_Repository):
    def __init__(self, calls: list[str], *, replayed: bool = False) -> None:
        super().__init__(replayed=replayed)
        self.calls = calls

    def admit(self, command: Any, work_order: Any, assignment: Any, graph: Any) -> dict[str, Any]:
        self.calls.append("admit")
        return super().admit(command, work_order, assignment, graph)


class _IntentAdmissionVerifier:
    def __init__(
        self,
        calls: list[str],
        *,
        error: BaseException | None = None,
        prepared_set: bool = False,
    ) -> None:
        self.calls = calls
        self.error = error
        self.prepared_set = prepared_set

    def has_prepared_intent_receipt_set(self, **kwargs: Any) -> bool:
        del kwargs
        self.calls.append("intent-prepared-proof")
        return self.prepared_set

    def verify_intent_admission(self, **kwargs: Any) -> tuple[dict[str, str], ...]:
        self.calls.append("intent")
        if self.error is not None:
            raise self.error
        graph = kwargs["graph"]
        return tuple({"task_node_id": node.node_id} for node in graph.nodes)


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
    work_order = WorkOrderV3(
        work_order_id="wo-governed-1",
        domain="research",
        run_mode="research",
        objective="execute one governed command",
        workspace={"strategy": "temp_workspace"},
        root_work_order_id="wo-governed-1",
        task_graph_id="graph-governed-1",
        task_node_id="node-one",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="work-order:wo-governed-1",
            limits=BudgetAmount(
                token_count=10_000,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=3,
                model_cost_micros=1_000_000,
            ),
        ),
    )
    graph = TaskGraph(
        graph_id="graph-governed-1",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://governed/one-node",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        result_node_id="node-one",
        nodes=(
            TaskNode(
                node_id="node-one",
                dependencies=(),
                route="executive_runtime",
                input_refs=(),
                output_contract={"type": "object"},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
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
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=executor,
        assignment_id=f"runtime-assignment://{work_order.work_order_id}/{executor.value}",
        assigned_at_ms=1,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return command, work_order, assignment, graph


def _application(
    *,
    graph: TaskGraph,
    calls: list[str],
    verification_status: str,
    acceptance_decision: str = "ACCEPT",
    reservation_acquired: bool = True,
) -> GovernedCommandApplication:
    store = _FakeTaskStore(
        graph,
        calls,
        reservation_acquired=reservation_acquired,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier(verification_status),
        acceptance=_Acceptance(acceptance_decision),
        owner_id="worker-one",
    )
    return GovernedCommandApplication(command_repository=_Repository(), coordinator=coordinator)


def test_admitted_execution_preserves_result_and_node_risk_authority() -> None:
    command, _, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)

    admitted = AdmittedExecution.from_contracts(
        command=command,
        assignment=assignment,
        graph=graph,
    )

    assert admitted.result_node_id == "node-one"
    assert admitted.for_node("node-one").result_node_id == "node-one"
    assert admitted.active_node.risk_level == 1


@pytest.mark.asyncio
async def test_intent_admission_runs_after_durable_admit_and_before_worker() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_TracingRepository(calls),
        coordinator=coordinator,
        intent_admission_verifier=_IntentAdmissionVerifier(calls),
    )

    _ = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert calls[:2] == ["admit", "intent"]
    assert calls.index("intent") < next(
        index for index, value in enumerate(calls) if value.startswith("worker:")
    )


@pytest.mark.asyncio
async def test_intent_block_fails_closed_before_worker_or_budget_mutation() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )

    class _Blocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "canonical node is blocked"

    app = GovernedCommandApplication(
        command_repository=_TracingRepository(calls),
        coordinator=coordinator,
        intent_admission_verifier=_IntentAdmissionVerifier(
            calls,
            error=_Blocked(),
        ),
    )

    with pytest.raises(GovernedRuntimeError) as blocked:
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert blocked.value.reason_code == "INTENT_ADMISSION_BLOCKED"
    assert calls == ["admit", "intent"]
    assert store.budget is None


@pytest.mark.asyncio
async def test_exact_intent_receipt_replay_skips_expired_parent_preflight() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    grant_ref = "governor://request-governed-1/decision/frozen"
    command = replace(command, governor_grant_ref=grant_ref)
    work_order = replace(work_order, approval_ref=grant_ref)
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )

    class _ExactReplayRepository(_TracingRepository):
        def get(self, command_id: str) -> dict[str, Any] | None:
            assert command_id == command.command_id
            return {
                "command": command,
                "work_order": work_order,
                "assignment": assignment,
                "graph": graph,
            }

    class _ExpiredGrantService:
        def validate_parent_binding(self, **kwargs: Any) -> None:
            del kwargs
            calls.append("parent-preflight")
            raise RuntimeError("expired parent grant")

    class _ReceiptBlocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "replayed durable receipt remains blocked"

    app = GovernedCommandApplication(
        command_repository=_ExactReplayRepository(calls, replayed=True),
        coordinator=coordinator,
        node_grant_service=_ExpiredGrantService(),
        intent_admission_verifier=_IntentAdmissionVerifier(
            calls,
            error=_ReceiptBlocked(),
            prepared_set=True,
        ),
    )

    with pytest.raises(GovernedRuntimeError) as blocked:
        await app.execute(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )

    assert blocked.value.reason_code == "INTENT_ADMISSION_BLOCKED"
    assert calls == ["intent-prepared-proof", "admit", "intent"]


@pytest.mark.asyncio
async def test_matching_command_without_prepared_intent_set_cannot_skip_preflight() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    grant_ref = "governor://request-governed-1/decision/frozen"
    command = replace(command, governor_grant_ref=grant_ref)
    work_order = replace(work_order, approval_ref=grant_ref)
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )

    class _ExactReplayRepository(_TracingRepository):
        def get(self, command_id: str) -> dict[str, Any] | None:
            assert command_id == command.command_id
            return {
                "command": command,
                "work_order": work_order,
                "assignment": assignment,
                "graph": graph,
            }

    class _GrantService:
        def validate_parent_binding(self, **kwargs: Any) -> None:
            del kwargs
            calls.append("parent-preflight")

    class _ReceiptBlocked(RuntimeError):
        reason_code = "INTENT_ADMISSION_BLOCKED"
        detail = "prepared set is intentionally absent"

    app = GovernedCommandApplication(
        command_repository=_ExactReplayRepository(calls, replayed=True),
        coordinator=coordinator,
        node_grant_service=_GrantService(),
        intent_admission_verifier=_IntentAdmissionVerifier(
            calls,
            error=_ReceiptBlocked(),
            prepared_set=False,
        ),
    )

    with pytest.raises(GovernedRuntimeError):
        await app.execute(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )

    assert calls == [
        "intent-prepared-proof",
        "parent-preflight",
        "admit",
        "intent",
    ]


@pytest.mark.asyncio
async def test_coordinator_durable_identity_rejects_result_authority_drift() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )
    store.graph = {**store.graph, "graph": dict(store.graph["graph"])}
    store.graph["graph"]["result_node_id"] = None

    with pytest.raises(GovernedRuntimeError) as error:
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert error.value.reason_code == "GOVERNED_DURABLE_STATE_CONFLICT"
    assert calls == []


@pytest.mark.asyncio
async def test_dag_schema_is_checked_before_budget_mutation() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    def _reject_stale_schema() -> None:
        calls.append("schema:rejected")
        raise RuntimeError("migration 0089 missing")

    store.assert_dag_scheduling_ready = _reject_stale_schema  # type: ignore[method-assign]
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="migration 0089 missing"):
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert calls == ["schema:rejected"]
    assert store.budget is None


@pytest.mark.asyncio
async def test_parallel_decision_schema_is_checked_before_budget_or_worker() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    def _reject_stale_schema() -> None:
        calls.append("parallel-schema:rejected")
        raise RuntimeError("migration 0090 missing")

    store.assert_parallel_settlement_decision_ready = _reject_stale_schema  # type: ignore[method-assign]
    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_Resolver(calls),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="migration 0090 missing"):
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert calls == ["parallel-schema:rejected"]
    assert store.budget is None


@pytest.mark.asyncio
async def test_resolver_cancellation_is_not_converted_to_terminal_failure() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _CancelledResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            raise asyncio.CancelledError

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_CancelledResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    with pytest.raises(asyncio.CancelledError):
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:FAILED" not in calls
    assert "operation:RESERVED" not in calls


@pytest.mark.asyncio
async def test_worker_self_cancellation_enters_durable_recovery_without_graph_intent() -> None:
    command, work_order, assignment, graph = _contracts(
        executor=TaskExecutor.CANONICAL
    )
    calls: list[str] = []
    store = _FakeTaskStore(graph, calls)

    class _SelfCancellingWorker:
        @staticmethod
        async def execute_stream(**kwargs: Any):
            del kwargs
            raise asyncio.CancelledError
            if False:
                yield {}

    class _SelfCancellingResolver:
        @staticmethod
        def resolve(*, executor: str, worker_route: str) -> Any:
            del executor, worker_route
            return _SelfCancellingWorker()

    coordinator = GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=object(),
        outbox_publisher=_Publisher(calls),
        outbox_factory=_OutboxFactory(),
        worker_resolver=_SelfCancellingResolver(),
        verifier=_Verifier("PASS"),
        acceptance=_Acceptance("ACCEPT"),
        owner_id="worker-one",
    )
    app = GovernedCommandApplication(
        command_repository=_Repository(),
        coordinator=coordinator,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert (
        receipt["projection"]["worker_failure_reason_code"]
        == "GOVERNED_WORKER_SELF_CANCELLED"
    )
    assert store.reservation is not None
    assert store.reservation["status"] == "RESERVED"
    assert list(store.attempts.values())[-1]["status"] == "RUNNING"
    assert store.get_graph_cancellation_for_graph(graph.graph_id, graph.revision) is None
    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:FAILED" not in calls


@pytest.mark.asyncio
async def test_operation_collision_preserves_authority_for_reconciliation() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(
        graph=graph,
        calls=calls,
        verification_status="PASS",
        reservation_acquired=False,
    )

    events = [
        event
        async for event in app.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert [event["phase"] for event in events] == ["TERMINAL"]
    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert receipt["accepted"] is False
    assert "worker:canonical" not in calls
    assert "attempt:FAILED" not in calls
    assert "node:FAILED" not in calls
    assert "graph:BLOCKED" not in calls
    assert "budget:RELEASED" not in calls
    assert calls[-1] == "publish:200"


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
async def test_missing_budget_policy_fails_before_worker_or_task_mutation() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
    calls: list[str] = []
    app = _application(graph=graph, calls=calls, verification_status="PASS")

    with pytest.raises(GovernedRuntimeError) as error:
        _ = [
            event
            async for event in app.execute_stream(
                command=command,
                work_order=replace(work_order, budget_policy=None),
                assignment=assignment,
                graph=graph,
            )
        ]

    assert error.value.reason_code == "GOVERNED_BUDGET_POLICY_REQUIRED"
    assert calls == []


@pytest.mark.asyncio
async def test_failed_verification_never_emits_commit() -> None:
    command, work_order, assignment, graph = _contracts(executor=TaskExecutor.CANONICAL)
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
    assert "resolve:canonical:executive_runtime" in calls
    assert "worker:canonical" in calls
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
