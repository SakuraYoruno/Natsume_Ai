from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event
from typing import Any
import uuid

import pytest

import host_brain.persistence.task_parallel_settlement_store as settlement_store_module
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.parallel_settlement import (
    CanonicalParallelSettlementDecision,
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationResult,
    VerificationStatus,
)
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    build_outbox_payload_ref,
)


NOW = 1_920_000_000_000
OWNER = "runtime-ne01/parallel-store"
BUDGET_ID = "budget-ne01-parallel-store"
FAILURE_REF = "retry-error://worker-registry/transient"


@dataclass
class Clock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    identity: str | None = None,
    graph_revision: int | None = None,
) -> OutboxMessage:
    stable = identity or uuid.uuid4().hex
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        graph_revision=(
            int(graph_revision or 1) if aggregate_type == "TASK_GRAPH" else None
        ),
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=(
                int(graph_revision or 1)
                if aggregate_type == "TASK_GRAPH"
                else None
            ),
        ),
        idempotency_key=f"ne01-parallel-store:{stable}",
        outbox_id=f"outbox::ne01-parallel-store:{stable}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _graph(graph_id: str, *, revision: int = 1) -> TaskGraph:
    return TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=revision,
        planner_ref="planner://ne01/parallel-store",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=tuple(
            TaskNode(
                node_id=node_id,
                dependencies=(),
                route=f"skill:{node_id}",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            )
            for node_id in ("left", "right")
        ),
    )


def _store(
    tmp_path: Path,
    graph_id: str,
    *,
    retry_count_limit: int = 0,
) -> DurableTaskStore:
    db_path = tmp_path / f"{graph_id}.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"migration::{graph_id}",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    store = DurableTaskStore(db_path, clock=Clock())
    store.create_graph(
        _graph(graph_id),
        outbox=_event("TASK_GRAPH", graph_id, 0, "TASK_GRAPH_CREATED"),
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", graph_id, 1, "TASK_GRAPH_ACTIVE"),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=BUDGET_ID,
            scope=f"graph:{graph_id}@1",
            limits=BudgetAmount(
                token_count=100,
                tool_call_count=10,
                time_ms=60_000,
                retry_count=retry_count_limit,
            ),
            reserved=BudgetAmount(
                token_count=100,
                tool_call_count=10,
                time_ms=60_000,
                retry_count=retry_count_limit,
            ),
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=graph_id,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    store.transition_budget(
        BUDGET_ID,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            1,
            "BUDGET_RESERVATION_ACTIVE",
        ),
    )
    return store


def _node_states(store: DurableTaskStore, graph_id: str) -> dict[str, dict[str, Any]]:
    return {
        node_id: {
            "status": (record := store.get_node(graph_id, 1, node_id))["status"],
            "status_version": record["status_version"],
        }
        for node_id in ("left", "right")
    }


def _transition_node(
    store: DurableTaskStore,
    graph_id: str,
    node_id: str,
    target: TaskNodeStatus,
    *,
    verification_result: VerificationResult | None = None,
) -> dict[str, Any]:
    current = store.get_node(graph_id, 1, node_id)
    return store.transition_node(
        graph_id,
        1,
        node_id,
        expected_version=int(current["status_version"]),
        target_status=target,
        outbox=_event(
            "TASK_NODE",
            f"{graph_id}@1/{node_id}",
            int(current["status_version"]) + 1,
            f"TASK_NODE_{target.value}",
            evidence_refs=(
                verification_result.evidence_refs
                if verification_result is not None
                else ()
            ),
        ),
        verification_result=verification_result,
    )


def _verification_result(graph_id: str, node_id: str) -> VerificationResult:
    return VerificationResult(
        verification_id=f"verify::{graph_id}::{node_id}",
        work_order_id=f"work-order::{graph_id}",
        required=True,
        status=VerificationStatus.PASS,
        task_graph_id=graph_id,
        task_node_id=node_id,
        reason_code=None,
        evidence_refs=(f"verification://{graph_id}/{node_id}/pass",),
        checks=({"status": VerificationStatus.PASS.value},),
        started_at_ms=NOW,
        finished_at_ms=NOW + 1,
    )


def _running_attempt(
    store: DurableTaskStore,
    graph_id: str,
    *,
    attempt_id: str,
    node_id: str = "left",
) -> dict[str, Any]:
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id=graph_id,
        graph_revision=1,
        node_id=node_id,
        attempt_no=1,
        expected_node_version=0,
        operation_scope=f"graph:{graph_id}@1/{node_id}",
        operation_fingerprint=f"sha256:{('1' if node_id == 'left' else '2') * 64}",
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=attempt_id,
        ),
    )
    lease = store.acquire_lease(
        attempt_id,
        owner_id=OWNER,
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id=attempt_id,
        ),
    )
    _transition_node(store, graph_id, node_id, TaskNodeStatus.RUNNING)
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id=attempt_id,
        ),
    )
    return lease


def _stable_attempt(
    store: DurableTaskStore,
    graph_id: str,
    *,
    node_id: str,
    terminal_status: AttemptStatus,
) -> str:
    """Create the latest stable Attempt and matching durable Node outcome."""

    if terminal_status not in {AttemptStatus.FAILED, AttemptStatus.SUCCEEDED}:
        raise AssertionError(f"unsupported fixture Attempt status: {terminal_status}")
    attempt_id = f"attempt::{graph_id}::{node_id}::1"
    lease = _running_attempt(
        store,
        graph_id,
        attempt_id=attempt_id,
        node_id=node_id,
    )
    if terminal_status is AttemptStatus.SUCCEEDED:
        for target in (
            TaskNodeStatus.EXECUTED,
            TaskNodeStatus.VERIFYING,
            TaskNodeStatus.VERIFIED,
            TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
        ):
            _transition_node(
                store,
                graph_id,
                node_id,
                target,
                verification_result=(
                    _verification_result(graph_id, node_id)
                    if target is TaskNodeStatus.VERIFIED
                    else None
                ),
            )
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=terminal_status,
        error_ref=(FAILURE_REF if terminal_status is AttemptStatus.FAILED else None),
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            f"TASK_ATTEMPT_{terminal_status.value}",
            attempt_id=attempt_id,
        ),
    )
    if terminal_status is AttemptStatus.FAILED:
        _transition_node(store, graph_id, node_id, TaskNodeStatus.FAILED)
    return attempt_id


def _decision_outcome(
    *,
    node_id: str,
    attempt_id: str | None,
    status: str,
    reason_code: str | None,
    evidence_refs: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "attempt_id": attempt_id,
        "status": status,
        "accepted": status == TaskNodeStatus.ACCEPTED.value,
        "reason_code": reason_code,
        "evidence_refs": list(
            evidence_refs
            if evidence_refs is not None
            else (
                (
                    FAILURE_REF
                    if status == TaskNodeStatus.FAILED.value
                    else f"review://{attempt_id}"
                ),
            )
        ),
    }


def test_waiting_approval_contract_requires_null_attempt_and_other_outcomes_require_one() -> None:
    base = {
        "schema_version": "Natsume.task_runtime.parallel_settlement.v2",
        "decision_kind": "REVIEW",
        "selected_node_id": "left",
        "graph_id": "graph-pre-review-contract",
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 1,
        "graph_target_status": "BLOCKED",
        "budget_id": BUDGET_ID,
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "budget_target_status": None,
        "node_states": {
            "left": {"status": "WAITING_APPROVAL", "status_version": 1},
        },
        "outcomes": [
            _decision_outcome(
                node_id="left",
                attempt_id=None,
                status="WAITING_APPROVAL",
                reason_code="GOVERNED_REVIEW_REQUIRED",
                evidence_refs=("review://pre-review", "task-checkpoint://pre-review"),
            )
        ],
        "graph_outbox_id": "outbox::pre-review-contract",
        "budget_outbox_id": None,
    }

    normalized = normalize_parallel_settlement_decision(base)
    assert normalized.payload["outcomes"][0]["attempt_id"] is None

    forged_attempt = json.loads(json.dumps(base))
    forged_attempt["outcomes"][0]["attempt_id"] = "attempt::not-created"
    with pytest.raises(ParallelSettlementContractError, match="must not bind attempt_id"):
        normalize_parallel_settlement_decision(forged_attempt)

    post_without_attempt = json.loads(json.dumps(base))
    post_without_attempt["node_states"]["left"]["status"] = "WAITING_ACCEPTANCE_REVIEW"
    post_without_attempt["outcomes"][0]["status"] = "WAITING_ACCEPTANCE_REVIEW"
    with pytest.raises(ParallelSettlementContractError, match="attempt_id is required"):
        normalize_parallel_settlement_decision(post_without_attempt)


def test_waiting_approval_settlement_preserves_review_evidence_without_attempt(
    tmp_path: Path,
) -> None:
    graph_id = "graph-pre-review-no-attempt"
    store = _store(tmp_path, graph_id)
    _transition_node(store, graph_id, "left", TaskNodeStatus.WAITING_APPROVAL)
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
        identity=f"{graph_id}:blocked",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=None,
                status=TaskNodeStatus.WAITING_APPROVAL.value,
                reason_code="GOVERNED_REVIEW_REQUIRED",
                evidence_refs=(
                    "review://pre-review-no-attempt",
                    "task-checkpoint://pre-review-no-attempt",
                ),
            ),
        ),
    )
    assert budget_outbox is None

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=None,
    )

    assert committed["graph"]["status"] == TaskGraphStatus.BLOCKED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.ACTIVE.value
    assert committed["decision"]["payload"]["outcomes"][0]["attempt_id"] is None
    assert store.read_scheduler_snapshot(graph_id, 1)["attempts"] == []

    replayed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=None,
    )
    assert replayed["replayed"] is True


def _budget_settlement_fixture(
    tmp_path: Path,
    *,
    graph_id: str,
    selected_evidence_refs: tuple[str, ...] | None = None,
) -> tuple[
    DurableTaskStore,
    str,
    str,
    dict[str, dict[str, Any]],
    CanonicalParallelSettlementDecision,
    OutboxMessage,
    OutboxMessage,
]:
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.FAILED,
    )
    usage_id = "budget-usage://sha256/" + hashlib.sha256(
        f"{graph_id}\nleft\n{attempt_id}\noverrun".encode("utf-8")
    ).hexdigest()
    usage = store.consume_budget_once(
        BUDGET_ID,
        usage_id=usage_id,
        charge_kind="WORKER_USAGE",
        graph_id=graph_id,
        graph_revision=1,
        node_id="left",
        attempt_id=attempt_id,
        delta=BudgetAmount(token_count=101),
        expected_version=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            2,
            "BUDGET_EXCEEDED",
            attempt_id=attempt_id,
            evidence_refs=(usage_id,),
            identity=f"{graph_id}:budget-overrun",
        ),
    )
    assert usage.accepted is False
    assert usage.usage_id == usage_id
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
        identity=f"{graph_id}:budget-graph",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        3,
        "BUDGET_RESERVATION_RELEASED",
        identity=f"{graph_id}:budget-release",
    )
    decision, graph_outbox, bound_budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="BUDGET",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status="BUDGET_BLOCKED",
                reason_code="BUDGET_EXCEEDED",
                evidence_refs=(
                    (usage_id,)
                    if selected_evidence_refs is None
                    else selected_evidence_refs
                ),
            ),
        ),
        expected_budget_version=2,
        budget_status_before=BudgetReservationStatus.EXCEEDED.value,
    )
    assert bound_budget_outbox is not None
    return (
        store,
        attempt_id,
        usage_id,
        states,
        decision,
        graph_outbox,
        bound_budget_outbox,
    )


def _settlement_decision(
    *,
    graph_id: str,
    node_states: dict[str, dict[str, Any]],
    graph_outbox: OutboxMessage,
    budget_outbox: OutboxMessage | None,
    decision_kind: str,
    selected_node_id: str,
    graph_target_status: TaskGraphStatus,
    budget_target_status: BudgetReservationStatus | None,
    outcomes: tuple[dict[str, Any], ...],
    expected_graph_version: int = 1,
    expected_budget_version: int = 1,
    budget_status_before: str = BudgetReservationStatus.ACTIVE.value,
) -> tuple[
    CanonicalParallelSettlementDecision,
    OutboxMessage,
    OutboxMessage | None,
]:
    decision = normalize_parallel_settlement_decision(
        {
            "schema_version": "Natsume.task_runtime.parallel_settlement.v2",
            "decision_kind": decision_kind,
            "selected_node_id": selected_node_id,
            "graph_id": graph_id,
            "graph_revision": 1,
            "graph_status_before": TaskGraphStatus.ACTIVE.value,
            "graph_status_version_before": expected_graph_version,
            "graph_target_status": graph_target_status.value,
            "budget_id": BUDGET_ID,
            "budget_status_before": budget_status_before,
            "budget_status_version_before": expected_budget_version,
            "budget_target_status": (
                budget_target_status.value
                if budget_target_status is not None
                else None
            ),
            "node_states": node_states,
            "outcomes": list(outcomes),
            "graph_outbox_id": graph_outbox.outbox_id,
            "budget_outbox_id": (
                budget_outbox.outbox_id if budget_outbox is not None else None
            ),
        }
    )
    evidence_refs = tuple(
        dict.fromkeys((*graph_outbox.evidence_refs, decision.decision_ref))
    )
    bound_graph_outbox = replace(graph_outbox, evidence_refs=evidence_refs)
    bound_budget_outbox = (
        replace(
            budget_outbox,
            evidence_refs=tuple(
                dict.fromkeys((*budget_outbox.evidence_refs, decision.decision_ref))
            ),
        )
        if budget_outbox is not None
        else None
    )
    return decision, bound_graph_outbox, bound_budget_outbox


def _retry_usage_id(attempt_id: str, *, node_only: bool) -> str:
    material = f"{BUDGET_ID}\n{attempt_id}\nretry"
    if node_only:
        material += "\nnode-only"
    return "budget-usage://" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _retry_outbox(attempt_id: str, *, node_only: bool) -> OutboxMessage:
    usage_id = _retry_usage_id(attempt_id, node_only=node_only)
    return _event(
        "TASK_ATTEMPT",
        attempt_id,
        3,
        "TASK_ATTEMPT_FAILED",
        attempt_id=attempt_id,
        evidence_refs=(FAILURE_REF, usage_id),
        identity=f"retry:{attempt_id}:{'node' if node_only else 'graph'}",
    )


def test_rejected_retry_node_outcome_keeps_graph_active_and_exactly_replays(
    tmp_path: Path,
) -> None:
    graph_id = "graph-retry-node-only"
    attempt_id = "attempt-retry-node-only"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)
    outbox = _retry_outbox(attempt_id, node_only=True)

    committed = store.schedule_retry_node_outcome(
        attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        expected_graph_version=1,
        error_ref=FAILURE_REF,
        outbox=outbox,
    )
    replayed = DurableTaskStore(store.db_path, clock=Clock()).schedule_retry_node_outcome(
        attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        expected_graph_version=1,
        error_ref=FAILURE_REF,
        outbox=outbox,
    )

    assert committed["accepted"] is False
    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert committed["graph"]["status"] == TaskGraphStatus.ACTIVE.value
    assert committed["graph"]["status_version"] == 1
    assert committed["node"]["status"] == TaskNodeStatus.FAILED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.EXCEEDED.value
    graph_events = [
        row
        for row in store.list_pending_outbox(limit=100)
        if row["aggregate_type"] == "TASK_GRAPH" and row["attempt_id"] == attempt_id
    ]
    assert graph_events == []


def test_legacy_rejected_retry_still_blocks_graph(tmp_path: Path) -> None:
    graph_id = "graph-retry-legacy"
    attempt_id = "attempt-retry-legacy"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)

    result = store.schedule_retry(
        attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        error_ref=FAILURE_REF,
        outbox=_retry_outbox(attempt_id, node_only=False),
    )

    assert result["accepted"] is False
    assert result["graph"]["status"] == TaskGraphStatus.BLOCKED.value
    assert any(
        row["event_type"] == "TASK_GRAPH_BLOCKED" and row["attempt_id"] == attempt_id
        for row in store.list_pending_outbox(limit=100)
    )


def test_parallel_failure_settlement_releases_budget_and_exactly_replays(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-failure-settlement"
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.FAILED,
    )
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
        identity="parallel-failure-graph",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
        identity="parallel-failure-budget",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="WORKER_FAILED",
            ),
        ),
    )
    assert budget_outbox is not None

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    replayed = DurableTaskStore(store.db_path, clock=Clock()).settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )

    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert committed["graph"]["status"] == TaskGraphStatus.FAILED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.RELEASED.value
    durable_decision = DurableTaskStore(
        store.db_path,
        clock=Clock(),
    ).get_parallel_settlement_decision(decision.decision_ref)
    assert committed["decision"] == replayed["decision"] == durable_decision
    assert durable_decision["payload"] == decision.payload
    assert durable_decision["payload_json"] == decision.payload_json
    assert durable_decision["payload_sha256"] == decision.payload_sha256
    with sqlite3.connect(store.db_path) as conn:
        decision_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0]
        settlement_outboxes = conn.execute(
            """
            SELECT evidence_refs_json FROM task_runtime_outbox
            WHERE outbox_id IN (?, ?) ORDER BY outbox_id
            """,
            (graph_outbox.outbox_id, budget_outbox.outbox_id),
        ).fetchall()
    assert decision_count == 1
    assert len(settlement_outboxes) == 2
    assert all(
        decision.decision_ref in json.loads(row[0])
        for row in settlement_outboxes
    )


def test_parallel_failure_decision_accepts_retry_pending_sibling(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-failure-with-retry"
    store = _store(tmp_path, graph_id, retry_count_limit=1)
    left_attempt_id = f"attempt::{graph_id}::left::1"
    left_lease = _running_attempt(
        store,
        graph_id,
        attempt_id=left_attempt_id,
        node_id="left",
    )
    retry = store.schedule_retry_node_outcome(
        left_attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(left_lease["fencing_token"]),
        expected_attempt_version=2,
        expected_graph_version=1,
        error_ref=FAILURE_REF,
        outbox=_retry_outbox(left_attempt_id, node_only=True),
    )
    assert retry["accepted"] is True
    assert retry["node"]["status"] == TaskNodeStatus.RETRY_PENDING.value
    right_attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="right",
        terminal_status=AttemptStatus.FAILED,
    )
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
        identity=f"{graph_id}:graph",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        3,
        "BUDGET_RESERVATION_RELEASED",
        identity=f"{graph_id}:budget",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="right",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=left_attempt_id,
                status=TaskNodeStatus.RETRY_PENDING.value,
                reason_code="GOVERNED_EXECUTION_IN_PROGRESS",
            ),
            _decision_outcome(
                node_id="right",
                attempt_id=right_attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="GOV_PARALLEL_NODE_FAILED",
            ),
        ),
        expected_budget_version=2,
    )
    assert budget_outbox is not None

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )

    assert committed["graph"]["status"] == TaskGraphStatus.FAILED.value
    assert {
        outcome["node_id"]: outcome["status"]
        for outcome in committed["decision"]["payload"]["outcomes"]
    } == {"left": "RETRY_PENDING", "right": "FAILED"}


def test_parallel_budget_settlement_binds_rejected_usage_and_readers_fail_after_loss(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-budget-settlement"
    (
        store,
        _,
        usage_id,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id=graph_id)

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )

    assert committed["graph"]["status"] == TaskGraphStatus.BLOCKED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.RELEASED.value
    assert committed["decision"]["payload"]["decision_kind"] == "BUDGET"
    assert usage_id in committed["decision"]["payload"]["outcomes"][0][
        "evidence_refs"
    ]
    assert store.get_parallel_settlement_decision(decision.decision_ref)[
        "decision_ref"
    ] == decision.decision_ref
    assert store.get_latest_parallel_settlement_decision(graph_id, 1)[
        "decision_ref"
    ] == decision.decision_ref

    with sqlite3.connect(store.db_path) as conn:
        deleted = conn.execute(
            """
            DELETE FROM task_runtime_budget_usage
            WHERE budget_id = ? AND usage_id = ?
            """,
            (BUDGET_ID, usage_id),
        ).rowcount
    assert deleted == 1

    with pytest.raises(TaskStoreError) as by_ref:
        store.get_parallel_settlement_decision(decision.decision_ref)
    with pytest.raises(TaskStoreError) as latest:
        store.get_latest_parallel_settlement_decision(graph_id, 1)
    assert by_ref.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert latest.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_parallel_decision_readers_fail_closed_when_attempt_lineage_drifts(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-attempt-lineage-drift"
    (
        store,
        attempt_id,
        _,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id=graph_id)
    store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )

    with sqlite3.connect(store.db_path) as conn:
        changed = conn.execute(
            """
            UPDATE task_runtime_attempt SET status = 'CANCELLED'
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).rowcount
    assert changed == 1

    with pytest.raises(TaskStoreError) as by_ref:
        store.get_parallel_settlement_decision(decision.decision_ref)
    with pytest.raises(TaskStoreError) as latest:
        store.get_latest_parallel_settlement_decision(graph_id, 1)
    assert by_ref.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert latest.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_parallel_budget_decision_rejects_second_terminal_version_outbox(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-budget-duplicate-terminal-outbox"
    (
        store,
        _,
        _,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id=graph_id)
    store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    duplicate = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_TERMINAL_DUPLICATE",
        identity=f"{graph_id}:duplicate-terminal-outbox",
    )
    with store._transaction() as conn:  # noqa: SLF001 - corruption fixture
        store._insert_outbox(  # noqa: SLF001 - corruption fixture
            conn,
            duplicate,
            ts_ms=NOW,
            expected_aggregate=("BUDGET_RESERVATION", BUDGET_ID, 2),
            expected_event_type="BUDGET_TERMINAL_DUPLICATE",
            expected_attempt_id=None,
        )

    with pytest.raises(TaskStoreError) as invalid:
        store.get_parallel_settlement_decision(decision.decision_ref)
    assert invalid.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_parallel_budget_decision_rejects_terminal_reservation_version_drift(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-budget-terminal-version-drift"
    (
        store,
        _,
        _,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id=graph_id)
    store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation SET status_version = 9
            WHERE budget_id = ?
            """,
            (BUDGET_ID,),
        )

    with pytest.raises(TaskStoreError) as invalid:
        store.get_parallel_settlement_decision(decision.decision_ref)
    assert invalid.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_parallel_decision_reader_uses_one_sqlite_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_id = "graph-parallel-decision-read-snapshot"
    (
        store,
        attempt_id,
        _,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(tmp_path, graph_id=graph_id)
    store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=2,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    decision_read = Event()
    writer_updated = Event()
    original_record = settlement_store_module._parallel_settlement_decision_record
    observed = False

    def record_with_interleave(row: Any) -> dict[str, Any]:
        nonlocal observed
        record = original_record(row)
        if not observed:
            observed = True
            decision_read.set()
            assert writer_updated.wait(timeout=2.0)
        return record

    monkeypatch.setattr(
        settlement_store_module,
        "_parallel_settlement_decision_record",
        record_with_interleave,
    )

    def tamper_attempt() -> None:
        assert decision_read.wait(timeout=2.0)
        with sqlite3.connect(store.db_path, timeout=2.0) as conn:
            conn.execute("PRAGMA busy_timeout = 2000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE task_runtime_attempt SET status = 'CANCELLED' "
                "WHERE attempt_id = ?",
                (attempt_id,),
            )
            writer_updated.set()
            conn.commit()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tamper_attempt)
        first = store.get_parallel_settlement_decision(decision.decision_ref)
        future.result(timeout=3.0)
    assert first["decision_ref"] == decision.decision_ref

    with pytest.raises(TaskStoreError) as invalid:
        store.get_parallel_settlement_decision(decision.decision_ref)
    assert invalid.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


@pytest.mark.parametrize(
    "evidence_refs",
    (
        (),
        (f"budget-usage://sha256/{'f' * 64}",),
    ),
    ids=("missing", "forged"),
)
def test_parallel_budget_settlement_rejects_unbound_usage_without_partial_write(
    tmp_path: Path,
    evidence_refs: tuple[str, ...],
) -> None:
    graph_id = f"graph-parallel-budget-{len(evidence_refs)}"
    (
        store,
        _,
        _,
        states,
        decision,
        graph_outbox,
        budget_outbox,
    ) = _budget_settlement_fixture(
        tmp_path,
        graph_id=graph_id,
        selected_evidence_refs=evidence_refs,
    )
    with sqlite3.connect(store.db_path) as conn:
        outbox_count_before = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox"
        ).fetchone()[0]

    with pytest.raises(TaskStoreError) as exc:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=2,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )

    assert exc.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    budget = store.reconcile_budget(BUDGET_ID)
    assert budget["status"] == BudgetReservationStatus.EXCEEDED.value
    assert budget["status_version"] == 2
    with sqlite3.connect(store.db_path) as conn:
        decision_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0]
        outbox_count_after = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox"
        ).fetchone()[0]
    assert decision_count == 0
    assert outbox_count_after == outbox_count_before


def test_latest_parallel_decision_returns_none_for_legacy_graph_outboxes(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-legacy-outboxes"
    store = _store(tmp_path, graph_id)

    assert store.get_latest_parallel_settlement_decision(graph_id, 1) is None


def test_latest_parallel_decision_fails_closed_when_graph_outbox_declares_missing_row(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-missing-decision"
    store = _store(tmp_path, graph_id)
    missing_ref = f"taskbatch://sha256/{'a' * 64}"
    store.transition_graph(
        graph_id,
        1,
        expected_version=1,
        target_status=TaskGraphStatus.FAILED,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            2,
            "TASK_GRAPH_FAILED",
            evidence_refs=(missing_ref,),
            identity="parallel-missing-decision",
        ),
    )

    with pytest.raises(TaskStoreError) as exc:
        store.get_latest_parallel_settlement_decision(graph_id, 1)

    assert exc.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert missing_ref in exc.value.detail


def test_latest_parallel_decision_scans_only_the_exact_graph_revision(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-multi-revision-marker"
    store = _store(tmp_path, graph_id)
    store.create_graph(
        _graph(graph_id, revision=2),
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            0,
            "TASK_GRAPH_CREATED",
            graph_revision=2,
            identity="multi-revision-created",
        ),
    )
    store.transition_graph(
        graph_id,
        2,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            1,
            "TASK_GRAPH_ACTIVE",
            graph_revision=2,
            identity="multi-revision-active",
        ),
    )
    missing_ref = f"taskbatch://sha256/{'b' * 64}"
    store.transition_graph(
        graph_id,
        2,
        expected_version=1,
        target_status=TaskGraphStatus.FAILED,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            2,
            "TASK_GRAPH_FAILED",
            graph_revision=2,
            evidence_refs=(missing_ref,),
            identity="multi-revision-missing-decision",
        ),
    )

    assert store.get_latest_parallel_settlement_decision(graph_id, 1) is None
    with pytest.raises(TaskStoreError) as exc:
        store.get_latest_parallel_settlement_decision(graph_id, 2)
    assert exc.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert missing_ref in exc.value.detail


@pytest.mark.parametrize(
    "marker_corruption",
    ("missing_latest", "wrong_existing"),
)
def test_latest_parallel_decision_rejects_invalid_current_marker_with_older_row(
    tmp_path: Path,
    marker_corruption: str,
) -> None:
    graph_id = f"graph-parallel-current-marker-{marker_corruption}"
    store = _store(tmp_path, graph_id)
    left_attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.SUCCEEDED,
    )
    review_states = _node_states(store, graph_id)
    review_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
        identity=f"{graph_id}:review",
    )
    review_decision, review_outbox, _ = _settlement_decision(
        graph_id=graph_id,
        node_states=review_states,
        graph_outbox=review_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=left_attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
        ),
    )
    store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=review_states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        decision_ref=review_decision.decision_ref,
        decision_payload=review_decision.payload,
        graph_outbox=review_outbox,
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=2,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            3,
            "TASK_GRAPH_ACTIVE",
            identity=f"{graph_id}:resume",
        ),
    )
    right_attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="right",
        terminal_status=AttemptStatus.FAILED,
    )
    failure_states = _node_states(store, graph_id)
    failure_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        4,
        "TASK_GRAPH_FAILED",
        identity=f"{graph_id}:failure",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
        identity=f"{graph_id}:release",
    )
    failure_decision, failure_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=failure_states,
        graph_outbox=failure_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="right",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=left_attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
            _decision_outcome(
                node_id="right",
                attempt_id=right_attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="WORKER_FAILED",
            ),
        ),
        expected_graph_version=3,
    )
    assert budget_outbox is not None
    store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=3,
        expected_budget_version=1,
        expected_node_states=failure_states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=failure_decision.decision_ref,
        decision_payload=failure_decision.payload,
        graph_outbox=failure_outbox,
        budget_outbox=budget_outbox,
    )
    assert store.get_latest_parallel_settlement_decision(graph_id, 1)[
        "decision_ref"
    ] == failure_decision.decision_ref

    with sqlite3.connect(store.db_path) as conn:
        deleted = conn.execute(
            """
            DELETE FROM task_runtime_parallel_settlement_decision
            WHERE decision_ref = ?
            """,
            (failure_decision.decision_ref,),
        ).rowcount
        if marker_corruption == "wrong_existing":
            conn.execute(
                """
                UPDATE task_runtime_outbox SET evidence_refs_json = ?
                WHERE outbox_id = ?
                """,
                (
                    json.dumps([review_decision.decision_ref]),
                    failure_outbox.outbox_id,
                ),
            )
    assert deleted == 1

    with pytest.raises(TaskStoreError) as exc:
        store.get_latest_parallel_settlement_decision(graph_id, 1)

    assert exc.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    expected_ref = (
        failure_decision.decision_ref
        if marker_corruption == "missing_latest"
        else review_decision.decision_ref
    )
    assert expected_ref in exc.value.detail


def test_parallel_decision_readiness_requires_migration_0090(tmp_path: Path) -> None:
    graph_id = "graph-parallel-readiness"
    store = _store(tmp_path, graph_id)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TABLE task_runtime_parallel_settlement_decision")

    with pytest.raises(TaskStoreError) as exc:
        store.assert_parallel_settlement_decision_ready()

    assert exc.value.reason_code == TASK_STORE_SCHEMA_NOT_READY
    assert "0090" in exc.value.detail


def test_parallel_settlement_rejects_payload_ref_drift_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-decision-drift"
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.FAILED,
    )
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
        identity="parallel-drift-graph",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
        identity="parallel-drift-budget",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="WORKER_FAILED",
            ),
        ),
    )
    assert budget_outbox is not None
    drifted_payload = json.loads(decision.payload_json)
    drifted_payload["outcomes"][0]["reason_code"] = "TAMPERED_FAILURE"
    drifted = normalize_parallel_settlement_decision(drifted_payload)
    assert drifted.decision_ref != decision.decision_ref
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            decision_ref=decision.decision_ref,
            decision_payload=drifted.payload,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )

    assert error.value.reason_code == TASK_STORE_INVALID
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == (
        BudgetReservationStatus.ACTIVE.value
    )
    assert len(store.list_pending_outbox(limit=100)) == before_outbox
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_settlement_decision"
        ).fetchone()[0] == 0


def test_parallel_settlement_payload_tamper_breaks_getter_and_replay(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-decision-tamper"
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.FAILED,
    )
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
        identity="parallel-tamper-graph",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
        identity="parallel-tamper-budget",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="WORKER_FAILED",
            ),
        ),
    )
    assert budget_outbox is not None
    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    assert committed["decision"]["decision_ref"] == decision.decision_ref
    before_outbox = len(store.list_pending_outbox(limit=100))
    tampered_payload = json.loads(decision.payload_json)
    tampered_payload["outcomes"][0]["reason_code"] = "TAMPERED_DURABLE_FAILURE"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_parallel_settlement_decision
            SET payload_json = ? WHERE decision_ref = ?
            """,
            (
                json.dumps(
                    tampered_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                decision.decision_ref,
            ),
        )

    restarted = DurableTaskStore(store.db_path, clock=Clock())
    with pytest.raises(TaskStoreError) as getter_error:
        restarted.get_parallel_settlement_decision(decision.decision_ref)
    assert getter_error.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID

    with pytest.raises(TaskStoreError) as replay_error:
        restarted.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )
    assert replay_error.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    assert restarted.get_graph(graph_id, 1)["status_version"] == 2
    assert restarted.reconcile_budget(BUDGET_ID)["status_version"] == 2
    assert len(restarted.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_review_settlement_blocks_graph_and_keeps_budget_active(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-review-settlement"
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.SUCCEEDED,
    )
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
        identity="parallel-review-graph",
    )
    decision, graph_outbox, _ = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
        ),
    )

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
    )
    replayed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
    )

    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert committed["graph"]["status"] == TaskGraphStatus.BLOCKED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.ACTIVE.value
    assert committed["budget"]["status_version"] == 1


def test_parallel_settlement_rejects_stale_node_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-stale-node"
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.SUCCEEDED,
    )
    states = _node_states(store, graph_id)
    states["left"]["status_version"] += 1
    before_outbox = len(store.list_pending_outbox(limit=100))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
    )
    decision, graph_outbox, _ = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
        ),
    )

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
        )

    assert error.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_active_lease_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-active-lease"
    attempt_id = "attempt-parallel-active-lease"
    store = _store(tmp_path, graph_id)
    _running_attempt(store, graph_id, attempt_id=attempt_id)
    review_attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="right",
        terminal_status=AttemptStatus.SUCCEEDED,
    )
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
    )
    decision, graph_outbox, _ = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="right",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="right",
                attempt_id=review_attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
        ),
    )

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_terminal_target_without_durable_cause(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-missing-terminal-cause"
    store = _store(tmp_path, graph_id)
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError):
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            decision_ref=f"taskbatch://sha256/{'0' * 64}",
            decision_payload={},
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_FAILED",
            ),
            budget_outbox=_event(
                "BUDGET_RESERVATION",
                BUDGET_ID,
                2,
                "BUDGET_RESERVATION_RELEASED",
            ),
        )

    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_review_settlement_rejects_failed_sibling_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-review-with-failure"
    store = _store(tmp_path, graph_id)
    _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.FAILED,
    )
    review_attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="right",
        terminal_status=AttemptStatus.SUCCEEDED,
    )
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
    )
    decision, graph_outbox, _ = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="right",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="right",
                attempt_id=review_attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
        ),
    )

    with pytest.raises(TaskStoreError):
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
        )

    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_review_settlement_requires_active_budget_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-review-exceeded-budget"
    store = _store(tmp_path, graph_id)
    attempt_id = _stable_attempt(
        store,
        graph_id,
        node_id="left",
        terminal_status=AttemptStatus.SUCCEEDED,
    )
    store.consume_budget(
        BUDGET_ID,
        delta=BudgetAmount(token_count=101),
        expected_version=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            2,
            "BUDGET_EXCEEDED",
        ),
    )
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
    )
    decision, graph_outbox, _ = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=None,
        decision_kind="REVIEW",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value,
                reason_code="ACCEPTANCE_REVIEW_REQUIRED",
            ),
        ),
        expected_budget_version=2,
    )

    with pytest.raises(TaskStoreError):
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=2,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
        )

    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.EXCEEDED.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_unresolved_operation_reservation(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-unresolved-operation"
    attempt_id = "attempt-parallel-unresolved-operation"
    reservation_id = "operation-parallel-unresolved"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)
    store.reserve_operation(
        reservation_id=reservation_id,
        business_scope=f"graph:{graph_id}@1/left",
        operation_fingerprint=f"sha256:{'1' * 64}",
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            0,
            "OPERATION_RESERVED",
            attempt_id=attempt_id,
        ),
    )
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.FAILED,
        error_ref="taskerror://parallel-unresolved-operation",
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            "TASK_ATTEMPT_FAILED",
            attempt_id=attempt_id,
        ),
    )
    _transition_node(store, graph_id, "left", TaskNodeStatus.FAILED)
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="WORKER_FAILED",
            ),
        ),
    )
    assert budget_outbox is not None

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_recoverable_attempt_without_active_lease(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-recoverable-attempt"
    attempt_id = "attempt-parallel-recoverable-attempt"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.FAILED,
        error_ref="taskerror://parallel-recoverable-attempt",
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            "TASK_ATTEMPT_FAILED",
            attempt_id=attempt_id,
        ),
    )
    _transition_node(store, graph_id, "left", TaskNodeStatus.FAILED)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_attempt SET status = 'RECOVERABLE' WHERE attempt_id = ?",
            (attempt_id,),
        )
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
    )
    decision, graph_outbox, budget_outbox = _settlement_decision(
        graph_id=graph_id,
        node_states=states,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        decision_kind="FAILURE",
        selected_node_id="left",
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        outcomes=(
            _decision_outcome(
                node_id="left",
                attempt_id=attempt_id,
                status=TaskNodeStatus.FAILED.value,
                reason_code="WORKER_FAILED",
            ),
        ),
    )
    assert budget_outbox is not None

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            decision_ref=decision.decision_ref,
            decision_payload=decision.payload,
            graph_outbox=graph_outbox,
            budget_outbox=budget_outbox,
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox
