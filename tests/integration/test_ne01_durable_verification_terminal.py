from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
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
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TaskStoreError,
    build_outbox_payload_ref,
)
from tests.task_runtime_authority_fixtures import seed_raw_task_graph_for_test


NOW = 1_786_100_000_000
GRAPH_ID = "graph::durable-verification"
NODE_ID = "node::durable-verification"
ATTEMPT_ID = "attempt::durable-verification"
RESERVATION_ID = "operation::durable-verification"
BUDGET_ID = "budget::durable-verification"
OWNER_ID = "owner::durable-verification"
RECEIPT_REF = "worker-receipt://durable-verification/committed"


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    discriminator: str,
    attempt_id: str | None = None,
    graph_revision: int | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=graph_revision,
        ),
        idempotency_key=f"durable-verification:{discriminator}",
        outbox_id=f"outbox::durable-verification:{discriminator}",
        attempt_id=attempt_id,
        graph_revision=graph_revision,
        evidence_refs=evidence_refs,
    )


def _verification(*, status: VerificationStatus) -> VerificationResult:
    return VerificationResult(
        verification_id=f"verify::{status.value.lower()}",
        work_order_id="work-order::durable-verification",
        required=True,
        status=status,
        task_graph_id=GRAPH_ID,
        task_node_id=NODE_ID,
        attempt_id=ATTEMPT_ID,
        reason_code=(
            None if status is VerificationStatus.PASS else "OUTPUT_REJECTED"
        ),
        evidence_refs=(RECEIPT_REF,),
        checks=({"name": "worker-commit", "status": status.value},),
        started_at_ms=NOW,
        finished_at_ms=NOW + 1,
    )


def _prepared_store(tmp_path: Path, *, name: str) -> DurableTaskStore:
    db_path = tmp_path / name
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"durable-verification:{name}",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    graph = TaskGraph(
        graph_id=GRAPH_ID,
        root_work_order_id="work-order::durable-verification",
        revision=1,
        planner_ref="planner://durable-verification/test",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id=NODE_ID,
                dependencies=(),
                route="skill:test",
                input_refs=(),
                output_contract={"type": "object"},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=("output verified",),
                status=TaskNodeStatus.READY,
            ),
        ),
    )
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            0,
            "TASK_GRAPH_CREATED",
            discriminator="graph-created",
            graph_revision=1,
        ),
    )
    store.transition_graph(
        GRAPH_ID,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            GRAPH_ID,
            1,
            "TASK_GRAPH_ACTIVE",
            discriminator="graph-active",
            graph_revision=1,
        ),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=BUDGET_ID,
            scope="work-order:durable-verification",
            limits=BudgetAmount(
                token_count=100,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=10_000,
            ),
            reserved=BudgetAmount(
                token_count=100,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=10_000,
            ),
            status=BudgetReservationStatus.ACTIVE,
        ),
        graph_id=GRAPH_ID,
        graph_revision=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            0,
            "BUDGET_RESERVATION_CREATED",
            discriminator="budget-created",
        ),
    )
    claim = store.claim_ready_node(
        attempt_id=ATTEMPT_ID,
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        expected_node_version=0,
        expected_attempt_count=0,
        owner_id=OWNER_ID,
        lease_ttl_ms=60_000,
        budget_id=BUDGET_ID,
        operation_scope="durable-verification",
        operation_fingerprint="sha256:" + "a" * 64,
        attempt_created_outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            0,
            "TASK_ATTEMPT_CREATED",
            discriminator="attempt-created",
            attempt_id=ATTEMPT_ID,
        ),
        attempt_leased_outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            1,
            "TASK_ATTEMPT_LEASED",
            discriminator="attempt-leased",
            attempt_id=ATTEMPT_ID,
        ),
    )
    lease = claim["lease"]
    store.transition_attempt(
        ATTEMPT_ID,
        owner_id=OWNER_ID,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            2,
            "TASK_ATTEMPT_RUNNING",
            discriminator="attempt-running",
            attempt_id=ATTEMPT_ID,
        ),
    )
    store.transition_node(
        GRAPH_ID,
        1,
        NODE_ID,
        expected_version=1,
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE",
            f"{GRAPH_ID}@1/{NODE_ID}",
            2,
            "TASK_NODE_RUNNING",
            discriminator="node-running",
        ),
    )
    acquired = store.reserve_operation(
        reservation_id=RESERVATION_ID,
        business_scope="durable-verification",
        operation_fingerprint="sha256:" + "a" * 64,
        attempt_id=ATTEMPT_ID,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER_ID,
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            RESERVATION_ID,
            0,
            "OPERATION_RESERVED",
            discriminator="operation-reserved",
            attempt_id=ATTEMPT_ID,
        ),
    )
    assert acquired.acquired is True
    store.commit_operation(
        RESERVATION_ID,
        attempt_id=ATTEMPT_ID,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER_ID,
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        receipt_ref=RECEIPT_REF,
        outbox=_event(
            "OPERATION_RESERVATION",
            RESERVATION_ID,
            1,
            "OPERATION_COMMITTED",
            discriminator="operation-committed",
            attempt_id=ATTEMPT_ID,
        ),
    )
    store.transition_attempt(
        ATTEMPT_ID,
        owner_id=OWNER_ID,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            3,
            "TASK_ATTEMPT_SUCCEEDED",
            discriminator="attempt-succeeded",
            attempt_id=ATTEMPT_ID,
        ),
    )
    for expected_version, target in (
        (2, TaskNodeStatus.EXECUTED),
        (3, TaskNodeStatus.VERIFYING),
    ):
        store.transition_node(
            GRAPH_ID,
            1,
            NODE_ID,
            expected_version=expected_version,
            target_status=target,
            outbox=_event(
                "TASK_NODE",
                f"{GRAPH_ID}@1/{NODE_ID}",
                expected_version + 1,
                f"TASK_NODE_{target.value}",
                discriminator=f"node-{target.value.lower()}",
            ),
        )
    return store


def _verification_outbox(*, status: TaskNodeStatus) -> OutboxMessage:
    return _event(
        "TASK_NODE",
        f"{GRAPH_ID}@1/{NODE_ID}",
        5,
        f"TASK_NODE_{status.value}",
        discriminator=f"node-{status.value.lower()}",
        attempt_id=ATTEMPT_ID,
        evidence_refs=(RECEIPT_REF,),
    )


def test_pass_decision_reopens_after_process_loss(tmp_path: Path) -> None:
    store = _prepared_store(tmp_path, name="verification-pass.sqlite3")
    verification = _verification(status=VerificationStatus.PASS)
    committed = store.commit_node_verification(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        reservation_id=RESERVATION_ID,
        expected_node_version=4,
        target_status=TaskNodeStatus.VERIFIED,
        verification_result=verification,
        node_outbox=_verification_outbox(status=TaskNodeStatus.VERIFIED),
    )
    assert committed["replayed"] is False
    assert committed["node"]["status"] == "VERIFIED"

    reopened = DurableTaskStore(store.db_path, clock=lambda: NOW + 10)
    decision = reopened.get_node_verification_decision(
        GRAPH_ID,
        1,
        NODE_ID,
        node_version_after=5,
    )
    assert decision["verification_result"] == verification.to_dict()
    assert decision["attempt_id"] == ATTEMPT_ID
    snapshot = reopened.read_scheduler_snapshot(GRAPH_ID, 1)
    assert snapshot["node_verifications"][0]["verification_ref"] == (
        decision["verification_ref"]
    )
    replay = reopened.commit_node_verification(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        reservation_id=RESERVATION_ID,
        expected_node_version=4,
        target_status=TaskNodeStatus.VERIFIED,
        verification_result=verification,
        node_outbox=_verification_outbox(status=TaskNodeStatus.VERIFIED),
    )
    assert replay["replayed"] is True
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_node_verification_decision
                SET target_status = 'VERIFICATION_FAILED'
                """
            )


def test_failure_terminal_is_atomic_replayable_and_rolls_back_on_drift(
    tmp_path: Path,
) -> None:
    store = _prepared_store(tmp_path, name="verification-failure.sqlite3")
    verification = _verification(status=VerificationStatus.FAIL)
    node_outbox = _verification_outbox(
        status=TaskNodeStatus.VERIFICATION_FAILED
    )
    graph_outbox = _event(
        "TASK_GRAPH",
        GRAPH_ID,
        2,
        "TASK_GRAPH_FAILED",
        discriminator="graph-failed",
        graph_revision=1,
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        1,
        "BUDGET_RESERVATION_RELEASED",
        discriminator="budget-released",
    )
    committed = store.finalize_node_verification_failure(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        reservation_id=RESERVATION_ID,
        budget_id=BUDGET_ID,
        expected_node_version=4,
        expected_graph_version=1,
        expected_budget_version=0,
        verification_result=verification,
        node_outbox=node_outbox,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    assert committed["replayed"] is False
    assert committed["node"]["status"] == "VERIFICATION_FAILED"
    assert committed["graph"]["status"] == "FAILED"
    assert committed["budget"]["status"] == "RELEASED"

    replay = DurableTaskStore(
        store.db_path,
        clock=lambda: NOW + 20,
    ).finalize_node_verification_failure(
        GRAPH_ID,
        1,
        NODE_ID,
        attempt_id=ATTEMPT_ID,
        reservation_id=RESERVATION_ID,
        budget_id=BUDGET_ID,
        expected_node_version=4,
        expected_graph_version=1,
        expected_budget_version=0,
        verification_result=verification,
        node_outbox=node_outbox,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    assert replay["replayed"] is True

    second = _prepared_store(tmp_path, name="verification-rollback.sqlite3")
    with pytest.raises(TaskStoreError):
        second.finalize_node_verification_failure(
            GRAPH_ID,
            1,
            NODE_ID,
            attempt_id=ATTEMPT_ID,
            reservation_id=RESERVATION_ID,
            budget_id=BUDGET_ID,
            expected_node_version=4,
            expected_graph_version=1,
            expected_budget_version=0,
            verification_result=verification,
            node_outbox=node_outbox,
            graph_outbox=_event(
                "TASK_GRAPH",
                "different-graph",
                2,
                "TASK_GRAPH_FAILED",
                discriminator="invalid-graph-failed",
                graph_revision=1,
            ),
            budget_outbox=budget_outbox,
        )
    assert second.get_node(GRAPH_ID, 1, NODE_ID)["status"] == "VERIFYING"
    assert second.get_graph(GRAPH_ID, 1)["status"] == "ACTIVE"
    assert second.read_scheduler_snapshot(GRAPH_ID, 1)["budgets"][0]["status"] == (
        "ACTIVE"
    )
    with pytest.raises(TaskStoreError):
        second.get_node_verification_decision(GRAPH_ID, 1, NODE_ID)
