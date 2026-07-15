from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.review_policy import ReviewResumePhase
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_INVALID,
    HostCommandRepository,
    HostCommandRepositoryError,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import (
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_SCHEDULER_CAPACITY,
    DurableTaskStore,
    OutboxMessage,
    TaskStoreError,
)
from host_brain.task_runtime import (
    AdmittedExecution,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
    RetryableWorkerPreparationError,
)
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_900_000_000_000


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "gr03a.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr03a-migration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _contracts(
    specs: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    review_required: bool = False,
) -> tuple[Any, WorkOrderV3, ExecutorAssignment, TaskGraph]:
    work_order = WorkOrderV3(
        work_order_id="work-order-gr03a",
        domain="code",
        run_mode="inspect",
        objective="execute a durable dependency graph",
        acceptance_criteria=(),
        root_work_order_id="work-order-gr03a",
        task_graph_id="graph-gr03a",
        task_node_id=specs[0][0],
        graph_revision=1,
        approval_policy={"required": review_required},
        review_policy={
            "review_required": review_required,
            "durable_review_required": review_required,
        },
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-gr03a",
            limits=BudgetAmount(
                token_count=100_000,
                tool_call_count=100,
                time_ms=600_000,
                concurrency=1,
                retry_count=10,
                model_cost_micros=10_000_000,
            ),
        ),
    )
    graph = TaskGraph(
        graph_id="graph-gr03a",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://gr03a/dag",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=tuple(
            TaskNode(
                node_id=node_id,
                dependencies=dependencies,
                route=f"worker:{node_id}",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=(
                    TaskNodeStatus.BLOCKED
                    if dependencies
                    else TaskNodeStatus.READY
                ),
                status_version=0,
            )
            for node_id, dependencies in specs
        ),
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-gr03a",
        session_id="session-gr03a",
        principal_id="principal-gr03a",
        input_ref="host-input://gr03a",
        input_sha256=_sha("gr03a-input"),
        root_work_order_id=work_order.work_order_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        route_type="chat",
        target_id=specs[0][0],
        business_scope="host-command-gr03a",
        operation_fingerprint=_sha("gr03a-operation"),
        created_at_ms=NOW,
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-gr03a/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return command, work_order, assignment, graph


class _Publisher:
    def publish_pending(self, *, limit: int = 100) -> dict[str, Any]:
        return {"status": "OK", "limit": limit}


class _Worker:
    def __init__(self, calls: list[str], fail_nodes: set[str]) -> None:
        self._calls = calls
        self._fail_nodes = fail_nodes

    async def execute_stream(self, *, execution_context: dict[str, Any], **_: Any):
        node_id = execution_context["node_id"]
        self._calls.append(node_id)
        if node_id in self._fail_nodes:
            raise RuntimeError(f"worker failed: {node_id}")
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "node_id": node_id,
            "budget_usage": {
                "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
                "usage_id": f"worker-usage://{node_id}/stable",
                "measurements": {
                    "token_count": 10,
                    "retry_count": 0,
                    "model_cost_micros": 25,
                },
                "evidence_refs": [f"model-budget://{node_id}/stable"],
            },
        }


class _Resolver:
    def __init__(self, calls: list[str], fail_nodes: set[str] | None = None) -> None:
        self._worker = _Worker(calls, fail_nodes or set())

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        assert worker_route.startswith("worker:")
        return self._worker


class _RetryOnceResolver:
    def __init__(self, calls: list[str]) -> None:
        self._worker = _Worker(calls, set())
        self.resolve_count = 0

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        assert worker_route.startswith("worker:")
        self.resolve_count += 1
        if self.resolve_count == 1:
            raise RetryableWorkerPreparationError(
                "WORKER_PREPARATION_TRANSIENT",
                "worker registry is warming before operation reservation",
            )
        return self._worker


class _Verifier:
    def verify(
        self,
        *,
        execution_context: dict[str, Any],
        evidence_refs: tuple[str, ...],
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": f"verify-{execution_context['node_id']}",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": "PASS",
            "reason_code": None,
            "evidence_refs": list(evidence_refs),
            "checks": [],
            "started_at_ms": NOW,
            "finished_at_ms": NOW + 1,
        }


class _Acceptance:
    def __init__(self, decision: str = "ACCEPT") -> None:
        self._decision = decision

    def judge(
        self,
        *,
        execution_context: dict[str, Any],
        evidence_refs: tuple[str, ...],
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": f"accept-{execution_context['node_id']}",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": self._decision,
            "reason_code": (
                None if self._decision == "ACCEPT" else "ACCEPTANCE_REVIEW_REQUIRED"
            ),
            "evidence_refs": list(evidence_refs),
            "criteria": [],
            "critic_candidate": None,
            "started_at_ms": NOW,
            "finished_at_ms": NOW + 1,
        }


def _coordinator(
    db_path: Path,
    calls: list[str],
    *,
    fail_nodes: set[str] | None = None,
    review_runtime: TaskNodeReviewRuntime | None = None,
    acceptance_decision: str = "ACCEPT",
    clock: Any = None,
    worker_resolver: Any = None,
) -> GovernedTaskCoordinator:
    resolved_clock = clock if clock is not None else (lambda: NOW)
    return GovernedTaskCoordinator(
        task_store=DurableTaskStore(db_path, clock=resolved_clock),
        outbox_publisher=_Publisher(),
        outbox_factory=OutboxMessage,
        worker_resolver=(
            worker_resolver
            if worker_resolver is not None
            else _Resolver(calls, fail_nodes)
        ),
        verifier=_Verifier(),
        acceptance=_Acceptance(acceptance_decision),
        review_runtime=review_runtime,
        owner_id="gr03a-worker",
    )


def _runtime_outbox(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    discriminator = hashlib.sha256(
        f"{aggregate_type}\n{aggregate_id}\n{aggregate_version}\n{event_type}".encode()
    ).hexdigest()
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=(
            f"taskstore://event/{aggregate_type}/{aggregate_id}/{aggregate_version}"
        ),
        idempotency_key=f"gr03a-runtime:{discriminator}",
        outbox_id=f"outbox::gr03a-runtime::{discriminator}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _budget_usage_outbox(
    budget_id: str, version: int, attempt_id: str, discriminator: str
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type="BUDGET_RESERVATION",
        aggregate_id=budget_id,
        aggregate_version=version,
        event_type="BUDGET_CONSUMED",
        payload_ref=f"taskstore://event/BUDGET_RESERVATION/{budget_id}/{version}",
        idempotency_key=f"gr03b:{discriminator}",
        attempt_id=attempt_id,
        outbox_id=f"outbox::gr03b::{discriminator}",
    )


def _consume_review(store: ReviewStore, review: dict[str, Any]) -> None:
    approved = store.approve_review(
        review["review_id"],
        actor_id="gr03-reviewer",
        expected_subject_ref=review["subject_ref"],
        expected_subject_version=review["subject_version"],
        expected_resume_phase=review["resume_phase"],
        expected_checkpoint_id=review["checkpoint_id"],
    )
    store.resume_review(
        review["review_id"],
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=review["subject_ref"],
        subject_version=review["subject_version"],
        resume_phase=review["resume_phase"],
        checkpoint_id=review["checkpoint_id"],
    )


@pytest.mark.asyncio
async def test_gr03_pre_execution_review_resumes_without_early_worker_effect(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),), review_required=True)
    calls: list[str] = []
    review_store = ReviewStore(db_path, clock=lambda: NOW)
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            review_runtime=TaskNodeReviewRuntime(review_store, clock=lambda: NOW),
        ),
    )

    first = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    first_receipt = first[-1]["execution_receipt"]
    assert first_receipt["status"] == "WAITING_APPROVAL"
    assert calls == []
    review = first_receipt["projection"]["review"]
    assert review["resume_phase"] == ReviewResumePhase.PRE_EXECUTION.value
    _consume_review(review_store, review)

    resumed = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert calls == ["root"]


@pytest.mark.asyncio
async def test_gr03_post_acceptance_review_resumes_without_worker_reexecution(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    calls: list[str] = []
    review_store = ReviewStore(db_path, clock=lambda: NOW)
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            review_runtime=TaskNodeReviewRuntime(review_store, clock=lambda: NOW),
            acceptance_decision="REVIEW",
        ),
    )

    first = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    first_receipt = first[-1]["execution_receipt"]
    assert first_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert calls == ["root"]
    review = first_receipt["projection"]["review"]
    assert review["resume_phase"] == ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value
    _consume_review(review_store, review)

    resumed = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert resumed[-1]["replayed"] is True
    assert calls == ["root"]


@pytest.mark.asyncio
async def test_gr03a_fan_out_fan_in_persists_and_executes_dependency_first(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (
            ("root", ()),
            ("left", ("root",)),
            ("right", ("root",)),
            ("join", ("left", "right")),
        )
    )
    calls: list[str] = []
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(db_path, calls),
    )

    events = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]

    assert calls == ["root", "left", "right", "join"]
    assert len([event for event in events if event.get("done")]) == 1
    assert events[-1]["execution_receipt"]["node_id"] == "join"
    assert events[-1]["execution_receipt"]["accepted"] is True
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    graph_record = store.get_graph("graph-gr03a", 1)
    assert graph_record["status"] == "COMPLETED"
    assert {node["node_id"]: node["status"] for node in graph_record["nodes"]} == {
        "join": "ACCEPTED",
        "left": "ACCEPTED",
        "right": "ACCEPTED",
        "root": "ACCEPTED",
    }
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(snapshot["attempts"]) == 4
    assert len(snapshot["budgets"]) == 1
    assert snapshot["budgets"][0]["status"] == "CONSUMED"
    assert snapshot["budgets"][0]["consumed"]["token_count"] == 40
    assert snapshot["budgets"][0]["consumed"]["tool_call_count"] == 4
    assert snapshot["budgets"][0]["consumed"]["retry_count"] == 0
    assert snapshot["budgets"][0]["consumed"]["model_cost_micros"] == 100
    assert snapshot["budgets"][0]["consumed"]["time_ms"] >= 0
    assert len(snapshot["budget_usage"]) == 8
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_node").fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox WHERE event_type = 'TASK_NODE_READY'"
        ).fetchone()[0] == 3


@pytest.mark.asyncio
async def test_gr03a_failed_dependency_never_executes_descendant(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root", ()), ("failed", ("root",)), ("descendant", ("failed",)))
    )
    calls: list[str] = []
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(db_path, calls, fail_nodes={"failed"}),
    )

    events = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]

    assert calls == ["root", "failed"]
    assert events[-1]["decision"] == "REJECT"
    graph_record = DurableTaskStore(db_path, clock=lambda: NOW).get_graph("graph-gr03a", 1)
    assert graph_record["status"] == "FAILED"
    assert {node["node_id"]: node["status"] for node in graph_record["nodes"]}[
        "descendant"
    ] == "BLOCKED"
    failed_snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        "graph-gr03a", 1
    )
    assert failed_snapshot["budgets"][0]["status"] == "RELEASED"


@pytest.mark.asyncio
async def test_gr03b_precharge_exceeded_blocks_before_worker_and_releases_budget(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    limited = replace(
        work_order,
        budget_policy=BudgetPolicy(
            scope=work_order.budget_policy.scope,
            limits=BudgetAmount(
                token_count=100,
                tool_call_count=0,
                time_ms=10_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=1_000,
            ),
        ),
    )
    calls: list[str] = []
    events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=_coordinator(db_path, calls),
        ).execute_stream(
            command=command,
            work_order=limited,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert calls == []
    assert events[-1]["execution_receipt"]["accepted"] is False
    assert events[-1]["execution_receipt"]["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        graph.graph_id, graph.revision
    )
    assert snapshot["graph_record"]["status"] == "BLOCKED"
    assert snapshot["graph_record"]["nodes"][0]["status"] == "FAILED"
    assert snapshot["budgets"][0]["status"] == "RELEASED"
    assert len(snapshot["budget_usage"]) == 1
    assert snapshot["budget_usage"][0]["accepted"] is False
    assert snapshot["budget_usage"][0]["exceeded_dimensions"] == ["tool_call_count"]


@pytest.mark.asyncio
async def test_gr03b_typed_worker_usage_exceeded_blocks_acceptance_and_is_durable(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    limited = replace(
        work_order,
        budget_policy=BudgetPolicy(
            scope=work_order.budget_policy.scope,
            limits=BudgetAmount(
                token_count=5,
                tool_call_count=1,
                time_ms=10_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=1_000,
            ),
        ),
    )
    calls: list[str] = []

    events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=_coordinator(db_path, calls),
        ).execute_stream(
            command=command,
            work_order=limited,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert calls == ["root"]
    assert events[-1]["execution_receipt"]["accepted"] is False
    assert events[-1]["execution_receipt"]["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        graph.graph_id, graph.revision
    )
    assert snapshot["graph_record"]["status"] == "BLOCKED"
    assert snapshot["budgets"][0]["status"] == "RELEASED"
    worker_usage = [
        row for row in snapshot["budget_usage"] if row["charge_kind"].startswith("WORKER_USAGE:")
    ]
    assert len(worker_usage) == 1
    assert worker_usage[0]["accepted"] is False
    assert worker_usage[0]["exceeded_dimensions"] == ["token_count"]


@pytest.mark.asyncio
async def test_gr03a_restart_resumes_after_accepted_predecessor_without_reexecution(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("root", ()), ("middle", ("root",)), ("final", ("middle",)))
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    record = repository.admit(command, work_order, assignment, graph)
    assert isinstance(record["work_order"], WorkOrderV3)
    calls: list[str] = []
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    first_runtime = _coordinator(db_path, calls)

    first_events = [
        event
        async for event in first_runtime._execute_node_stream(  # noqa: SLF001
            admitted=admitted.for_node("root"),
            command=record["command"],
            work_order=record["work_order"],
        )
    ]
    assert first_events[-1]["execution_receipt"]["accepted"] is True
    first_store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert first_store.get_graph("graph-gr03a", 1)[
        "status"
    ] == "ACTIVE"
    first_usage_ids = {
        row["usage_id"]
        for row in first_store.read_scheduler_snapshot("graph-gr03a", 1)["budget_usage"]
    }
    assert len(first_usage_ids) == 2

    replay = HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command, work_order, assignment, graph
    )
    resumed = AdmittedExecution.from_contracts(
        command=replay["command"],
        assignment=replay["assignment"],
        graph=replay["graph"],
        replayed=True,
    )
    resumed_events = [
        event
        async for event in _coordinator(db_path, calls).execute_stream(
            admitted=resumed,
            command=replay["command"],
            work_order=replay["work_order"],
        )
    ]

    assert calls == ["root", "middle", "final"]
    assert resumed_events[-1]["execution_receipt"]["node_id"] == "final"
    assert DurableTaskStore(db_path, clock=lambda: NOW).get_graph("graph-gr03a", 1)[
        "status"
    ] == "COMPLETED"
    final_snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        "graph-gr03a", 1
    )
    assert len(final_snapshot["budgets"]) == 1
    assert final_snapshot["budgets"][0]["status"] == "CONSUMED"
    assert len({row["usage_id"] for row in final_snapshot["budget_usage"]}) == 6


@pytest.mark.asyncio
async def test_ne01_b1_retry_deadline_and_attempt_sequence_survive_reopen(
    tmp_path: Path,
) -> None:
    class _Clock:
        def __init__(self, value: int) -> None:
            self.value = value

        def __call__(self) -> int:
            return self.value

    clock = _Clock(NOW)
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    graph = replace(
        graph,
        nodes=(
            replace(
                graph.nodes[0],
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 100},
            ),
        ),
    )
    repository = HostCommandRepository(db_path, clock=clock)
    record = repository.admit(command, work_order, assignment, graph)
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    calls: list[str] = []
    first_runtime = _coordinator(db_path, calls, clock=clock)
    budget = await first_runtime._budgets.ensure_active(  # noqa: SLF001
        admitted=admitted,
        work_order=record["work_order"],
    )
    store = DurableTaskStore(db_path, clock=clock)
    graph_record = store.get_graph(graph.graph_id, graph.revision)
    store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=graph_record["status_version"],
        target_status="ACTIVE",
        outbox=_runtime_outbox(
            "TASK_GRAPH",
            graph.graph_id,
            graph_record["status_version"] + 1,
            "TASK_GRAPH_ACTIVE",
        ),
    )
    node = store.get_node(graph.graph_id, graph.revision, "root")
    attempt_id = "attempt::gr03a::pre-side-effect-failure"
    claim = store.claim_ready_node(
        attempt_id=attempt_id,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id="root",
        expected_node_version=node["status_version"],
        expected_attempt_count=0,
        owner_id="gr03a-worker",
        lease_ttl_ms=10_000,
        budget_id=budget.budget_id,
        operation_scope=admitted.business_scope,
        operation_fingerprint=admitted.operation_fingerprint,
        attempt_created_outbox=_runtime_outbox(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=attempt_id,
        ),
        attempt_leased_outbox=_runtime_outbox(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id=attempt_id,
        ),
    )
    running_node = store.transition_node(
        graph.graph_id,
        graph.revision,
        "root",
        expected_version=claim["node"]["status_version"],
        target_status="RUNNING",
        outbox=_runtime_outbox(
            "TASK_NODE",
            f"{graph.graph_id}@{graph.revision}/root",
            claim["node"]["status_version"] + 1,
            "TASK_NODE_RUNNING",
        ),
    )
    running_attempt = store.transition_attempt(
        attempt_id,
        owner_id="gr03a-worker",
        fencing_token=claim["lease"]["fencing_token"],
        expected_version=claim["attempt"]["status_version"],
        target_status="RUNNING",
        outbox=_runtime_outbox(
            "TASK_ATTEMPT",
            attempt_id,
            claim["attempt"]["status_version"] + 1,
            "TASK_ATTEMPT_RUNNING",
            attempt_id=attempt_id,
        ),
    )
    retry_error_ref = "taskerror://gr03a/pre-side-effect"
    retry_usage_id = "budget-usage://" + hashlib.sha256(
        f"{budget.budget_id}\n{attempt_id}\nretry".encode("utf-8")
    ).hexdigest()
    retry_outbox = _runtime_outbox(
        "TASK_ATTEMPT",
        attempt_id,
        running_attempt["status_version"] + 1,
        "TASK_ATTEMPT_FAILED",
        attempt_id=attempt_id,
        evidence_refs=(retry_error_ref, retry_usage_id),
    )
    retry = store.schedule_retry(
        attempt_id,
        budget_id=budget.budget_id,
        owner_id="gr03a-worker",
        fencing_token=claim["lease"]["fencing_token"],
        expected_attempt_version=running_attempt["status_version"],
        error_ref=retry_error_ref,
        outbox=retry_outbox,
    )
    assert running_node["status"] == "RUNNING"
    assert retry["replayed"] is False
    assert retry["node"]["status"] == "RETRY_PENDING"
    assert retry["retry_not_before_ms"] == NOW + 100
    assert retry["lease"]["status"] == "RELEASED"
    with sqlite3.connect(db_path) as conn:
        before_replay_counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM task_runtime_budget_usage),
                (SELECT COUNT(*) FROM task_runtime_outbox)
            """
        ).fetchone()

    exact_replay = DurableTaskStore(db_path, clock=clock).schedule_retry(
        attempt_id,
        budget_id=budget.budget_id,
        owner_id="gr03a-worker",
        fencing_token=claim["lease"]["fencing_token"],
        expected_attempt_version=running_attempt["status_version"],
        error_ref=retry_error_ref,
        outbox=retry_outbox,
    )
    assert exact_replay == {**retry, "replayed": True}
    with sqlite3.connect(db_path) as conn:
        after_replay_counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM task_runtime_budget_usage),
                (SELECT COUNT(*) FROM task_runtime_outbox)
            """
        ).fetchone()
    assert after_replay_counts == before_replay_counts
    drift_error_ref = "taskerror://gr03a/different-failure"
    drift_outbox = _runtime_outbox(
        "TASK_ATTEMPT",
        attempt_id,
        running_attempt["status_version"] + 1,
        "TASK_ATTEMPT_FAILED",
        attempt_id=attempt_id,
        evidence_refs=(drift_error_ref, retry_usage_id),
    )
    with pytest.raises(TaskStoreError) as drift:
        store.schedule_retry(
            attempt_id,
            budget_id=budget.budget_id,
            owner_id="gr03a-worker",
            fencing_token=claim["lease"]["fencing_token"],
            expected_attempt_version=running_attempt["status_version"],
            error_ref=drift_error_ref,
            outbox=drift_outbox,
        )
    assert drift.value.reason_code == TASK_STORE_CAS_CONFLICT

    replay = HostCommandRepository(db_path, clock=clock).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    resumed = AdmittedExecution.from_contracts(
        command=replay["command"],
        assignment=replay["assignment"],
        graph=replay["graph"],
        replayed=True,
    )
    clock.value = NOW + 99
    with pytest.raises(GovernedRuntimeError) as waiting:
        _ = [
            event
            async for event in _coordinator(db_path, calls, clock=clock).execute_stream(
                admitted=resumed,
                command=replay["command"],
                work_order=replay["work_order"],
            )
        ]
    assert waiting.value.reason_code == "GOVERNED_EXECUTION_IN_PROGRESS"
    assert calls == []

    clock.value = NOW + 100
    events = [
        event
        async for event in _coordinator(db_path, calls, clock=clock).execute_stream(
            admitted=resumed,
            command=replay["command"],
            work_order=replay["work_order"],
        )
    ]
    assert events[-1]["execution_receipt"]["accepted"] is True
    assert calls == ["root"]
    reopened = DurableTaskStore(db_path, clock=clock)
    snapshot = reopened.read_scheduler_snapshot(graph.graph_id, graph.revision)
    attempts = [row for row in snapshot["attempts"] if row["node_id"] == "root"]
    assert [(row["attempt_no"], row["status"]) for row in attempts] == [
        (1, "FAILED"),
        (2, "SUCCEEDED"),
    ]
    assert reopened.get_node(graph.graph_id, graph.revision, "root")[
        "retry_not_before_ms"
    ] is None


@pytest.mark.parametrize("lose_retry_response", (False, True))
@pytest.mark.asyncio
async def test_ne01_b2_coordinator_safe_retry_charges_budget_and_runs_attempt_two(
    tmp_path: Path,
    lose_retry_response: bool,
) -> None:
    class _Clock:
        def __init__(self, value: int) -> None:
            self.value = value

        def __call__(self) -> int:
            return self.value

    clock = _Clock(NOW)
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    graph = replace(
        graph,
        nodes=(
            replace(
                graph.nodes[0],
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 100},
            ),
        ),
    )
    record = HostCommandRepository(db_path, clock=clock).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    calls: list[str] = []
    resolver = _RetryOnceResolver(calls)
    runtime = _coordinator(
        db_path,
        calls,
        clock=clock,
        worker_resolver=resolver,
    )
    schedule_retry_calls = 0
    if lose_retry_response:
        committed_schedule_retry = runtime._store.schedule_retry  # noqa: SLF001

        def lose_first_committed_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal schedule_retry_calls
            schedule_retry_calls += 1
            result = committed_schedule_retry(*args, **kwargs)
            if schedule_retry_calls == 1:
                raise TimeoutError("simulated response loss after retry commit")
            return result

        runtime._store.schedule_retry = lose_first_committed_response  # type: ignore[method-assign]  # noqa: SLF001
    first_events = [
        event
        async for event in runtime.execute_stream(
            admitted=admitted,
            command=record["command"],
            work_order=record["work_order"],
        )
    ]
    first_receipt = first_events[-1]["execution_receipt"]
    assert first_receipt["status"] == "RETRY_PENDING"
    assert first_receipt["reason_code"] == "GOVERNED_EXECUTION_IN_PROGRESS"
    assert resolver.resolve_count == 1
    assert calls == []
    assert schedule_retry_calls == (2 if lose_retry_response else 0)

    after_first = DurableTaskStore(db_path, clock=clock).read_scheduler_snapshot(
        graph.graph_id,
        graph.revision,
    )
    assert [(row["attempt_no"], row["status"]) for row in after_first["attempts"]] == [
        (1, "FAILED"),
    ]
    assert after_first["graph_record"]["nodes"][0]["status"] == "RETRY_PENDING"
    assert after_first["budgets"][0]["status"] == "ACTIVE"
    assert after_first["budgets"][0]["consumed"]["retry_count"] == 1
    retry_usages = [
        row for row in after_first["budget_usage"] if row["charge_kind"] == "RETRY"
    ]
    assert len(retry_usages) == 1
    assert retry_usages[0]["accepted"] is True
    assert retry_usages[0]["delta"]["retry_count"] == 1
    with sqlite3.connect(db_path) as conn:
        attempt_one_reservations = conn.execute(
            """
            SELECT COUNT(*)
            FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ?
            """,
            (after_first["attempts"][0]["attempt_id"],),
        ).fetchone()[0]
        retry_outboxes = conn.execute(
            """
            SELECT event_type, evidence_refs_json
            FROM task_runtime_outbox
            WHERE attempt_id = ? AND event_type IN (
                'BUDGET_CONSUMED', 'TASK_ATTEMPT_FAILED',
                'TASK_NODE_RETRY_PENDING', 'TASK_LEASE_RELEASED'
            )
            ORDER BY event_type
            """,
            (after_first["attempts"][0]["attempt_id"],),
        ).fetchall()
    assert attempt_one_reservations == 0
    retry_outboxes = [
        row
        for row in retry_outboxes
        if row[0] != "BUDGET_CONSUMED"
        or retry_usages[0]["usage_id"] in json.loads(row[1])
    ]
    assert [row[0] for row in retry_outboxes] == [
        "BUDGET_CONSUMED",
        "TASK_ATTEMPT_FAILED",
        "TASK_LEASE_RELEASED",
        "TASK_NODE_RETRY_PENDING",
    ]
    retry_evidence = {
        row[0]: tuple(json.loads(row[1])) for row in retry_outboxes
    }
    assert retry_usages[0]["usage_id"] in retry_evidence["BUDGET_CONSUMED"]
    assert set(retry_evidence.values()) == {
        tuple(retry_evidence["BUDGET_CONSUMED"])
    }

    clock.value = NOW + 99
    with pytest.raises(GovernedRuntimeError) as waiting:
        _ = [
            event
            async for event in _coordinator(
                db_path,
                calls,
                clock=clock,
                worker_resolver=resolver,
            ).execute_stream(
                admitted=admitted,
                command=record["command"],
                work_order=record["work_order"],
            )
        ]
    assert waiting.value.reason_code == "GOVERNED_EXECUTION_IN_PROGRESS"
    assert resolver.resolve_count == 1

    clock.value = NOW + 100
    final_events = [
        event
        async for event in _coordinator(
            db_path,
            calls,
            clock=clock,
            worker_resolver=resolver,
        ).execute_stream(
            admitted=admitted,
            command=record["command"],
            work_order=record["work_order"],
        )
    ]
    assert final_events[-1]["execution_receipt"]["accepted"] is True
    assert resolver.resolve_count == 2
    assert calls == ["root"]

    final_snapshot = DurableTaskStore(db_path, clock=clock).read_scheduler_snapshot(
        graph.graph_id,
        graph.revision,
    )
    assert [(row["attempt_no"], row["status"]) for row in final_snapshot["attempts"]] == [
        (1, "FAILED"),
        (2, "SUCCEEDED"),
    ]
    assert final_snapshot["budgets"][0]["status"] == "CONSUMED"
    assert final_snapshot["budgets"][0]["consumed"]["retry_count"] == 1


@pytest.mark.asyncio
async def test_ne01_b2_retry_budget_exhaustion_fails_closed_before_reservation(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    graph = replace(
        graph,
        nodes=(
            replace(
                graph.nodes[0],
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 0},
            ),
        ),
    )
    work_order = replace(
        work_order,
        budget_policy=replace(
            work_order.budget_policy,
            limits=replace(work_order.budget_policy.limits, retry_count=0),
        ),
    )
    record = HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    calls: list[str] = []
    resolver = _RetryOnceResolver(calls)
    events = [
        event
        async for event in _coordinator(
            db_path,
            calls,
            worker_resolver=resolver,
        ).execute_stream(
            admitted=admitted,
            command=record["command"],
            work_order=record["work_order"],
        )
    ]
    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    assert resolver.resolve_count == 1
    assert calls == []

    snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        graph.graph_id,
        graph.revision,
    )
    assert snapshot["graph_record"]["status"] == "BLOCKED"
    assert snapshot["graph_record"]["nodes"][0]["status"] == "FAILED"
    assert [(row["attempt_no"], row["status"]) for row in snapshot["attempts"]] == [
        (1, "FAILED"),
    ]
    assert snapshot["active_leases"] == []
    budget = snapshot["budgets"][0]
    assert budget["status"] == "RELEASED"
    assert budget["consumed"]["retry_count"] == 0
    assert budget["attempted_consumed"]["retry_count"] == 1
    assert budget["exceeded_dimensions"] == ["retry_count"]
    retry_usages = [
        row for row in snapshot["budget_usage"] if row["charge_kind"] == "RETRY"
    ]
    assert len(retry_usages) == 1
    assert retry_usages[0]["accepted"] is False
    assert retry_usages[0]["reason_code"] == "TASK_STORE_BUDGET_EXCEEDED"
    with sqlite3.connect(db_path) as conn:
        reservation_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_idempotency_reservation"
        ).fetchone()[0]
        graph_blocked_outbox = conn.execute(
            """
            SELECT attempt_id, evidence_refs_json
            FROM task_runtime_outbox
            WHERE aggregate_type = 'TASK_GRAPH'
              AND event_type = 'TASK_GRAPH_BLOCKED'
            """
        ).fetchone()
    assert reservation_count == 0
    assert graph_blocked_outbox is not None
    assert graph_blocked_outbox[0] == snapshot["attempts"][0]["attempt_id"]
    assert retry_usages[0]["usage_id"] in json.loads(graph_blocked_outbox[1])


@pytest.mark.asyncio
async def test_ne01_b2_post_reservation_worker_failure_is_never_auto_retried(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    graph = replace(
        graph,
        nodes=(
            replace(
                graph.nodes[0],
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 0},
            ),
        ),
    )
    record = HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    calls: list[str] = []
    events = [
        event
        async for event in _coordinator(
            db_path,
            calls,
            fail_nodes={"root"},
        ).execute_stream(
            admitted=admitted,
            command=record["command"],
            work_order=record["work_order"],
        )
    ]
    assert events[-1]["execution_receipt"]["status"] == "FAILED"
    assert calls == ["root"]
    snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        graph.graph_id,
        graph.revision,
    )
    assert snapshot["graph_record"]["status"] == "FAILED"
    assert snapshot["graph_record"]["nodes"][0]["status"] == "FAILED"
    assert [(row["attempt_no"], row["status"]) for row in snapshot["attempts"]] == [
        (1, "FAILED"),
    ]
    assert not any(row["charge_kind"] == "RETRY" for row in snapshot["budget_usage"])
    with sqlite3.connect(db_path) as conn:
        reservations = conn.execute(
            """
            SELECT attempt_id, status
            FROM task_runtime_idempotency_reservation
            ORDER BY reservation_id
            """
        ).fetchall()
    assert reservations == [(snapshot["attempts"][0]["attempt_id"], "RESERVED")]


@pytest.mark.asyncio
async def test_ne01_b1_atomic_claim_enforces_durable_graph_concurrency(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()))
    )
    record = HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    runtime = _coordinator(db_path, [])
    budget = await runtime._budgets.ensure_active(  # noqa: SLF001
        admitted=admitted,
        work_order=record["work_order"],
    )
    first_store = DurableTaskStore(db_path, clock=lambda: NOW)
    graph_record = first_store.get_graph(graph.graph_id, graph.revision)
    first_store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=graph_record["status_version"],
        target_status="ACTIVE",
        outbox=_runtime_outbox(
            "TASK_GRAPH",
            graph.graph_id,
            graph_record["status_version"] + 1,
            "TASK_GRAPH_ACTIVE",
        ),
    )
    left = first_store.get_node(graph.graph_id, graph.revision, "left")
    first_store.claim_ready_node(
        attempt_id="attempt::gr03a::left",
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id="left",
        expected_node_version=left["status_version"],
        expected_attempt_count=0,
        owner_id="gr03a-left",
        lease_ttl_ms=10_000,
        budget_id=budget.budget_id,
        operation_scope=admitted.for_node("left").business_scope,
        operation_fingerprint=admitted.for_node("left").operation_fingerprint,
        attempt_created_outbox=_runtime_outbox(
            "TASK_ATTEMPT",
            "attempt::gr03a::left",
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id="attempt::gr03a::left",
        ),
        attempt_leased_outbox=_runtime_outbox(
            "TASK_ATTEMPT",
            "attempt::gr03a::left",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt::gr03a::left",
        ),
    )

    reopened = DurableTaskStore(db_path, clock=lambda: NOW)
    right = reopened.get_node(graph.graph_id, graph.revision, "right")
    with pytest.raises(TaskStoreError) as blocked:
        reopened.claim_ready_node(
            attempt_id="attempt::gr03a::right",
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id="right",
            expected_node_version=right["status_version"],
            expected_attempt_count=0,
            owner_id="gr03a-right",
            lease_ttl_ms=10_000,
            budget_id=budget.budget_id,
            operation_scope=admitted.for_node("right").business_scope,
            operation_fingerprint=admitted.for_node("right").operation_fingerprint,
            attempt_created_outbox=_runtime_outbox(
                "TASK_ATTEMPT",
                "attempt::gr03a::right",
                0,
                "TASK_ATTEMPT_CREATED",
                attempt_id="attempt::gr03a::right",
            ),
            attempt_leased_outbox=_runtime_outbox(
                "TASK_ATTEMPT",
                "attempt::gr03a::right",
                1,
                "TASK_ATTEMPT_LEASED",
                attempt_id="attempt::gr03a::right",
            ),
        )

    assert blocked.value.reason_code == TASK_STORE_SCHEDULER_CAPACITY
    snapshot = reopened.read_scheduler_snapshot(graph.graph_id, graph.revision)
    assert [(row["node_id"], row["attempt_no"]) for row in snapshot["attempts"]] == [
        ("left", 1),
    ]
    assert reopened.get_node(graph.graph_id, graph.revision, "right")["status"] == "READY"


@pytest.mark.asyncio
async def test_ne01_b1_atomic_claim_exact_replay_returns_one_live_authority(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    record = HostCommandRepository(db_path, clock=lambda: NOW).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    runtime = _coordinator(db_path, [])
    budget = await runtime._budgets.ensure_active(  # noqa: SLF001
        admitted=admitted,
        work_order=record["work_order"],
    )
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    graph_record = store.get_graph(graph.graph_id, graph.revision)
    store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=graph_record["status_version"],
        target_status="ACTIVE",
        outbox=_runtime_outbox(
            "TASK_GRAPH",
            graph.graph_id,
            graph_record["status_version"] + 1,
            "TASK_GRAPH_ACTIVE",
        ),
    )
    node = store.get_node(graph.graph_id, graph.revision, "root")
    attempt_id = "attempt::gr03a::claim-replay"
    created_outbox = _runtime_outbox(
        "TASK_ATTEMPT",
        attempt_id,
        0,
        "TASK_ATTEMPT_CREATED",
        attempt_id=attempt_id,
    )
    leased_outbox = _runtime_outbox(
        "TASK_ATTEMPT",
        attempt_id,
        1,
        "TASK_ATTEMPT_LEASED",
        attempt_id=attempt_id,
    )
    claim_args = {
        "attempt_id": attempt_id,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "node_id": "root",
        "expected_node_version": node["status_version"],
        "expected_attempt_count": 0,
        "owner_id": "gr03a-worker",
        "lease_ttl_ms": 10_000,
        "budget_id": budget.budget_id,
        "operation_scope": admitted.business_scope,
        "operation_fingerprint": admitted.operation_fingerprint,
        "attempt_created_outbox": created_outbox,
        "attempt_leased_outbox": leased_outbox,
    }
    first = store.claim_ready_node(**claim_args)
    assert first["replayed"] is False

    reopened = DurableTaskStore(db_path, clock=lambda: NOW)
    replay = reopened.claim_ready_node(**claim_args)
    assert replay["replayed"] is True
    assert replay["attempt"]["attempt_id"] == first["attempt"]["attempt_id"]
    assert replay["lease"]["lease_id"] == first["lease"]["lease_id"]
    assert replay["lease"]["fencing_token"] == first["lease"]["fencing_token"]
    snapshot = reopened.read_scheduler_snapshot(graph.graph_id, graph.revision)
    assert len(snapshot["attempts"]) == 1
    assert len(snapshot["active_leases"]) == 1
    with sqlite3.connect(db_path) as conn:
        claim_outbox_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()[0]
    assert claim_outbox_count == 3

    with pytest.raises(TaskStoreError) as drifted:
        reopened.claim_ready_node(**{**claim_args, "owner_id": "other-worker"})
    assert drifted.value.reason_code == TASK_STORE_CAS_CONFLICT
    after_drift = reopened.read_scheduler_snapshot(graph.graph_id, graph.revision)
    assert len(after_drift["attempts"]) == 1
    assert len(after_drift["active_leases"]) == 1


@pytest.mark.asyncio
async def test_gr03b_usage_replay_is_exact_and_competing_cas_has_one_winner(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("root", ()), ("later", ("root",)))
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    record = repository.admit(command, work_order, assignment, graph)
    admitted = AdmittedExecution.from_contracts(
        command=record["command"], assignment=record["assignment"], graph=record["graph"]
    )
    _ = [
        event
        async for event in _coordinator(db_path, [])._execute_node_stream(  # noqa: SLF001
            admitted=admitted.for_node("root"),
            command=record["command"],
            work_order=record["work_order"],
        )
    ]
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    snapshot = store.read_scheduler_snapshot(graph.graph_id, graph.revision)
    budget = snapshot["budgets"][0]
    usage = snapshot["budget_usage"][0]
    before = store.reconcile_budget(budget["budget_id"])
    replay = store.consume_budget_once(
        budget["budget_id"],
        usage_id=usage["usage_id"],
        charge_kind=usage["charge_kind"],
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id=usage["node_id"],
        attempt_id=usage["attempt_id"],
        delta=BudgetAmount.from_mapping(usage["delta"]),
        expected_version=0,
        outbox=_budget_usage_outbox(
            budget["budget_id"], 1, usage["attempt_id"], "ignored-exact-replay"
        ),
    )
    after = store.reconcile_budget(budget["budget_id"])
    assert replay.replayed is True
    assert after["status_version"] == before["status_version"]
    assert after["consumed"] == before["consumed"]

    expected = after["status_version"]
    attempt_id = usage["attempt_id"]

    def charge(suffix: str) -> Any:
        return DurableTaskStore(db_path, clock=lambda: NOW).consume_budget_once(
            budget["budget_id"],
            usage_id=f"budget-usage://cas-{suffix}",
            charge_kind="WORKER_USAGE",
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id="root",
            attempt_id=attempt_id,
            delta=BudgetAmount(token_count=1),
            expected_version=expected,
            outbox=_budget_usage_outbox(
                budget["budget_id"], expected + 1, attempt_id, f"cas-{suffix}"
            ),
        )

    outcomes: list[Any] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(charge, suffix) for suffix in ("a", "b")]
        for future in futures:
            try:
                outcomes.append(future.result())
            except TaskStoreError as exc:
                outcomes.append(exc)
    assert len([item for item in outcomes if not isinstance(item, Exception)]) == 1
    conflicts = [item for item in outcomes if isinstance(item, TaskStoreError)]
    assert len(conflicts) == 1
    assert conflicts[0].reason_code == TASK_STORE_CAS_CONFLICT


def test_gr03a_cycle_fails_before_any_graph_or_command_write(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("first", ("second",)), ("second", ("first",)))
    )

    with pytest.raises(HostCommandRepositoryError) as error:
        HostCommandRepository(db_path, clock=lambda: NOW).admit(
            command, work_order, assignment, graph
        )

    assert error.value.reason_code == HOST_COMMAND_INVALID
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM host_command_journal").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_graph").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_node").fetchone()[0] == 0


def test_gr03a_admission_rejects_forged_ready_dependent(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("root", ()), ("dependent", ("root",)))
    )
    forged = replace(
        graph,
        nodes=(graph.nodes[0], replace(graph.nodes[1], status=TaskNodeStatus.READY)),
    )

    with pytest.raises(HostCommandRepositoryError) as error:
        HostCommandRepository(db_path, clock=lambda: NOW).admit(
            command, work_order, assignment, forged
        )

    assert error.value.reason_code == HOST_COMMAND_INVALID
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM host_command_journal").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_node").fetchone()[0] == 0
