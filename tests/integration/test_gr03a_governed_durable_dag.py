from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.review_policy import ReviewResumePhase
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
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
    TASK_STORE_OWNERSHIP_CONFLICT,
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


def _parallel_decision_argument_snapshot(
    kwargs: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    return (
        kwargs["decision_ref"],
        json.loads(
            json.dumps(
                kwargs["decision_payload"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        ),
    )


def _elapsed_clock() -> Any:
    started = time.monotonic()

    def clock() -> int:
        return NOW + int((time.monotonic() - started) * 1_000)

    return clock


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
    concurrency: int = 1,
    token_limit: int = 100_000,
    completion_policy: TaskGraphCompletionPolicy | None = None,
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
                token_count=token_limit,
                tool_call_count=100,
                time_ms=600_000,
                concurrency=concurrency,
                retry_count=10,
                model_cost_micros=10_000_000,
            ),
        ),
    )
    depended_on = {
        dependency for _, dependencies in specs for dependency in dependencies
    }
    sink_node_ids = tuple(
        node_id for node_id, _ in specs if node_id not in depended_on
    )
    result_node_id = (
        sink_node_ids[-1]
        if len(sink_node_ids) == 1
        or completion_policy is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
        else None
    )
    graph = TaskGraph(
        graph_id="graph-gr03a",
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref="planner://gr03a/dag",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        result_node_id=result_node_id,
        completion_policy=completion_policy,
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


class _AsyncPublisherProbe:
    def __init__(self) -> None:
        self.active = 0
        self.peak_active = 0
        self.call_count = 0

    async def publish_pending(self, *, limit: int = 100) -> dict[str, Any]:
        self.call_count += 1
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            await asyncio.sleep(0.005)
        finally:
            self.active -= 1
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


class _SlowWorker(_Worker):
    def __init__(
        self,
        calls: list[str],
        *,
        delay_seconds: float,
    ) -> None:
        super().__init__(calls, set())
        self._delay_seconds = delay_seconds

    async def execute_stream(self, **kwargs: Any):
        await asyncio.sleep(self._delay_seconds)
        async for event in super().execute_stream(**kwargs):
            yield event


class _AsyncFanOutProbe:
    """Async barrier plus counters used to prove bounded, overlapping work."""

    def __init__(self, root_nodes: tuple[str, ...]) -> None:
        self.root_nodes = frozenset(root_nodes)
        self.active = 0
        self.peak_active = 0
        self.entered_nodes: set[str] = set()
        self.completed_nodes: set[str] = set()
        self.cancelled_nodes: set[str] = set()
        self.progress_events_emitted: dict[str, int] = {}
        self.root_tasks: dict[str, asyncio.Task[Any]] = {}
        self.all_roots_started = asyncio.Event()
        self._lock = asyncio.Lock()

    async def activate(self, node_id: str) -> None:
        async with self._lock:
            task = asyncio.current_task()
            assert task is not None
            self.root_tasks[node_id] = task
            self.entered_nodes.add(node_id)
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            if self.entered_nodes == self.root_nodes:
                self.all_roots_started.set()

    async def deactivate(self) -> None:
        async with self._lock:
            self.active -= 1


def _fan_out_commit(node_id: str, *, token_count: int = 10) -> dict[str, Any]:
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "node_id": node_id,
        "budget_usage": {
            "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
            "usage_id": f"worker-usage://{node_id}/fan-out",
            "measurements": {
                "token_count": token_count,
                "retry_count": 0,
                "model_cost_micros": 25,
            },
            "evidence_refs": [f"model-budget://{node_id}/fan-out"],
        },
    }


class _FanOutWorker(_Worker):
    def __init__(
        self,
        calls: list[str],
        *,
        db_path: Path,
        probe: _AsyncFanOutProbe,
        require_full_barrier: bool,
        root_delay_seconds: float = 0.0,
        progress_release: asyncio.Event | None = None,
        block_until_cancelled: bool = False,
        fail_node_id: str | None = None,
        self_cancel_node_id: str | None = None,
        progress_event_count: int = 1,
        node_delays: dict[str, float] | None = None,
        token_counts: dict[str, int] | None = None,
    ) -> None:
        super().__init__(calls, set())
        self._db_path = db_path
        self._probe = probe
        self._require_full_barrier = require_full_barrier
        self._root_delay_seconds = root_delay_seconds
        self._progress_release = progress_release
        self._block_until_cancelled = block_until_cancelled
        self._fail_node_id = fail_node_id
        self._self_cancel_node_id = self_cancel_node_id
        self._progress_event_count = int(progress_event_count)
        self._node_delays = dict(node_delays or {})
        self._token_counts = dict(token_counts or {})
        self.join_dependency_statuses: tuple[str, ...] | None = None

    async def execute_stream(self, *, execution_context: dict[str, Any], **_: Any):
        node_id = execution_context["node_id"]
        self._calls.append(node_id)
        if node_id not in self._probe.root_nodes:
            store = DurableTaskStore(self._db_path, clock=lambda: NOW)
            self.join_dependency_statuses = tuple(
                store.get_node("graph-gr03a", 1, dependency)["status"]
                for dependency in sorted(self._probe.root_nodes)
            )
            yield _fan_out_commit(node_id)
            return

        await self._probe.activate(node_id)
        try:
            if self._require_full_barrier:
                await asyncio.wait_for(
                    self._probe.all_roots_started.wait(),
                    timeout=2.0,
                )
            for progress_no in range(1, self._progress_event_count + 1):
                self._probe.progress_events_emitted[node_id] = progress_no
                yield {
                    "phase": "EXECUTE",
                    "done": False,
                    "node_id": node_id,
                    "message": f"fan-out progress {progress_no}: {node_id}",
                }
            if self._progress_release is not None:
                await self._progress_release.wait()
            if node_id == self._self_cancel_node_id:
                raise asyncio.CancelledError(f"fan-out worker self-cancelled: {node_id}")
            if node_id == self._fail_node_id:
                raise RuntimeError(f"fan-out worker failed: {node_id}")
            if self._block_until_cancelled:
                await asyncio.Future()
            delay_seconds = self._node_delays.get(node_id, self._root_delay_seconds)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            self._probe.completed_nodes.add(node_id)
            yield _fan_out_commit(
                node_id,
                token_count=self._token_counts.get(node_id, 10),
            )
        except asyncio.CancelledError:
            self._probe.cancelled_nodes.add(node_id)
            raise
        finally:
            await self._probe.deactivate()


class _StaticResolver:
    def __init__(self, worker: _Worker) -> None:
        self._worker = worker

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        assert worker_route.startswith("worker:")
        return self._worker


class _FailingNodeResolver(_StaticResolver):
    def __init__(self, worker: _Worker, *, fail_node_id: str) -> None:
        super().__init__(worker)
        self._fail_route = f"worker:{fail_node_id}"

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        if worker_route == self._fail_route:
            raise RuntimeError(f"worker registry failed before reservation: {worker_route}")
        return super().resolve(executor=executor, worker_route=worker_route)


class _FailingResolver:
    def __init__(self) -> None:
        self.resolve_count = 0

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        assert worker_route.startswith("worker:")
        self.resolve_count += 1
        raise RuntimeError("worker registry is unavailable")


class _FailingHeartbeatStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.heartbeat_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def heartbeat_lease(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.heartbeat_calls += 1
        if self.heartbeat_calls <= 2:
            raise RuntimeError("lease heartbeat transport unavailable")
        return self._delegate.heartbeat_lease(*args, **kwargs)


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
    def __init__(
        self,
        decision: str = "ACCEPT",
        *,
        decisions_by_node: dict[str, str] | None = None,
    ) -> None:
        self._decision = decision
        self._decisions_by_node = dict(decisions_by_node or {})

    def judge(
        self,
        *,
        execution_context: dict[str, Any],
        evidence_refs: tuple[str, ...],
        **_: Any,
    ) -> dict[str, Any]:
        decision = self._decisions_by_node.get(
            execution_context["node_id"],
            self._decision,
        )
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": f"accept-{execution_context['node_id']}",
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": decision,
            "reason_code": (
                None if decision == "ACCEPT" else "ACCEPTANCE_REVIEW_REQUIRED"
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
    acceptance: Any = None,
    clock: Any = None,
    worker_resolver: Any = None,
    task_store: Any = None,
    lease_ttl_ms: int = 120_000,
    operation_ttl_ms: int = 120_000,
    bounded_fanout_enabled: bool = False,
    fanout_event_buffer_size: int = 32,
    outbox_publisher: Any = None,
) -> GovernedTaskCoordinator:
    resolved_clock = clock if clock is not None else (lambda: NOW)
    return GovernedTaskCoordinator(
        task_store=(
            task_store
            if task_store is not None
            else DurableTaskStore(db_path, clock=resolved_clock)
        ),
        outbox_publisher=(
            outbox_publisher if outbox_publisher is not None else _Publisher()
        ),
        outbox_factory=OutboxMessage,
        worker_resolver=(
            worker_resolver
            if worker_resolver is not None
            else _Resolver(calls, fail_nodes)
        ),
        verifier=_Verifier(),
        acceptance=(
            acceptance if acceptance is not None else _Acceptance(acceptance_decision)
        ),
        review_runtime=review_runtime,
        owner_id="gr03a-worker",
        lease_ttl_ms=lease_ttl_ms,
        operation_ttl_ms=operation_ttl_ms,
        bounded_fanout_enabled=bounded_fanout_enabled,
        fanout_event_buffer_size=fanout_event_buffer_size,
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
async def test_ne01_bounded_fan_out_overlaps_two_roots_then_runs_fan_in(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    publisher = _AsyncPublisherProbe()
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
            outbox_publisher=publisher,
        ),
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

    assert events[-1]["execution_receipt"]["accepted"] is True
    assert probe.entered_nodes == {"left", "right"}
    assert probe.peak_active == contracts[1].budget_policy.limits.concurrency == 2
    assert probe.active == 0
    assert publisher.call_count > 1
    assert publisher.peak_active == 1
    assert worker.join_dependency_statuses == ("ACCEPTED", "ACCEPTED")
    assert calls[-1] == "join"
    graph_record = DurableTaskStore(db_path, clock=lambda: NOW).get_graph(
        "graph-gr03a", 1
    )
    assert graph_record["status"] == "COMPLETED"


@pytest.mark.parametrize("lose_settlement_response", (False, True))
@pytest.mark.asyncio
async def test_ne01_parallel_failure_waits_for_sibling_then_outer_settles_once(
    tmp_path: Path,
    lose_settlement_response: bool,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=False,
        # The second admission-order node fails before it owns an operation
        # reservation while the first remains in flight.  Outer fan-in must
        # let the first reach a durable node-local terminal before settlement.
        node_delays={"left": 0.08},
    )
    runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
        bounded_fanout_enabled=True,
    )
    settlement_calls = 0
    if lose_settlement_response:
        committed_settlement = runtime._store.settle_parallel_graph_budget  # noqa: SLF001

        def lose_first_committed_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal settlement_calls
            settlement_calls += 1
            result = committed_settlement(*args, **kwargs)
            if settlement_calls == 1:
                raise TimeoutError("simulated outer settlement response loss")
            return result

        runtime._store.settle_parallel_graph_budget = lose_first_committed_response  # type: ignore[method-assign]  # noqa: SLF001
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=runtime,
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

    assert events[-1]["execution_receipt"]["accepted"] is False
    assert events[-1]["execution_receipt"]["status"] == "FAILED"
    settlement = events[-1]["execution_receipt"]["projection"]["parallel_settlement"]
    assert settlement["graph_status"] == "FAILED"
    assert settlement["decision_ref"].startswith("taskbatch://sha256/")
    decision_ref = settlement["decision_ref"]
    assert decision_ref in events[-1]["execution_receipt"]["evidence_refs"]
    assert probe.entered_nodes == {"left"}
    assert probe.cancelled_nodes == set()
    assert probe.completed_nodes == {"left"}
    assert "join" not in calls
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "ACCEPTED"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "FAILED"
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["budgets"][0]["status"] == "RELEASED"
    with sqlite3.connect(db_path) as conn:
        operation_commit_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_COMMITTED'
            """
        ).fetchone()[0]
        graph_failure_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'TASK_GRAPH_FAILED'
            """
        ).fetchone()[0]
        settlement_outboxes = conn.execute(
            """
            SELECT event_type, attempt_id, evidence_refs_json
            FROM task_runtime_outbox
            WHERE event_type IN (
                'TASK_GRAPH_FAILED', 'BUDGET_RESERVATION_RELEASED'
            )
            ORDER BY event_type
            """
        ).fetchall()
    assert operation_commit_count == 1
    assert graph_failure_count == 1
    assert settlement_calls == (2 if lose_settlement_response else 0)
    assert [row[0] for row in settlement_outboxes] == [
        "BUDGET_RESERVATION_RELEASED",
        "TASK_GRAPH_FAILED",
    ]
    assert all(row[1] is None for row in settlement_outboxes)
    assert all(decision_ref in json.loads(row[2]) for row in settlement_outboxes)
    assert store.get_graph("graph-gr03a", 1)["status"] == "FAILED"


@pytest.mark.asyncio
async def test_ne01_parallel_post_reservation_failure_stays_recovery_required(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        fail_node_id="right",
        node_delays={"left": 0.05},
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
    )

    first_events = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    first_receipt = first_events[-1]["execution_receipt"]
    assert first_receipt["node_id"] == "right"
    assert first_receipt["status"] == "RECOVERY_REQUIRED"
    assert first_receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert first_receipt["projection"]["reservation_status"] == "RESERVED"

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "RUNNING"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "FAILED"
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert "left" in probe.cancelled_nodes
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["budgets"][0]["status"] == "ACTIVE"
    durable_command = HostCommandRepository(db_path, clock=lambda: NOW).get(
        contracts[0].command_id
    )
    assert durable_command is not None
    right_reservation = store.get_operation_reservation(
        AdmittedExecution.from_contracts(
            command=durable_command["command"],
            assignment=durable_command["assignment"],
            graph=durable_command["graph"],
        ).for_node("right").reservation_id
    )
    assert right_reservation["status"] == "RESERVED"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type IN (
                'TASK_GRAPH_FAILED', 'TASK_GRAPH_BLOCKED',
                'BUDGET_RESERVATION_RELEASED'
            )
            """
        ).fetchone()[0] == 0

    replay_events = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    assert replay_events[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "ACTIVE"
    assert "join" not in calls


@pytest.mark.asyncio
async def test_ne01_b4_live_parallel_batch_reentry_preserves_active_authority(
    tmp_path: Path,
) -> None:
    class _SplitLiveWorker(_Worker):
        def __init__(self, calls: list[str]) -> None:
            super().__init__(calls, set())
            self.right_started = asyncio.Event()
            self.release_right = asyncio.Event()

        async def execute_stream(
            self,
            *,
            execution_context: dict[str, Any],
            **_: Any,
        ):
            node_id = execution_context["node_id"]
            self._calls.append(node_id)
            if node_id == "left":
                await self.right_started.wait()
            else:
                self.right_started.set()
                await self.release_right.wait()
            yield _fan_out_commit(node_id)

    class _ForbiddenReentryResolver:
        def __init__(self) -> None:
            self.resolve_count = 0

        def resolve(self, *, executor: str, worker_route: str) -> _Worker:
            self.resolve_count += 1
            raise AssertionError(
                f"live parallel re-entry must not resolve {executor}/{worker_route}"
            )

    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    repository = HostCommandRepository(db_path, clock=lambda: NOW)
    record = repository.admit(command, work_order, assignment, graph)
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )
    calls: list[str] = []
    worker = _SplitLiveWorker(calls)
    first_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_StaticResolver(worker),
        acceptance=_Acceptance(decisions_by_node={"left": "REVIEW"}),
        bounded_fanout_enabled=True,
    )

    async def collect_first_execution() -> list[dict[str, Any]]:
        return [
            event
            async for event in first_runtime.execute_stream(
                admitted=admitted,
                command=record["command"],
                work_order=record["work_order"],
            )
        ]

    first_task = asyncio.create_task(collect_first_execution())
    first_events: list[dict[str, Any]] | None = None
    try:
        await asyncio.wait_for(worker.right_started.wait(), timeout=2.0)
        store = DurableTaskStore(db_path, clock=lambda: NOW)
        for _ in range(200):
            left = store.get_node(graph.graph_id, graph.revision, "left")
            right = store.get_node(graph.graph_id, graph.revision, "right")
            snapshot = store.read_scheduler_snapshot(graph.graph_id, graph.revision)
            if (
                left["status"] == "WAITING_ACCEPTANCE_REVIEW"
                and right["status"] == "RUNNING"
                and snapshot["active_leases"]
            ):
                break
            await asyncio.sleep(0.005)
        else:
            pytest.fail("parallel execution did not reach review/running split state")

        before_snapshot = store.read_scheduler_snapshot(graph.graph_id, graph.revision)
        before_authority = {
            "attempts": [
                (
                    row["attempt_id"],
                    row["node_id"],
                    row["status"],
                    row["status_version"],
                )
                for row in before_snapshot["attempts"]
            ],
            "active_leases": [
                (
                    row["lease_id"],
                    row["attempt_id"],
                    row["status"],
                    row["fencing_token"],
                )
                for row in before_snapshot["active_leases"]
            ],
        }
        assert {row[1] for row in before_authority["attempts"]} == {
            "left",
            "right",
        }
        right_attempt_ids = {
            row[0] for row in before_authority["attempts"] if row[1] == "right"
        }
        assert {row[1] for row in before_authority["active_leases"]} == (
            right_attempt_ids
        )

        forbidden_resolver = _ForbiddenReentryResolver()
        reentry_runtime = _coordinator(
            db_path,
            calls,
            worker_resolver=forbidden_resolver,
            acceptance=_Acceptance(decisions_by_node={"left": "REVIEW"}),
            bounded_fanout_enabled=True,
        )
        with pytest.raises(GovernedRuntimeError) as in_progress:
            _ = [
                event
                async for event in reentry_runtime.execute_stream(
                    admitted=admitted,
                    command=record["command"],
                    work_order=record["work_order"],
                )
            ]
        assert in_progress.value.reason_code == "GOVERNED_EXECUTION_IN_PROGRESS"
        assert forbidden_resolver.resolve_count == 0
        assert sorted(calls) == ["left", "right"]

        after_snapshot = store.read_scheduler_snapshot(graph.graph_id, graph.revision)
        after_authority = {
            "attempts": [
                (
                    row["attempt_id"],
                    row["node_id"],
                    row["status"],
                    row["status_version"],
                )
                for row in after_snapshot["attempts"]
            ],
            "active_leases": [
                (
                    row["lease_id"],
                    row["attempt_id"],
                    row["status"],
                    row["fencing_token"],
                )
                for row in after_snapshot["active_leases"]
            ],
        }
        assert after_authority == before_authority
    finally:
        worker.release_right.set()
        first_events = await asyncio.wait_for(first_task, timeout=3.0)

    assert first_events[-1]["execution_receipt"]["status"] == (
        "WAITING_ACCEPTANCE_REVIEW"
    )
    assert sorted(calls) == ["left", "right"]


@pytest.mark.asyncio
async def test_ne01_parallel_budget_overrun_is_settled_once_by_outer_fan_in(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
        token_limit=15,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        node_delays={"right": 0.08},
        token_counts={"left": 5, "right": 20},
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
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

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["accepted"] is False
    assert receipt["projection"]["parallel_settlement"]["graph_status"] == "BLOCKED"
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert decision_ref.startswith("taskbatch://sha256/")
    assert decision_ref in receipt["evidence_refs"]
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "ACCEPTED"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "FAILED"
    assert store.get_graph("graph-gr03a", 1)["status"] == "BLOCKED"
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["budgets"][0]["status"] == "RELEASED"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox WHERE event_type = 'TASK_GRAPH_BLOCKED'"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'BUDGET_RESERVATION_RELEASED'
            """
        ).fetchone()[0] == 1
        settlement_outboxes = conn.execute(
            """
            SELECT event_type, attempt_id, evidence_refs_json
            FROM task_runtime_outbox
            WHERE event_type IN (
                'TASK_GRAPH_BLOCKED', 'BUDGET_RESERVATION_RELEASED'
            )
            ORDER BY event_type
            """
        ).fetchall()
    assert [row[0] for row in settlement_outboxes] == [
        "BUDGET_RESERVATION_RELEASED",
        "TASK_GRAPH_BLOCKED",
    ]
    assert all(row[1] is None for row in settlement_outboxes)
    assert all(decision_ref in json.loads(row[2]) for row in settlement_outboxes)


@pytest.mark.asyncio
async def test_ne01_b4_budget_crash_reentry_and_replay_keep_rejected_usage_lineage(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
        token_limit=15,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        node_delays={"right": 0.08},
        token_counts={"left": 5, "right": 20},
    )
    first_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_StaticResolver(worker),
        bounded_fanout_enabled=True,
    )
    first_settlement_calls = 0

    def timeout_before_outer_settlement(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal first_settlement_calls
        first_settlement_calls += 1
        raise TimeoutError("simulated outer Budget settlement timeout")

    first_runtime._store.settle_parallel_graph_budget = timeout_before_outer_settlement  # type: ignore[method-assign]  # noqa: SLF001
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=first_runtime,
    )

    with pytest.raises(GovernedRuntimeError):
        _ = [
            event
            async for event in first_app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    assert first_settlement_calls == 2
    assert sorted(calls) == ["left", "right"]
    crashed_store = DurableTaskStore(db_path, clock=lambda: NOW)
    crashed_snapshot = crashed_store.read_scheduler_snapshot("graph-gr03a", 1)
    assert crashed_store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert crashed_snapshot["budgets"][0]["status"] == "EXCEEDED"
    rejected_usages = [
        row for row in crashed_snapshot["budget_usage"] if row["accepted"] is False
    ]
    assert len(rejected_usages) == 1
    rejected_usage = rejected_usages[0]
    rejected_usage_id = rejected_usage["usage_id"]
    rejected_node_id = rejected_usage["node_id"]
    rejected_attempt_id = rejected_usage["attempt_id"]
    assert rejected_node_id == "right"
    assert crashed_store.get_node("graph-gr03a", 1, rejected_node_id)[
        "status"
    ] == "FAILED"

    reentry_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
    )
    reentry_events = [
        event
        async for event in reentry_app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    receipt = reentry_events[-1]["execution_receipt"]
    settlement = receipt["projection"]["parallel_settlement"]
    decision_ref = settlement["decision_ref"]
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["node_id"] == rejected_node_id
    assert receipt["attempt_id"] == rejected_attempt_id
    assert settlement == {
        "graph_status": "BLOCKED",
        "decision_ref": decision_ref,
    }
    assert decision_ref.startswith("taskbatch://sha256/")
    assert rejected_usage_id in receipt["evidence_refs"]
    assert decision_ref in receipt["evidence_refs"]
    assert sorted(calls) == ["left", "right"]

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    decision = store.get_parallel_settlement_decision(decision_ref)
    payload = decision["payload"]
    assert payload["decision_kind"] == "BUDGET"
    assert payload["selected_node_id"] == rejected_node_id
    selected_outcome = next(
        outcome
        for outcome in payload["outcomes"]
        if outcome["node_id"] == payload["selected_node_id"]
    )
    assert selected_outcome["attempt_id"] == rejected_attempt_id
    assert selected_outcome["status"] == "BUDGET_BLOCKED"
    assert selected_outcome["accepted"] is False
    assert rejected_usage_id in selected_outcome["evidence_refs"]

    replay_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
        ),
    )
    replay_events = [
        event
        async for event in replay_app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    replay_receipt = replay_events[-1]["execution_receipt"]
    assert replay_receipt["status"] == "BUDGET_BLOCKED"
    assert replay_receipt["replayed"] is True
    assert replay_receipt["node_id"] == rejected_node_id
    assert replay_receipt["attempt_id"] == rejected_attempt_id
    assert replay_receipt["projection"]["parallel_settlement"] == {
        "graph_status": "BLOCKED",
        "decision_ref": decision_ref,
    }
    assert rejected_usage_id in replay_receipt["evidence_refs"]
    assert decision_ref in replay_receipt["evidence_refs"]
    assert sorted(calls) == ["left", "right"]

    with sqlite3.connect(db_path) as conn:
        settlement_counts = dict(
            conn.execute(
                """
                SELECT event_type, COUNT(*)
                FROM task_runtime_outbox
                WHERE event_type IN (
                    'TASK_GRAPH_BLOCKED', 'BUDGET_RESERVATION_RELEASED'
                )
                GROUP BY event_type
                """
            ).fetchall()
        )
        decision_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0]
    assert settlement_counts == {
        "BUDGET_RESERVATION_RELEASED": 1,
        "TASK_GRAPH_BLOCKED": 1,
    }
    assert decision_count == 1


@pytest.mark.asyncio
async def test_ne01_b4_parallel_budget_selects_rejected_usage_not_admission_order(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    limited = replace(
        work_order,
        budget_policy=replace(
            work_order.budget_policy,
            limits=replace(
                work_order.budget_policy.limits,
                tool_call_count=0,
            ),
        ),
    )
    calls: list[str] = []
    forbidden_resolver = _FailingResolver()
    runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=forbidden_resolver,
        bounded_fanout_enabled=True,
    )
    original_precharge = runtime._budgets.precharge  # noqa: SLF001
    right_precharge_finished = asyncio.Event()
    precharge_order: list[str] = []

    async def right_first_precharge(
        context: Any,
        *,
        node_id: str,
        attempt_id: str,
        tool_call_count: int = 1,
    ) -> Any:
        if node_id == "left":
            precharge_order.append("left:waiting")
            await asyncio.wait_for(right_precharge_finished.wait(), timeout=2.0)
            precharge_order.append("left:after-right")
            return await original_precharge(
                context,
                node_id=node_id,
                attempt_id=attempt_id,
                tool_call_count=tool_call_count,
            )
        assert node_id == "right"
        precharge_order.append("right:charging")
        try:
            charge = await original_precharge(
                context,
                node_id=node_id,
                attempt_id=attempt_id,
                tool_call_count=tool_call_count,
            )
            assert charge.accepted is False
            precharge_order.append("right:rejected")
            return charge
        finally:
            right_precharge_finished.set()

    runtime._budgets.precharge = right_first_precharge  # type: ignore[method-assign]  # noqa: SLF001
    events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=runtime,
        ).execute_stream(
            command=command,
            work_order=limited,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert precharge_order == [
        "left:waiting",
        "right:charging",
        "right:rejected",
        "left:after-right",
    ]
    assert calls == []
    assert forbidden_resolver.resolve_count == 0
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    snapshot = store.read_scheduler_snapshot(graph.graph_id, graph.revision)
    rejected_usages = [
        row for row in snapshot["budget_usage"] if row["accepted"] is False
    ]
    assert len(snapshot["budget_usage"]) == 1
    assert len(rejected_usages) == 1
    rejected_usage = rejected_usages[0]
    assert rejected_usage["node_id"] == "right"
    right_attempt_id = rejected_usage["attempt_id"]
    rejected_usage_id = rejected_usage["usage_id"]

    receipt = events[-1]["execution_receipt"]
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["accepted"] is False
    assert receipt["node_id"] == "right"
    assert receipt["attempt_id"] == right_attempt_id
    assert rejected_usage_id in receipt["evidence_refs"]
    assert decision_ref in receipt["evidence_refs"]
    assert store.get_graph(graph.graph_id, graph.revision)["status"] == "BLOCKED"
    assert snapshot["budgets"][0]["status"] == "RELEASED"

    decision = store.get_parallel_settlement_decision(decision_ref)
    payload = decision["payload"]
    assert payload["decision_kind"] == "BUDGET"
    assert payload["selected_node_id"] == "right"
    selected_outcome = next(
        outcome
        for outcome in payload["outcomes"]
        if outcome["node_id"] == payload["selected_node_id"]
    )
    assert selected_outcome["attempt_id"] == right_attempt_id
    assert selected_outcome["status"] == "BUDGET_BLOCKED"
    assert selected_outcome["accepted"] is False
    assert rejected_usage_id in selected_outcome["evidence_refs"]


@pytest.mark.asyncio
async def test_ne01_b4_right_first_budget_decision_is_stable_across_crash_reentry(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    limited = replace(
        work_order,
        budget_policy=replace(
            work_order.budget_policy,
            limits=replace(
                work_order.budget_policy.limits,
                tool_call_count=0,
            ),
        ),
    )
    calls: list[str] = []
    first_resolver = _FailingResolver()
    first_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=first_resolver,
        bounded_fanout_enabled=True,
    )
    original_precharge = first_runtime._budgets.precharge  # noqa: SLF001
    right_precharge_finished = asyncio.Event()
    precharge_order: list[str] = []

    async def right_first_precharge(
        context: Any,
        *,
        node_id: str,
        attempt_id: str,
        tool_call_count: int = 1,
    ) -> Any:
        if node_id == "left":
            precharge_order.append("left:waiting")
            await asyncio.wait_for(right_precharge_finished.wait(), timeout=2.0)
            precharge_order.append("left:after-right")
            return await original_precharge(
                context,
                node_id=node_id,
                attempt_id=attempt_id,
                tool_call_count=tool_call_count,
            )
        assert node_id == "right"
        precharge_order.append("right:charging")
        try:
            charge = await original_precharge(
                context,
                node_id=node_id,
                attempt_id=attempt_id,
                tool_call_count=tool_call_count,
            )
            assert charge.accepted is False
            precharge_order.append("right:rejected")
            return charge
        finally:
            right_precharge_finished.set()

    first_runtime._budgets.precharge = right_first_precharge  # type: ignore[method-assign]  # noqa: SLF001
    live_decisions: list[tuple[str, dict[str, Any]]] = []
    first_settlement_calls = 0

    def crash_before_budget_settlement(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal first_settlement_calls
        first_settlement_calls += 1
        live_decisions.append(
            (
                kwargs["decision_ref"],
                json.loads(
                    json.dumps(
                        kwargs["decision_payload"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                ),
            )
        )
        raise TimeoutError("simulated crash before right-first Budget settlement")

    first_runtime._store.settle_parallel_graph_budget = crash_before_budget_settlement  # type: ignore[method-assign]  # noqa: SLF001
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=first_runtime,
    )
    with pytest.raises(GovernedRuntimeError):
        _ = [
            event
            async for event in first_app.execute_stream(
                command=command,
                work_order=limited,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert precharge_order == [
        "left:waiting",
        "right:charging",
        "right:rejected",
        "left:after-right",
    ]
    assert first_settlement_calls == 2
    assert first_resolver.resolve_count == 0
    assert calls == []
    crashed_store = DurableTaskStore(db_path, clock=lambda: NOW)
    crashed_snapshot = crashed_store.read_scheduler_snapshot(graph.graph_id, 1)
    rejected_usages = [
        row for row in crashed_snapshot["budget_usage"] if row["accepted"] is False
    ]
    assert len(crashed_snapshot["budget_usage"]) == 1
    assert len(rejected_usages) == 1
    rejected_usage = rejected_usages[0]
    assert rejected_usage["node_id"] == "right"
    assert crashed_snapshot["graph_record"]["status"] == "ACTIVE"
    assert crashed_snapshot["budgets"][0]["status"] == "EXCEEDED"

    reentry_resolver = _FailingResolver()
    reentry_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=reentry_resolver,
        bounded_fanout_enabled=True,
    )
    committed_settlement = reentry_runtime._store.settle_parallel_graph_budget  # noqa: SLF001
    reentry_decisions: list[tuple[str, dict[str, Any]]] = []
    reentry_settlement_calls = 0

    def commit_reentry_budget_settlement(
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal reentry_settlement_calls
        reentry_settlement_calls += 1
        reentry_decisions.append(
            (
                kwargs["decision_ref"],
                json.loads(
                    json.dumps(
                        kwargs["decision_payload"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                ),
            )
        )
        return committed_settlement(*args, **kwargs)

    reentry_runtime._store.settle_parallel_graph_budget = commit_reentry_budget_settlement  # type: ignore[method-assign]  # noqa: SLF001
    reentry_events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=reentry_runtime,
        ).execute_stream(
            command=command,
            work_order=limited,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert reentry_settlement_calls == 1
    assert reentry_resolver.resolve_count == 0
    assert calls == []
    receipt = reentry_events[-1]["execution_receipt"]
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["node_id"] == "right"
    assert receipt["attempt_id"] == rejected_usage["attempt_id"]
    assert rejected_usage["usage_id"] in receipt["evidence_refs"]

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    persisted = store.get_parallel_settlement_decision(decision_ref)
    persisted_payload = persisted["payload"]
    expected_decision = (decision_ref, persisted_payload)
    assert live_decisions == [expected_decision, expected_decision]
    assert reentry_decisions == [expected_decision]
    assert persisted["decision_ref"] == decision_ref
    assert persisted_payload["decision_kind"] == "BUDGET"
    assert persisted_payload["selected_node_id"] == "right"
    outcomes = {
        outcome["node_id"]: outcome for outcome in persisted_payload["outcomes"]
    }
    assert outcomes["right"]["status"] == "BUDGET_BLOCKED"
    assert outcomes["right"]["attempt_id"] == rejected_usage["attempt_id"]
    assert rejected_usage["usage_id"] in outcomes["right"]["evidence_refs"]
    assert outcomes["left"]["status"] == "FAILED"
    assert outcomes["left"]["accepted"] is False


@pytest.mark.asyncio
async def test_ne01_b4_later_batch_failure_decision_includes_prior_accepted_batch(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (
            ("seed", ()),
            ("left", ("seed",)),
            ("right", ("seed",)),
            ("join", ("left", "right")),
        ),
        concurrency=2,
    )
    calls: list[str] = []
    worker = _Worker(calls, set())
    acceptance = _Acceptance(decisions_by_node={"left": "REVIEW"})
    first_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
        acceptance=acceptance,
        bounded_fanout_enabled=True,
    )
    live_decisions: list[tuple[str, dict[str, Any]]] = []
    first_settlement_calls = 0

    def crash_before_later_batch_settlement(
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal first_settlement_calls
        first_settlement_calls += 1
        live_decisions.append(_parallel_decision_argument_snapshot(kwargs))
        raise TimeoutError("simulated crash before later-batch settlement")

    first_runtime._store.settle_parallel_graph_budget = crash_before_later_batch_settlement  # type: ignore[method-assign]  # noqa: SLF001
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=first_runtime,
    )
    with pytest.raises(GovernedRuntimeError):
        _ = [
            event
            async for event in first_app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    assert first_settlement_calls == 2
    assert calls == ["seed", "left"]
    crashed_store = DurableTaskStore(db_path, clock=lambda: NOW)
    crashed_snapshot = crashed_store.read_scheduler_snapshot("graph-gr03a", 1)
    crashed_statuses = {
        node["node_id"]: node["status"]
        for node in crashed_snapshot["graph_record"]["nodes"]
    }
    assert crashed_snapshot["graph_record"]["status"] == "ACTIVE"
    assert crashed_snapshot["budgets"][0]["status"] == "ACTIVE"
    assert crashed_statuses == {
        "seed": "ACCEPTED",
        "left": "WAITING_ACCEPTANCE_REVIEW",
        "right": "FAILED",
        "join": "BLOCKED",
    }

    reentry_resolver = _FailingResolver()
    reentry_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=reentry_resolver,
        acceptance=acceptance,
        bounded_fanout_enabled=True,
    )
    committed_settlement = reentry_runtime._store.settle_parallel_graph_budget  # noqa: SLF001
    reentry_decisions: list[tuple[str, dict[str, Any]]] = []
    reentry_settlement_calls = 0

    def commit_later_batch_reentry(
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal reentry_settlement_calls
        reentry_settlement_calls += 1
        reentry_decisions.append(_parallel_decision_argument_snapshot(kwargs))
        return committed_settlement(*args, **kwargs)

    reentry_runtime._store.settle_parallel_graph_budget = commit_later_batch_reentry  # type: ignore[method-assign]  # noqa: SLF001
    events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=reentry_runtime,
        ).execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]

    assert reentry_settlement_calls == 1
    assert reentry_resolver.resolve_count == 0
    assert calls == ["seed", "left"]
    receipt = events[-1]["execution_receipt"]
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert receipt["status"] == "FAILED"
    assert receipt["node_id"] == "right"

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    persisted = store.get_parallel_settlement_decision(decision_ref)
    expected_decision = (decision_ref, persisted["payload"])
    assert live_decisions == [expected_decision, expected_decision]
    assert reentry_decisions == [expected_decision]
    assert persisted["decision_ref"] == decision_ref
    payload = persisted["payload"]
    assert payload["decision_kind"] == "FAILURE"
    assert payload["selected_node_id"] == "right"
    outcomes = {outcome["node_id"]: outcome for outcome in payload["outcomes"]}
    assert list(outcomes) == ["left", "right", "seed"]
    assert outcomes["seed"]["status"] == "ACCEPTED"
    assert outcomes["seed"]["accepted"] is True
    assert outcomes["left"]["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert outcomes["right"]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_ne01_b4_failed_batch_decision_keeps_retry_pending_sibling(
    tmp_path: Path,
) -> None:
    class _RetryLeftFailRightResolver:
        def __init__(self) -> None:
            self.resolve_counts = {"left": 0, "right": 0}

        def resolve(self, *, executor: str, worker_route: str) -> _Worker:
            assert executor == "canonical"
            node_id = worker_route.removeprefix("worker:")
            self.resolve_counts[node_id] += 1
            if node_id == "left":
                raise RetryableWorkerPreparationError(
                    "WORKER_PREPARATION_TRANSIENT",
                    "left worker is temporarily unavailable",
                )
            raise RuntimeError("right worker registry failed before reservation")

    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    graph = replace(
        graph,
        nodes=tuple(
            replace(
                node,
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 60_000},
            )
            if node.node_id == "left"
            else node
            for node in graph.nodes
        ),
    )
    first_resolver = _RetryLeftFailRightResolver()
    calls: list[str] = []
    first_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=first_resolver,
        bounded_fanout_enabled=True,
    )
    live_decisions: list[tuple[str, dict[str, Any]]] = []
    first_settlement_calls = 0

    def crash_before_retry_sibling_settlement(
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal first_settlement_calls
        first_settlement_calls += 1
        live_decisions.append(_parallel_decision_argument_snapshot(kwargs))
        raise TimeoutError("simulated crash before retry-sibling settlement")

    first_runtime._store.settle_parallel_graph_budget = crash_before_retry_sibling_settlement  # type: ignore[method-assign]  # noqa: SLF001
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=first_runtime,
    )
    with pytest.raises(GovernedRuntimeError):
        _ = [
            event
            async for event in first_app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]

    assert first_settlement_calls == 2
    assert first_resolver.resolve_counts == {"left": 1, "right": 1}
    assert calls == []
    crashed_store = DurableTaskStore(db_path, clock=lambda: NOW)
    crashed_snapshot = crashed_store.read_scheduler_snapshot(graph.graph_id, 1)
    crashed_statuses = {
        node["node_id"]: node["status"]
        for node in crashed_snapshot["graph_record"]["nodes"]
    }
    assert crashed_snapshot["graph_record"]["status"] == "ACTIVE"
    assert crashed_snapshot["budgets"][0]["status"] == "ACTIVE"
    assert crashed_statuses == {
        "left": "RETRY_PENDING",
        "right": "FAILED",
        "join": "BLOCKED",
    }
    assert [
        (attempt["node_id"], attempt["status"])
        for attempt in crashed_snapshot["attempts"]
    ] == [("left", "FAILED"), ("right", "FAILED")]

    reentry_resolver = _FailingResolver()
    reentry_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=reentry_resolver,
        bounded_fanout_enabled=True,
    )
    committed_settlement = reentry_runtime._store.settle_parallel_graph_budget  # noqa: SLF001
    reentry_decisions: list[tuple[str, dict[str, Any]]] = []
    reentry_settlement_calls = 0

    def commit_retry_sibling_reentry(
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal reentry_settlement_calls
        reentry_settlement_calls += 1
        reentry_decisions.append(_parallel_decision_argument_snapshot(kwargs))
        return committed_settlement(*args, **kwargs)

    reentry_runtime._store.settle_parallel_graph_budget = commit_retry_sibling_reentry  # type: ignore[method-assign]  # noqa: SLF001
    events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=reentry_runtime,
        ).execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]

    assert reentry_settlement_calls == 1
    assert reentry_resolver.resolve_count == 0
    assert calls == []
    receipt = events[-1]["execution_receipt"]
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert receipt["status"] == "FAILED"
    assert receipt["node_id"] == "right"

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    persisted = store.get_parallel_settlement_decision(decision_ref)
    expected_decision = (decision_ref, persisted["payload"])
    assert live_decisions == [expected_decision, expected_decision]
    assert reentry_decisions == [expected_decision]
    assert persisted["decision_ref"] == decision_ref
    payload = persisted["payload"]
    assert payload["decision_kind"] == "FAILURE"
    assert payload["selected_node_id"] == "right"
    outcomes = {outcome["node_id"]: outcome for outcome in payload["outcomes"]}
    assert outcomes["left"]["status"] == "RETRY_PENDING"
    assert outcomes["left"]["accepted"] is False
    assert outcomes["right"]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_ne01_parallel_review_blocks_graph_after_all_node_outcomes(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        node_delays={"left": 0.05},
    )
    review_store = ReviewStore(db_path, clock=lambda: NOW)
    review_runtime = TaskNodeReviewRuntime(review_store, clock=lambda: NOW)
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            acceptance=_Acceptance(decisions_by_node={"right": "REVIEW"}),
            review_runtime=review_runtime,
            bounded_fanout_enabled=True,
        ),
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

    receipt = events[-1]["execution_receipt"]
    assert receipt["node_id"] == "right"
    assert receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert receipt["projection"]["parallel_settlement"]["graph_status"] == "BLOCKED"
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert decision_ref.startswith("taskbatch://sha256/")
    assert decision_ref in receipt["evidence_refs"]
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "ACCEPTED"
    assert (
        store.get_node("graph-gr03a", 1, "right")["status"]
        == "WAITING_ACCEPTANCE_REVIEW"
    )
    assert store.get_graph("graph-gr03a", 1)["status"] == "BLOCKED"
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["budgets"][0]["status"] == "ACTIVE"
    assert "join" not in calls
    with sqlite3.connect(db_path) as conn:
        review_settlement = conn.execute(
            """
            SELECT attempt_id, evidence_refs_json FROM task_runtime_outbox
            WHERE event_type = 'TASK_GRAPH_BLOCKED'
            """
        ).fetchall()
        budget_release_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'BUDGET_RESERVATION_RELEASED'
            """
        ).fetchone()[0]
    assert len(review_settlement) == 1
    assert review_settlement[0][0] is None
    assert decision_ref in json.loads(review_settlement[0][1])
    assert budget_release_count == 0

    review = receipt["projection"]["review"]
    assert review["review_status"] == "PENDING"
    assert review["resume_phase"] == ReviewResumePhase.POST_EXECUTION_ACCEPTANCE.value

    pending_replay = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ][-1]["execution_receipt"]
    assert pending_replay["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert pending_replay["node_id"] == "right"
    assert pending_replay["attempt_id"] == receipt["attempt_id"]
    assert pending_replay["replayed"] is True
    assert pending_replay["projection"]["parallel_settlement"] == {
        "graph_status": "BLOCKED",
        "decision_ref": decision_ref,
    }
    assert decision_ref in pending_replay["evidence_refs"]
    assert sorted(calls) == ["left", "right"]

    rejected = review_store.reject_review(
        review["review_id"],
        actor_id="gr03-reviewer",
    )
    assert rejected["review"]["status"] == "REJECTED"
    denied_receipt = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ][-1]["execution_receipt"]
    assert denied_receipt["status"] == "REVIEW_DENIED"
    assert denied_receipt["node_id"] == "right"
    assert "parallel_settlement" not in denied_receipt["projection"]
    assert decision_ref not in denied_receipt["evidence_refs"]
    assert store.get_graph("graph-gr03a", 1)["status"] == "FAILED"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "FAILED"
    assert store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "RELEASED"
    assert sorted(calls) == ["left", "right"]

    failed_replay = [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ][-1]["execution_receipt"]
    assert failed_replay["status"] == "FAILED"
    assert failed_replay["node_id"] == "right"
    assert failed_replay["replayed"] is True
    assert "parallel_settlement" not in failed_replay["projection"]
    assert decision_ref not in failed_replay["evidence_refs"]
    assert sorted(calls) == ["left", "right"]
    latest_decision = store.get_latest_parallel_settlement_decision("graph-gr03a", 1)
    assert latest_decision is not None
    assert latest_decision["decision_ref"] == decision_ref
    assert latest_decision["payload"]["graph_target_status"] == "BLOCKED"


@pytest.mark.parametrize("lose_reentry_settlement_response", (False, True))
@pytest.mark.asyncio
async def test_ne01_b4_crash_reentry_failure_beats_review_and_persists_decision(
    tmp_path: Path,
    lose_reentry_settlement_response: bool,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=False,
        node_delays={"left": 0.05},
    )
    acceptance = _Acceptance(decisions_by_node={"left": "REVIEW"})
    first_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
        acceptance=acceptance,
        bounded_fanout_enabled=True,
    )
    first_settlement_calls = 0
    first_settlement_decisions: list[tuple[str, dict[str, Any]]] = []

    def crash_before_settlement(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal first_settlement_calls
        first_settlement_calls += 1
        first_settlement_decisions.append(
            (
                kwargs["decision_ref"],
                json.loads(
                    json.dumps(
                        kwargs["decision_payload"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                ),
            )
        )
        raise TimeoutError("simulated process crash before outer settlement commit")

    first_runtime._store.settle_parallel_graph_budget = crash_before_settlement  # type: ignore[method-assign]  # noqa: SLF001
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=first_runtime,
    )

    with pytest.raises(GovernedRuntimeError):
        _ = [
            event
            async for event in first_app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    assert first_settlement_calls == 2
    assert calls == ["left"]
    crashed_store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert crashed_store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert crashed_store.get_node("graph-gr03a", 1, "left")["status"] == (
        "WAITING_ACCEPTANCE_REVIEW"
    )
    assert crashed_store.get_node("graph-gr03a", 1, "right")["status"] == "FAILED"
    assert crashed_store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "ACTIVE"

    reentry_runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
        acceptance=acceptance,
        bounded_fanout_enabled=True,
    )
    committed_settlement = reentry_runtime._store.settle_parallel_graph_budget  # noqa: SLF001
    reentry_settlement_calls = 0
    reentry_settlement_decisions: list[tuple[str, dict[str, Any]]] = []

    def settle_reentry(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal reentry_settlement_calls
        reentry_settlement_calls += 1
        reentry_settlement_decisions.append(
            (
                kwargs["decision_ref"],
                json.loads(
                    json.dumps(
                        kwargs["decision_payload"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                ),
            )
        )
        result = committed_settlement(*args, **kwargs)
        if lose_reentry_settlement_response and reentry_settlement_calls == 1:
            raise TimeoutError("simulated committed re-entry settlement response loss")
        return result

    reentry_runtime._store.settle_parallel_graph_budget = settle_reentry  # type: ignore[method-assign]  # noqa: SLF001
    reentry_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=reentry_runtime,
    )
    events = [
        event
        async for event in reentry_app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "FAILED"
    assert receipt["node_id"] == "right"
    assert receipt["accepted"] is False
    settlement = receipt["projection"]["parallel_settlement"]
    assert settlement["graph_status"] == "FAILED"
    decision_ref = settlement["decision_ref"]
    assert decision_ref.startswith("taskbatch://sha256/")
    assert decision_ref in receipt["evidence_refs"]
    assert calls == ["left"]
    assert reentry_settlement_calls == (
        2 if lose_reentry_settlement_response else 1
    )

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_graph("graph-gr03a", 1)["status"] == "FAILED"
    assert store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "RELEASED"
    with sqlite3.connect(db_path) as conn:
        terminal_counts = dict(
            conn.execute(
                """
                SELECT event_type, COUNT(*)
                FROM task_runtime_outbox
                WHERE event_type IN (
                    'TASK_GRAPH_FAILED', 'TASK_GRAPH_BLOCKED',
                    'BUDGET_RESERVATION_RELEASED'
                )
                GROUP BY event_type
                """
            ).fetchall()
        )
        decision_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0]
    assert terminal_counts == {
        "BUDGET_RESERVATION_RELEASED": 1,
        "TASK_GRAPH_FAILED": 1,
    }
    assert decision_count == 1

    decision = store.get_parallel_settlement_decision(decision_ref)
    parsed_payload = json.loads(decision["payload_json"])
    canonical_payload_json = json.dumps(
        parsed_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(canonical_payload_json.encode("utf-8")).hexdigest()
    assert decision["payload"] == parsed_payload
    assert decision["payload_json"] == canonical_payload_json
    assert decision["payload_sha256"] == f"sha256:{digest}"
    assert decision["decision_ref"] == decision_ref == f"taskbatch://sha256/{digest}"
    assert parsed_payload["decision_kind"] == "FAILURE"
    assert parsed_payload["selected_node_id"] == "right"
    assert parsed_payload["graph_target_status"] == "FAILED"
    assert parsed_payload["budget_target_status"] == "RELEASED"
    assert parsed_payload["node_states"] == {
        "join": {"status": "BLOCKED", "status_version": 0},
        "left": {"status": "WAITING_ACCEPTANCE_REVIEW", "status_version": 6},
        "right": {"status": "FAILED", "status_version": 3},
    }
    assert [outcome["node_id"] for outcome in parsed_payload["outcomes"]] == [
        "left",
        "right",
    ]
    expected_decision = (decision_ref, parsed_payload)
    assert first_settlement_decisions == [expected_decision, expected_decision]
    assert reentry_settlement_decisions == [expected_decision] * (
        2 if lose_reentry_settlement_response else 1
    )

    replay_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
            acceptance=acceptance,
        ),
    )
    replay_events = [
        event
        async for event in replay_app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    replay_receipt = replay_events[-1]["execution_receipt"]
    assert replay_receipt["status"] == "FAILED"
    assert replay_receipt["node_id"] == "right"
    assert replay_receipt["replayed"] is True
    assert replay_receipt["projection"]["parallel_settlement"] == {
        "graph_status": "FAILED",
        "decision_ref": decision_ref,
    }
    assert decision_ref in replay_receipt["evidence_refs"]
    assert calls == ["left"]


@pytest.mark.asyncio
async def test_ne01_b4_parallel_settlement_ownership_conflict_maps_to_in_progress(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=False,
        node_delays={"left": 0.05},
    )
    runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
        bounded_fanout_enabled=True,
    )
    settlement_calls = 0

    def concurrent_owner_wins(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal settlement_calls
        settlement_calls += 1
        raise TaskStoreError(
            TASK_STORE_OWNERSHIP_CONFLICT,
            "another coordinator owns the parallel settlement fence",
        )

    runtime._store.settle_parallel_graph_budget = concurrent_owner_wins  # type: ignore[method-assign]  # noqa: SLF001
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=runtime,
    )

    with pytest.raises(GovernedRuntimeError) as in_progress:
        _ = [
            event
            async for event in app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    assert in_progress.value.reason_code == "GOVERNED_EXECUTION_IN_PROGRESS"
    assert settlement_calls == 1
    assert calls == ["left"]
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "ACCEPTED"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "FAILED"
    assert store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "ACTIVE"
    with sqlite3.connect(db_path) as conn:
        outer_terminal_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM task_runtime_outbox
            WHERE event_type IN (
                'TASK_GRAPH_FAILED', 'TASK_GRAPH_BLOCKED',
                'BUDGET_RESERVATION_RELEASED'
            )
            """
        ).fetchone()[0]
        decision_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0]
    assert outer_terminal_count == 0
    assert decision_count == 0


@pytest.mark.parametrize(
    ("claim_sibling", "expected_reason", "expected_settlement_calls"),
    (
        (True, "GOVERNED_EXECUTION_IN_PROGRESS", 1),
        (False, "GOVERNED_DURABLE_STATE_CONFLICT", 2),
    ),
)
@pytest.mark.asyncio
async def test_ne01_b4_parallel_settlement_cas_maps_only_live_authority_to_in_progress(
    tmp_path: Path,
    claim_sibling: bool,
    expected_reason: str,
    expected_settlement_calls: int,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left",))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=False,
        node_delays={"left": 0.05},
    )
    runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
        bounded_fanout_enabled=True,
    )
    store = runtime._store  # noqa: SLF001
    settlement_calls = 0
    observed_expected_join_states: list[dict[str, Any]] = []
    competing_attempt_id = "attempt::gr03a::cas-race-join"

    def cas_after_settlement_snapshot(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal settlement_calls
        settlement_calls += 1
        observed_expected_join_states.append(
            dict(kwargs["expected_node_states"]["join"])
        )
        if claim_sibling and settlement_calls == 1:
            expected_join = kwargs["expected_node_states"]["join"]
            assert expected_join["status"] == "BLOCKED"
            join = store.get_node("graph-gr03a", 1, "join")
            assert join["status"] == "BLOCKED"
            ready = store.transition_node(
                "graph-gr03a",
                1,
                "join",
                expected_version=join["status_version"],
                target_status="READY",
                outbox=_runtime_outbox(
                    "TASK_NODE",
                    "graph-gr03a@1/join",
                    join["status_version"] + 1,
                    "TASK_NODE_READY",
                ),
            )
            claimed = store.claim_ready_node(
                attempt_id=competing_attempt_id,
                graph_id="graph-gr03a",
                graph_revision=1,
                node_id="join",
                expected_node_version=ready["status_version"],
                expected_attempt_count=0,
                owner_id="gr03a-competing-worker",
                lease_ttl_ms=120_000,
                budget_id=kwargs["budget_id"],
                operation_scope="gr03a-cas-race:join",
                operation_fingerprint=_sha("gr03a-cas-race:join"),
                attempt_created_outbox=_runtime_outbox(
                    "TASK_ATTEMPT",
                    competing_attempt_id,
                    0,
                    "TASK_ATTEMPT_CREATED",
                    attempt_id=competing_attempt_id,
                ),
                attempt_leased_outbox=_runtime_outbox(
                    "TASK_ATTEMPT",
                    competing_attempt_id,
                    1,
                    "TASK_ATTEMPT_LEASED",
                    attempt_id=competing_attempt_id,
                ),
            )
            assert claimed["node"]["status"] == "LEASED"
            assert claimed["attempt"]["status"] == "LEASED"
            assert claimed["lease"]["status"] == "ACTIVE"
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            "parallel settlement snapshot changed after it was read",
        )

    runtime._store.settle_parallel_graph_budget = cas_after_settlement_snapshot  # type: ignore[method-assign]  # noqa: SLF001
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=runtime,
    )

    with pytest.raises(GovernedRuntimeError) as raised:
        _ = [
            event
            async for event in app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    assert raised.value.reason_code == expected_reason
    assert settlement_calls == expected_settlement_calls
    assert all(
        expected == {"status": "BLOCKED", "status_version": 0}
        for expected in observed_expected_join_states
    )
    assert calls == ["left"]
    snapshot = DurableTaskStore(db_path, clock=lambda: NOW).read_scheduler_snapshot(
        "graph-gr03a",
        1,
    )
    assert snapshot["graph_record"]["status"] == "ACTIVE"
    assert snapshot["budgets"][0]["status"] == "ACTIVE"
    if claim_sibling:
        node_statuses = {
            row["node_id"]: row["status"]
            for row in snapshot["graph_record"]["nodes"]
        }
        assert node_statuses["join"] == "LEASED"
        active_attempts = [
            row
            for row in snapshot["attempts"]
            if row["attempt_id"] == competing_attempt_id
        ]
        assert len(active_attempts) == 1
        assert active_attempts[0]["status"] == "LEASED"
        active_leases = [
            row
            for row in snapshot["active_leases"]
            if row["attempt_id"] == competing_attempt_id
        ]
        assert len(active_leases) == 1
        assert active_leases[0]["status"] == "ACTIVE"
    else:
        assert snapshot["active_leases"] == []
        assert all(
            row["status"] not in {"CREATED", "LEASED", "RUNNING"}
            for row in snapshot["attempts"]
        )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_ne01_b4_terminal_replay_rejects_newer_selected_attempt(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    graph = replace(
        graph,
        nodes=tuple(
            replace(
                node,
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 0},
            )
            if node.node_id == "right"
            else node
            for node in graph.nodes
        ),
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=False,
        node_delays={"left": 0.05},
    )
    events = [
        event
        async for event in GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
            coordinator=_coordinator(
                db_path,
                calls,
                worker_resolver=_FailingNodeResolver(worker, fail_node_id="right"),
                bounded_fanout_enabled=True,
            ),
        ).execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
    ]
    receipt = events[-1]["execution_receipt"]
    decision_ref = receipt["projection"]["parallel_settlement"]["decision_ref"]
    assert receipt["status"] == "FAILED"
    assert receipt["node_id"] == "right"
    assert calls == ["left"]

    store = DurableTaskStore(db_path, clock=lambda: NOW)
    graph_before = store.get_graph(graph.graph_id, graph.revision)
    historical_before = store.get_parallel_settlement_decision(decision_ref)
    selected = next(
        outcome
        for outcome in historical_before["payload"]["outcomes"]
        if outcome["node_id"] == historical_before["payload"]["selected_node_id"]
    )
    assert selected["node_id"] == "right"
    assert selected["status"] == "FAILED"

    later_attempt_id = "attempt::gr03a::right::late-failed"
    later_lease_id = "lease::gr03a::right::late-failed"
    with sqlite3.connect(db_path) as conn:
        prior_attempt = conn.execute(
            """
            SELECT * FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = 'right'
            ORDER BY attempt_no DESC, attempt_id DESC
            LIMIT 1
            """,
            (graph.graph_id, graph.revision),
        ).fetchone()
        assert prior_attempt is not None
        prior_attempt = tuple(prior_attempt)
        prior_columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(task_runtime_attempt)")
        ]
        prior = dict(zip(prior_columns, prior_attempt, strict=True))
        assert prior["attempt_id"] == selected["attempt_id"]
        assert prior["attempt_no"] == 1
        assert prior["status"] == "FAILED"
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
            ) VALUES (?, ?, ?, 'right', 2, 'FAILED', 3, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                later_attempt_id,
                graph.graph_id,
                graph.revision,
                prior["owner_id"],
                prior["operation_scope"],
                prior["operation_fingerprint"],
                NOW + 10,
                NOW + 10,
                NOW + 10,
                NOW + 11,
                NOW + 11,
                "taskerror://gr03a/right/late-failed",
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                released_ts_ms, status, status_version
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'RELEASED', 1)
            """,
            (
                later_lease_id,
                later_attempt_id,
                prior["owner_id"],
                NOW + 10,
                NOW + 10,
                NOW + 120_000,
                NOW + 11,
            ),
        )
        conn.commit()

    graph_after = store.get_graph(graph.graph_id, graph.revision)
    assert graph_after["status"] == graph_before["status"] == "FAILED"
    assert graph_after["status_version"] == graph_before["status_version"]
    historical_after = store.get_parallel_settlement_decision(decision_ref)
    assert historical_after["decision_ref"] == decision_ref
    assert historical_after["payload"] == historical_before["payload"]
    latest_historical = store.get_latest_parallel_settlement_decision(
        graph.graph_id,
        graph.revision,
    )
    assert latest_historical is not None
    assert latest_historical["decision_ref"] == decision_ref

    replay_resolver = _FailingResolver()
    replay_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=replay_resolver,
        ),
    )
    with pytest.raises(GovernedRuntimeError) as drift:
        _ = [
            event
            async for event in replay_app.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        ]
    assert drift.value.reason_code == "GOVERNED_DURABLE_STATE_CONFLICT"
    assert "selected Attempt is not latest" in drift.value.detail
    assert replay_resolver.resolve_count == 0
    assert calls == ["left"]


@pytest.mark.parametrize("lose_retry_response", (False, True))
@pytest.mark.asyncio
async def test_ne01_parallel_retry_uses_node_local_evidence_and_exact_replay(
    tmp_path: Path,
    lose_retry_response: bool,
) -> None:
    class _RetryLeftOnceResolver:
        def __init__(self, worker: _Worker) -> None:
            self._worker = worker
            self.left_resolve_count = 0

        def resolve(self, *, executor: str, worker_route: str) -> _Worker:
            assert executor == "canonical"
            if worker_route == "worker:left":
                self.left_resolve_count += 1
                if self.left_resolve_count == 1:
                    raise RetryableWorkerPreparationError(
                        "WORKER_PREPARATION_TRANSIENT",
                        "left worker registry is warming before reservation",
                    )
            return self._worker

    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    graph = replace(
        graph,
        nodes=tuple(
            replace(
                node,
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 100},
            )
            if node.node_id == "left"
            else node
            for node in graph.nodes
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
    resolver = _RetryLeftOnceResolver(_Worker(calls, set()))
    runtime = _coordinator(
        db_path,
        calls,
        worker_resolver=resolver,
        bounded_fanout_enabled=True,
    )
    retry_calls = 0
    if lose_retry_response:
        committed_retry = runtime._store.schedule_retry_node_outcome  # noqa: SLF001

        def lose_first_committed_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal retry_calls
            retry_calls += 1
            result = committed_retry(*args, **kwargs)
            if retry_calls == 1:
                raise TimeoutError("simulated node-only retry response loss")
            return result

        runtime._store.schedule_retry_node_outcome = lose_first_committed_response  # type: ignore[method-assign]  # noqa: SLF001

    events = [
        event
        async for event in runtime.execute_stream(
            admitted=admitted,
            command=record["command"],
            work_order=record["work_order"],
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["node_id"] == "left"
    assert receipt["status"] == "RETRY_PENDING"
    assert receipt["accepted"] is False
    assert receipt["projection"]["parallel_settlement"] == {
        "graph_status": "ACTIVE",
        "decision_ref": None,
    }
    assert retry_calls == (2 if lose_retry_response else 0)
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "RETRY_PENDING"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "ACCEPTED"
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert snapshot["budgets"][0]["status"] == "ACTIVE"
    retry_usages = [
        row for row in snapshot["budget_usage"] if row["charge_kind"] == "RETRY"
    ]
    assert len(retry_usages) == 1
    attempt = next(row for row in snapshot["attempts"] if row["node_id"] == "left")
    expected_usage_id = "budget-usage://" + hashlib.sha256(
        (
            f"{snapshot['budgets'][0]['budget_id']}\n{attempt['attempt_id']}"
            "\nretry\nnode-only"
        ).encode("utf-8")
    ).hexdigest()
    assert retry_usages[0]["usage_id"] == expected_usage_id
    with sqlite3.connect(db_path) as conn:
        evidence_json = conn.execute(
            """
            SELECT evidence_refs_json FROM task_runtime_outbox
            WHERE aggregate_type = 'TASK_ATTEMPT'
              AND aggregate_id = ? AND event_type = 'TASK_ATTEMPT_FAILED'
            """,
            (attempt["attempt_id"],),
        ).fetchone()[0]
        graph_terminal_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type IN ('TASK_GRAPH_BLOCKED', 'TASK_GRAPH_FAILED')
            """
        ).fetchone()[0]
    assert expected_usage_id in json.loads(evidence_json)
    assert graph_terminal_count == 0
    assert "join" not in calls


@pytest.mark.asyncio
async def test_ne01_parallel_terminal_receipt_must_match_durable_node_state(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    runtime = _coordinator(
        db_path,
        calls,
        bounded_fanout_enabled=True,
    )
    execute_node_stream = runtime._node_execution.execute_stream  # noqa: SLF001

    async def forge_left_terminal(**kwargs: Any):
        async for raw_event in execute_node_stream(**kwargs):
            event = dict(raw_event)
            if bool(event.get("done")) and kwargs["admitted"].node_id == "left":
                receipt = dict(event["execution_receipt"])
                receipt.update(
                    {
                        "status": "FAILED",
                        "accepted": False,
                        "reason_code": "FORGED_PARALLEL_TERMINAL",
                    }
                )
                event["execution_receipt"] = receipt
            yield event

    # Test-only fault injection at the real scheduling owner. A constructor
    # factory remains preferable if this seam is needed outside this contract.
    runtime._dag_scheduling._node_execution = SimpleNamespace(  # type: ignore[assignment]  # noqa: SLF001
        execute_stream=forge_left_terminal
    )
    record = HostCommandRepository(db_path, clock=lambda: NOW).admit(*contracts)
    admitted = AdmittedExecution.from_contracts(
        command=record["command"],
        assignment=record["assignment"],
        graph=record["graph"],
    )

    with pytest.raises(GovernedRuntimeError) as error:
        _ = [
            event
            async for event in runtime.execute_stream(
                admitted=admitted,
                command=record["command"],
                work_order=record["work_order"],
            )
        ]

    assert error.value.reason_code == "GOVERNED_DURABLE_STATE_CONFLICT"
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    assert store.get_node("graph-gr03a", 1, "left")["status"] == "ACCEPTED"
    assert store.get_node("graph-gr03a", 1, "right")["status"] == "ACCEPTED"
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "ACTIVE"


@pytest.mark.asyncio
async def test_ne01_parallel_progress_applies_bounded_consumer_backpressure(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    progress_per_root = 20
    buffer_size = 1
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        progress_event_count=progress_per_root,
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
            fanout_event_buffer_size=buffer_size,
        ),
    )
    stream = app.execute_stream(
        command=contracts[0],
        work_order=contracts[1],
        assignment=contracts[2],
        graph=contracts[3],
    )

    try:
        first_event = await asyncio.wait_for(anext(stream), timeout=3.0)
        assert first_event["done"] is False
        await asyncio.sleep(0.05)
        emitted_while_consumer_paused = sum(probe.progress_events_emitted.values())
        # One delivered item, one buffered item and at most one blocked put per
        # active child.  The producers cannot run through all progress chunks.
        assert emitted_while_consumer_paused <= 1 + buffer_size + len(probe.root_nodes)
        remaining = [event async for event in stream]
    finally:
        await stream.aclose()

    assert remaining[-1]["execution_receipt"]["accepted"] is True
    assert sum(probe.progress_events_emitted.values()) == progress_per_root * 2
    assert worker.join_dependency_statuses == ("ACCEPTED", "ACCEPTED")


@pytest.mark.asyncio
async def test_ne01_bounded_fan_out_honours_single_concurrency_slot(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=1,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=False,
        root_delay_seconds=0.05,
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
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

    assert events[-1]["execution_receipt"]["accepted"] is True
    assert probe.entered_nodes == {"left", "right"}
    assert probe.peak_active == contracts[1].budget_policy.limits.concurrency == 1
    assert probe.active == 0
    assert worker.join_dependency_statuses == ("ACCEPTED", "ACCEPTED")
    assert calls[-1] == "join"


@pytest.mark.asyncio
async def test_ne01_parallel_progress_streams_before_all_roots_complete(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    progress_release = asyncio.Event()
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        progress_release=progress_release,
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
    )
    stream = app.execute_stream(
        command=contracts[0],
        work_order=contracts[1],
        assignment=contracts[2],
        graph=contracts[3],
    )

    try:
        first_event = await asyncio.wait_for(anext(stream), timeout=3.0)
        assert first_event["phase"] == "EXECUTE"
        assert first_event["done"] is False
        assert probe.entered_nodes == {"left", "right"}
        assert probe.completed_nodes == set()
        progress_release.set()
        remaining = [event async for event in stream]
    finally:
        progress_release.set()
        await stream.aclose()

    assert remaining[-1]["execution_receipt"]["accepted"] is True
    assert probe.completed_nodes == {"left", "right"}


@pytest.mark.asyncio
async def test_ne01_parallel_self_cancellation_reaches_supervisor_without_hang(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        block_until_cancelled=True,
        self_cancel_node_id="left",
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
    )

    async def consume() -> list[dict[str, Any]]:
        return [
            event
            async for event in app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    events = await asyncio.wait_for(consume(), timeout=3.0)
    await asyncio.sleep(0)

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert receipt["accepted"] is False
    assert probe.entered_nodes == {"left", "right"}
    assert probe.cancelled_nodes == {"left", "right"}
    assert probe.active == 0
    assert all(task.done() for task in probe.root_tasks.values())
    assert [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name().startswith("task-heartbeat:")
    ] == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_COMMITTED'
            """
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
            ("graph-gr03a", 1),
        ).fetchone()[0] == "ACTIVE"
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_graph_cancellation_intent"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT status FROM task_runtime_attempt WHERE node_id = ?",
            ("left",),
        ).fetchone()[0] == "RUNNING"
        assert conn.execute(
            """
            SELECT reservation.status
            FROM task_runtime_idempotency_reservation AS reservation
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = reservation.attempt_id
            WHERE attempt.node_id = ?
            """,
            ("left",),
        ).fetchone()[0] == "RESERVED"


@pytest.mark.asyncio
async def test_ne01_outer_cancellation_joins_fan_out_children_and_heartbeats(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("left", ()), ("right", ()), ("join", ("left", "right"))),
        concurrency=2,
    )
    calls: list[str] = []
    probe = _AsyncFanOutProbe(("left", "right"))
    worker = _FanOutWorker(
        calls,
        db_path=db_path,
        probe=probe,
        require_full_barrier=True,
        block_until_cancelled=True,
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=_coordinator(
            db_path,
            calls,
            worker_resolver=_StaticResolver(worker),
            bounded_fanout_enabled=True,
        ),
    )

    async def consume() -> None:
        _ = [
            event
            async for event in app.execute_stream(
                command=contracts[0],
                work_order=contracts[1],
                assignment=contracts[2],
                graph=contracts[3],
            )
        ]

    outer = asyncio.create_task(consume(), name="test-ne01-fan-out-consumer")
    all_started = False
    try:
        await asyncio.wait_for(probe.all_roots_started.wait(), timeout=3.0)
        all_started = True
    except TimeoutError:
        pass
    finally:
        outer.cancel()
        try:
            await outer
        except asyncio.CancelledError:
            pass
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert all_started is True
    assert probe.entered_nodes == {"left", "right"}
    assert probe.cancelled_nodes == {"left", "right"}
    assert probe.active == 0
    assert set(probe.root_tasks) == {"left", "right"}
    assert all(task.done() for task in probe.root_tasks.values())
    leaked_heartbeats = [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name().startswith("task-heartbeat:")
    ]
    assert leaked_heartbeats == []
    with sqlite3.connect(db_path) as conn:
        operation_commit_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_COMMITTED'
            """
        ).fetchone()[0]
    assert operation_commit_count == 0


@pytest.mark.asyncio
async def test_ne01_coordinator_renews_long_execution_authority_until_acceptance(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    calls: list[str] = []
    clock = _elapsed_clock()
    store = DurableTaskStore(db_path, clock=clock)
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            db_path,
            calls,
            clock=clock,
            task_store=store,
            worker_resolver=_StaticResolver(
                _SlowWorker(calls, delay_seconds=2.5)
            ),
            lease_ttl_ms=2_000,
            operation_ttl_ms=2_000,
        ),
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

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "ACCEPTED"
    assert receipt["accepted"] is True
    assert calls == ["root"]
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["active_leases"] == []
    with sqlite3.connect(db_path) as conn:
        heartbeat_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'TASK_LEASE_HEARTBEAT'
            """
        ).fetchone()[0]
        renewal_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_RENEWED'
            """
        ).fetchone()[0]
        reservation_status = conn.execute(
            """
            SELECT status FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ?
            """,
            (snapshot["attempts"][0]["attempt_id"],),
        ).fetchone()[0]
    assert heartbeat_count >= 1
    assert renewal_count >= 1
    assert reservation_status == "COMMITTED"


@pytest.mark.asyncio
async def test_ne01_coordinator_heartbeat_loss_blocks_operation_commit(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    calls: list[str] = []
    clock = _elapsed_clock()
    durable_store = DurableTaskStore(db_path, clock=clock)
    failing_store = _FailingHeartbeatStore(durable_store)
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            db_path,
            calls,
            clock=clock,
            task_store=failing_store,
            worker_resolver=_StaticResolver(
                _SlowWorker(calls, delay_seconds=0.25)
            ),
            lease_ttl_ms=120,
            operation_ttl_ms=120,
        ),
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

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["accepted"] is False
    assert receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert failing_store.heartbeat_calls == 2
    with sqlite3.connect(db_path) as conn:
        reservation_status = conn.execute(
            "SELECT status FROM task_runtime_idempotency_reservation"
        ).fetchone()[0]
        commit_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_COMMITTED'
            """
        ).fetchone()[0]
    assert reservation_status == "RESERVED"
    assert commit_count == 0


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
        async for event in first_runtime._node_execution.execute_stream(  # noqa: SLF001
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
async def test_ne01_pre_reservation_failure_closes_authority_atomically(
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
    resolver = _FailingResolver()

    events = [
        event
        async for event in _coordinator(
            db_path,
            [],
            worker_resolver=resolver,
        ).execute_stream(
            admitted=admitted,
            command=record["command"],
            work_order=record["work_order"],
        )
    ]

    receipt = events[-1]["execution_receipt"]
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "GOVERNED_WORKER_FAILED"
    assert resolver.resolve_count == 1
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    snapshot = store.read_scheduler_snapshot(graph.graph_id, graph.revision)
    attempt = snapshot["attempts"][0]
    assert attempt["status"] == "FAILED"
    assert snapshot["graph_record"]["nodes"][0]["status"] == "FAILED"
    assert snapshot["graph_record"]["status"] == "FAILED"
    assert snapshot["active_leases"] == []
    with sqlite3.connect(db_path) as conn:
        lease_status = conn.execute(
            "SELECT status FROM task_runtime_lease WHERE attempt_id = ?",
            (attempt["attempt_id"],),
        ).fetchone()[0]
        reservation_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_idempotency_reservation"
        ).fetchone()[0]
        failure_outboxes = conn.execute(
            """
            SELECT aggregate_type, event_type, attempt_id, evidence_refs_json
            FROM task_runtime_outbox
            WHERE event_type IN (
                'TASK_ATTEMPT_FAILED',
                'TASK_NODE_FAILED',
                'TASK_GRAPH_FAILED',
                'TASK_LEASE_RELEASED_TERMINAL'
            )
            ORDER BY aggregate_type
            """
        ).fetchall()
    assert lease_status == "RELEASED"
    assert reservation_count == 0
    assert {row[1] for row in failure_outboxes} == {
        "TASK_ATTEMPT_FAILED",
        "TASK_NODE_FAILED",
        "TASK_GRAPH_FAILED",
        "TASK_LEASE_RELEASED_TERMINAL",
    }
    assert len(failure_outboxes) == 4
    assert all(row[2] == attempt["attempt_id"] for row in failure_outboxes)
    assert all(attempt["error_ref"] in json.loads(row[3]) for row in failure_outboxes)


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
        async for event in _coordinator(  # noqa: SLF001
            db_path, []
        )._node_execution.execute_stream(
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
