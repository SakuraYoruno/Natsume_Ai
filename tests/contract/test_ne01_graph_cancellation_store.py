from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import sqlite3
from threading import Event
import time
from urllib.parse import quote

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.agent_runtime.node_artifact import (
    NODE_ARTIFACT_SCHEMA_VERSION,
    artifact_digest,
    build_task_result_artifact_ref,
    canonical_artifact_json,
)
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    GraphCancellationOperationStoreRejected,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AttemptStatus,
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
    build_acceptance_effect_payload_digest,
    build_outbox_payload_ref,
    build_reconciliation_attestation_binding,
)
from host_brain.persistence.review_continuation_store import ReviewContinuationStore
from host_brain.persistence.review_repository import ReviewStore
from tests.task_runtime_authority_fixtures import (
    seed_raw_acceptance_effect_for_test,
    seed_raw_task_graph_for_test,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
    GraphCancellationOperationReconciliationService,
)


NOW = 1_940_000_000_000


@dataclass
class Clock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


class RealtimeClock:
    def __call__(self) -> int:
        return time.time_ns() // 1_000_000


def _event(
    aggregate_type: str,
    aggregate_id: str,
    version: int,
    event_type: str,
    *,
    outbox_id: str,
    evidence_refs: tuple[str, ...] = (),
    attempt_id: str | None = None,
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        graph_revision=(1 if aggregate_type == "TASK_GRAPH" else None),
        aggregate_version=version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            graph_revision=(1 if aggregate_type == "TASK_GRAPH" else None),
        ),
        idempotency_key=f"idempotency:{outbox_id}",
        outbox_id=outbox_id,
        evidence_refs=evidence_refs,
        attempt_id=attempt_id,
    )


def _store(
    tmp_path: Path,
    *,
    reconciliation_proof_verifier: Ed25519ReconciliationProofVerifier | None = None,
) -> DurableTaskStore:
    db_path = tmp_path / "cancel.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="migration-cancel",
        ts_ms=NOW,
    )
    assert result.success, result.as_dict()
    store = DurableTaskStore(
        db_path,
        clock=Clock(),
        reconciliation_proof_verifier=reconciliation_proof_verifier,
    )
    graph = TaskGraph(
        graph_id="graph-cancel",
        root_work_order_id="work-order-cancel",
        revision=1,
        planner_ref="planner://cancel/test",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id="node-1",
                dependencies=(),
                route="skill:test",
                input_refs=(),
                output_contract={"type": "object"},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
        result_node_id="node-1",
    )
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event(
            "TASK_GRAPH", "graph-cancel", 0, "TASK_GRAPH_CREATED",
            outbox_id="outbox-create",
        ),
    )
    store.transition_graph(
        "graph-cancel",
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH", "graph-cancel", 1, "TASK_GRAPH_ACTIVE",
            outbox_id="outbox-active",
        ),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id="budget-cancel",
            scope="graph:graph-cancel@1",
            limits=BudgetAmount(token_count=100),
            reserved=BudgetAmount(token_count=100),
        ),
        graph_id="graph-cancel",
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION", "budget-cancel", 0,
            "BUDGET_RESERVATION_CREATED", outbox_id="outbox-budget-create",
        ),
    )
    store.transition_budget(
        "budget-cancel",
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION", "budget-cancel", 1,
            "BUDGET_RESERVATION_ACTIVE", outbox_id="outbox-budget-active",
        ),
    )
    return store


def _request(store: DurableTaskStore) -> tuple[dict, OutboxMessage]:
    payload = {
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": "request-cancel",
        "request_identity": "principal://user/alice",
        "source": "USER",
        "reason_code": "USER_CANCELLED_TASK",
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 1,
        "graph_target_status": "BLOCKED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "node_states_before": {
            "node-1": {"status": "READY", "status_version": 0},
        },
        "node_states_after": {
            "node-1": {"status": "CANCEL_REQUESTED", "status_version": 1},
        },
        "graph_outbox_id": "outbox-cancel-request",
    }
    intent = normalize_graph_cancellation_intent(payload)
    outbox = _event(
        "TASK_GRAPH",
        "graph-cancel",
        2,
        "TASK_GRAPH_CANCELLATION_REQUESTED",
        outbox_id="outbox-cancel-request",
        evidence_refs=(intent.intent_ref,),
    )
    result = store.request_graph_cancellation(
        "graph-cancel",
        1,
        budget_id="budget-cancel",
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=payload["node_states_before"],
        intent_ref=intent.intent_ref,
        intent_payload=payload,
        graph_outbox=outbox,
    )
    return result, outbox


def _request_snapshot(
    store: DurableTaskStore,
    *,
    before_status: str,
    before_version: int,
    after_status: str,
    after_version: int,
) -> dict:
    payload = {
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": "request-cancel-custom",
        "request_identity": "principal://operator/test",
        "source": "OPERATOR",
        "reason_code": "OPERATOR_CANCELLED_TASK",
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 1,
        "graph_target_status": "BLOCKED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "node_states_before": {
            "node-1": {"status": before_status, "status_version": before_version},
        },
        "node_states_after": {
            "node-1": {"status": after_status, "status_version": after_version},
        },
        "graph_outbox_id": "outbox-cancel-request-custom",
    }
    intent = normalize_graph_cancellation_intent(payload)
    return store.request_graph_cancellation(
        "graph-cancel",
        1,
        budget_id="budget-cancel",
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=payload["node_states_before"],
        intent_ref=intent.intent_ref,
        intent_payload=payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            "graph-cancel",
            2,
            "TASK_GRAPH_CANCELLATION_REQUESTED",
            outbox_id="outbox-cancel-request-custom",
            evidence_refs=(intent.intent_ref,),
        ),
    )


def _running_operation(store: DurableTaskStore) -> tuple[dict, dict]:
    attempt_id = "attempt-cancel"
    fingerprint = "sha256:" + "a" * 64
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id="graph-cancel",
        graph_revision=1,
        node_id="node-1",
        attempt_no=1,
        expected_node_version=0,
        operation_scope="operation:cancel-test",
        operation_fingerprint=fingerprint,
        outbox=_event(
            "TASK_ATTEMPT", attempt_id, 0, "TASK_ATTEMPT_CREATED",
            outbox_id="outbox-attempt-create", attempt_id=attempt_id,
        ),
    )
    lease = store.acquire_lease(
        attempt_id,
        owner_id="worker-cancel",
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT", attempt_id, 1, "TASK_ATTEMPT_LEASED",
            outbox_id="outbox-attempt-lease", attempt_id=attempt_id,
        ),
    )
    store.transition_node(
        "graph-cancel",
        1,
        "node-1",
        expected_version=1,
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE", "graph-cancel@1/node-1", 2, "TASK_NODE_RUNNING",
            outbox_id="outbox-node-running",
        ),
    )
    store.transition_attempt(
        attempt_id,
        owner_id="worker-cancel",
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT", attempt_id, 2, "TASK_ATTEMPT_RUNNING",
            outbox_id="outbox-attempt-running", attempt_id=attempt_id,
        ),
    )
    reservation = store.reserve_operation(
        reservation_id="reservation-cancel",
        business_scope="operation:cancel-test",
        operation_fingerprint=fingerprint,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id="worker-cancel",
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            "OPERATION_RESERVATION", "reservation-cancel", 0, "OPERATION_RESERVED",
            outbox_id="outbox-operation-reserve", attempt_id=attempt_id,
        ),
    )
    return lease, reservation.reservation


def _stage_effect(
    store: DurableTaskStore,
    *,
    effect_id: str,
    accepted: bool,
) -> None:
    attempt_id = f"attempt-{effect_id.rsplit('/', 1)[-1]}"
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id="graph-cancel",
        graph_revision=1,
        node_id="node-1",
        attempt_no=1,
        expected_node_version=0,
        outbox=_event(
            "TASK_ATTEMPT", attempt_id, 0, "TASK_ATTEMPT_CREATED",
            outbox_id=f"outbox-{attempt_id}", attempt_id=attempt_id,
        ),
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            UPDATE task_runtime_attempt
            SET status = 'SUCCEEDED', status_version = 1,
                completed_ts_ms = ?, updated_ts_ms = ?
            WHERE attempt_id = ?
            """,
            (NOW, NOW, attempt_id),
        )
        conn.commit()
    effect_payload = {"message": effect_id}
    seed_raw_acceptance_effect_for_test(
        store,
        effect_id=effect_id,
        graph_id="graph-cancel",
        graph_revision=1,
        node_id="node-1",
        attempt_id=attempt_id,
        commit_scope=AcceptanceEffectCommitScope.NODE,
        effect_type="surface.reply_delivery",
        payload_ref=f"effect-payload://{effect_id.rsplit('/', 1)[-1]}",
        payload=effect_payload,
        payload_sha256=build_acceptance_effect_payload_digest(effect_payload),
        idempotency_key=f"idempotency-{effect_id}",
    )
    if accepted:
        # This fixture models a pre-0102 intent that existed before the Node
        # reached ACCEPTED.  The current production API commits artifact,
        # Acceptance, and NODE effects in one UoW and never permits a later
        # append to an already accepted Node.
        with sqlite3.connect(store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            suffix = effect_id.rsplit("/", 1)[-1]
            receipt_ref = f"worker-projection://cancellation/{suffix}"
            output_contract = {"type": "object"}
            artifact_payload = {"receipt_ref": receipt_ref}
            row = conn.execute(
                """
                SELECT node_json FROM task_runtime_node
                WHERE graph_id = 'graph-cancel' AND graph_revision = 1 AND node_id = 'node-1'
                """
            ).fetchone()
            payload = json.loads(str(row["node_json"]))
            payload["status"] = "ACCEPTED"
            payload["status_version"] = 1
            conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'ACCEPTED', status_version = 1,
                    node_json = ?, updated_ts_ms = ?
                WHERE graph_id = 'graph-cancel' AND graph_revision = 1 AND node_id = 'node-1'
                """,
                (json.dumps(payload, sort_keys=True, separators=(",", ":")), NOW),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_idempotency_reservation(
                    reservation_id, business_scope, operation_fingerprint,
                    attempt_id, expires_ts_ms, status, status_version,
                    receipt_ref, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, 'COMMITTED', 1, ?, ?, ?)
                """,
                (
                    f"reservation-cancel-effect-{suffix}",
                    f"cancel-effect:{suffix}",
                    "sha256:"
                    + hashlib.sha256(effect_id.encode("utf-8")).hexdigest(),
                    attempt_id,
                    NOW + 60_000,
                    receipt_ref,
                    NOW,
                    NOW,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_node_artifact(
                    artifact_ref, schema_version, graph_id, graph_revision,
                    node_id, attempt_id, receipt_ref, output_contract_json,
                    output_contract_sha256, payload_json, payload_sha256,
                    created_ts_ms
                ) VALUES (?, ?, 'graph-cancel', 1, 'node-1', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    build_task_result_artifact_ref(
                        "graph-cancel",
                        1,
                        "node-1",
                    ),
                    NODE_ARTIFACT_SCHEMA_VERSION,
                    attempt_id,
                    receipt_ref,
                    canonical_artifact_json(output_contract),
                    artifact_digest(output_contract),
                    canonical_artifact_json(artifact_payload),
                    artifact_digest(artifact_payload),
                    NOW,
                ),
            )
            conn.commit()


def _effect_fence(store: DurableTaskStore, effect_id: str) -> dict[str, object]:
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT observed_status, resolution_status,
                   observed_status_version, resolved_ts_ms
            FROM task_runtime_graph_cancellation_effect_fence
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _seed_legacy_retry_pending_effect(
    store: DurableTaskStore,
    *,
    effect_id: str,
) -> None:
    """Seed a canonical row unreachable through the current production API.

    A current dispatcher may claim only a SUCCEEDED Attempt on an ACCEPTED
    Node (or a COMPLETED Graph).  Cancellation excludes the accepted NODE
    authority, while a completed Graph is terminal.  This direct write models
    legacy/corrupt durable state so cancellation must fail closed rather than
    infer that the handler never ran.
    """

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'PENDING', status_version = 2,
                lease_owner = NULL, lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'legacy transport response was lost',
                updated_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, effect_id),
        )
        conn.commit()


def _effect_evidence_ref(
    *,
    effect_id: str,
    status: str,
    status_version: int,
    resolution_status: str,
) -> str:
    material = json.dumps(
        {
            "effect_id": effect_id,
            "status": status,
            "status_version": status_version,
            "resolution_status": resolution_status,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "taskcancel-effect://sha256/" + hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _assert_direct_cancellation_finalization_blocked(
    store: DurableTaskStore,
    *,
    requested: dict[str, object],
    advanced: dict[str, object],
    suffix: str,
) -> None:
    intent = requested["intent"]
    assert isinstance(intent, dict)
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent["intent_ref"],
        "request_id": intent["payload"]["request_id"],
        "request_identity": intent["payload"]["request_identity"],
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "BLOCKED",
        "graph_status_version_before": 2,
        "graph_target_status": "CANCELLED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "budget_target_status": "RELEASED",
        "node_states_before": {
            "node-1": {"status": "CANCEL_REQUESTED", "status_version": 1},
        },
        "node_states_after": {
            "node-1": {"status": "CANCELLED", "status_version": 2},
        },
        "reconciliation_evidence_refs": list(
            advanced["reconciliation_evidence_refs"]
        ),
        "graph_outbox_id": f"outbox-{suffix}-final",
        "budget_outbox_id": f"outbox-{suffix}-budget",
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    authority_evidence = (
        decision.decision_ref,
        intent["intent_ref"],
        intent["payload"]["request_identity"],
    )
    with pytest.raises(TaskStoreError) as blocked:
        store.finalize_graph_cancellation(
            "graph-cancel",
            1,
            budget_id="budget-cancel",
            expected_graph_version=2,
            expected_budget_version=1,
            expected_node_states=decision_payload["node_states_before"],
            intent_ref=intent["intent_ref"],
            decision_ref=decision.decision_ref,
            decision_payload=decision_payload,
            graph_outbox=_event(
                "TASK_GRAPH",
                "graph-cancel",
                3,
                "TASK_GRAPH_CANCELLED",
                outbox_id=decision_payload["graph_outbox_id"],
                evidence_refs=authority_evidence,
            ),
            budget_outbox=_event(
                "BUDGET_RESERVATION",
                "budget-cancel",
                2,
                "BUDGET_RESERVATION_RELEASED",
                outbox_id=decision_payload["budget_outbox_id"],
                evidence_refs=authority_evidence,
            ),
        )
    assert blocked.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT


def _insert_review(
    store: DurableTaskStore,
    *,
    review_id: str,
    revision: int,
    graph_id: str = "graph-cancel",
) -> None:
    subject_ref = f"task-node://{quote(graph_id, safe='')}/{revision}/node-1"
    payload = {
        "schema_version": "Natsume.agent.review_item.v1",
        "review_id": review_id,
        "review_type": "PRE_EXECUTION_APPROVAL",
        "subject_ref": subject_ref,
        "subject_version": 0,
        "requester": "operator-test",
        "reason": "approval required",
        "risk_level": 2,
        "side_effects": [],
        "evidence_refs": [],
        "diff_ref": None,
        "checkpoint_id": f"checkpoint-{review_id}",
        "status": "PENDING",
        "expires_at": NOW + 60_000,
        "decision_actor": None,
        "token_ref": None,
        "resume_phase": "PRE_EXECUTION",
        "resume_count": 0,
        "resume_result_ref": None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO agent_runtime_review(
                review_id, schema_version, review_type, subject_ref,
                subject_version, requester, reason, risk_level,
                side_effects_json, evidence_refs_json, status, status_version,
                expires_at, resume_phase, diff_ref, checkpoint_id,
                decision_actor, token_ref, resume_count, resume_result_ref,
                work_order_id, task_graph_id, task_node_id, review_json,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 0, ?, ?, 2, '[]', '[]', 'PENDING', 0,
                      ?, 'PRE_EXECUTION', NULL, ?, NULL, NULL, 0, NULL,
                      ?, ?, 'node-1', ?, ?, ?)
            """,
            (
                review_id,
                "Natsume.agent.review_item.v1",
                "PRE_EXECUTION_APPROVAL",
                subject_ref,
                "operator-test",
                "approval required",
                NOW + 60_000,
                f"checkpoint-{review_id}",
                f"work-order-{review_id}",
                graph_id,
                encoded,
                NOW,
                NOW,
            ),
        )
        conn.commit()


def _writer_lock(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _signal_next_store_connection(store: DurableTaskStore) -> Event:
    entered = Event()
    original_connect = store._connect

    def signalled_connect() -> sqlite3.Connection:
        conn = original_connect()
        entered.set()
        return conn

    store._connect = signalled_connect  # type: ignore[method-assign]
    return entered


def test_graph_cancellation_claim_lease_starts_after_writer_lock_acquisition(
    tmp_path: Path,
) -> None:
    seeded = _store(tmp_path)
    _request(seeded)
    clock = RealtimeClock()
    store = DurableTaskStore(seeded.db_path, clock=clock)
    entered = _signal_next_store_connection(store)
    writer = _writer_lock(store.db_path)
    lease_ms = 80
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                store.claim_pending_graph_cancellations,
                "lock-wait-worker",
                lease_ms,
                limit=1,
            )
            assert entered.wait(timeout=2)
            time.sleep((lease_ms + 80) / 1_000)
            released_ts_ms = clock()
            writer.commit()
            claimed = future.result(timeout=2)[0]
    finally:
        writer.close()

    assert claimed["claim_expires_ts_ms"] >= released_ts_ms + lease_ms


@pytest.mark.parametrize("operation", ["heartbeat", "defer", "complete"])
def test_graph_cancellation_settlement_resamples_time_after_writer_lock_wait(
    tmp_path: Path,
    operation: str,
) -> None:
    seeded = _store(tmp_path)
    requested, _ = _request(seeded)
    intent_ref = requested["intent"]["intent_ref"]
    clock = RealtimeClock()
    store = DurableTaskStore(seeded.db_path, clock=clock)
    lease_ms = 100
    claim = store.claim_pending_graph_cancellations(
        "lock-wait-worker",
        lease_ms,
        limit=1,
    )[0]
    entered = _signal_next_store_connection(store)
    writer = _writer_lock(store.db_path)

    def mutate_claim() -> dict:
        common = {
            "worker_id": "lock-wait-worker",
            "claim_token": claim["claim_token"],
            "fencing_token": claim["claim_fencing_token"],
            "expected_version": claim["status_version"],
        }
        if operation == "heartbeat":
            return store.heartbeat_graph_cancellation(
                intent_ref,
                lease_ms=lease_ms,
                **common,
            )
        if operation == "defer":
            return store.defer_graph_cancellation(
                intent_ref,
                retry_delay_ms=lease_ms,
                last_error="LOCK_WAIT_RETRY:TimeoutError",
                **common,
            )
        return store.complete_graph_cancellation(intent_ref, **common)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(mutate_claim)
            assert entered.wait(timeout=2)
            time.sleep((lease_ms + 80) / 1_000)
            writer.commit()
            with pytest.raises(TaskStoreError) as failure:
                future.result(timeout=2)
    finally:
        writer.close()

    assert failure.value.reason_code == TASK_STORE_LEASE_CONFLICT


def test_graph_cancellation_continuation_claim_heartbeat_defer_and_takeover(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    requested, _ = _request(store)
    intent_ref = requested["intent"]["intent_ref"]

    claimed = store.claim_pending_graph_cancellations(
        "worker-a",
        100,
        limit=2,
    )
    assert len(claimed) == 1
    first = claimed[0]
    assert first["intent_ref"] == intent_ref
    assert first["intent"]["status"] == "REQUESTED"
    assert first["claim_owner"] == "worker-a"
    assert first["claim_fencing_token"] == 1
    assert first["attempt_count"] == 1
    assert first["status_version"] == 1
    assert store.claim_pending_graph_cancellations("worker-b", 100) == []

    heartbeat = store.heartbeat_graph_cancellation(
        intent_ref,
        "worker-a",
        first["claim_token"],
        first["claim_fencing_token"],
        200,
        first["status_version"],
    )
    assert heartbeat["status_version"] == first["status_version"] + 1
    assert heartbeat["claim_expires_ts_ms"] == NOW + 200
    with pytest.raises(TaskStoreError, match="stale expected version"):
        store.heartbeat_graph_cancellation(
            intent_ref,
            "worker-a",
            first["claim_token"],
            first["claim_fencing_token"],
            200,
            first["status_version"],
        )

    assert isinstance(store._clock, Clock)
    store._clock.value = NOW + 200
    takeover_store = DurableTaskStore(store.db_path, clock=store._clock)
    takeover = takeover_store.claim_pending_graph_cancellations(
        "worker-b",
        100,
        limit=1,
    )[0]
    assert takeover["claim_fencing_token"] == 2
    assert takeover["attempt_count"] == 2
    assert takeover["status_version"] == heartbeat["status_version"] + 1

    with pytest.raises(TaskStoreError, match="owned by another worker"):
        store.defer_graph_cancellation(
            intent_ref,
            "worker-a",
            first["claim_token"],
            first["claim_fencing_token"],
            50,
            "CANCELLATION_NOT_STABLE:RuntimeError",
            takeover["status_version"],
        )

    deferred = takeover_store.defer_graph_cancellation(
        intent_ref,
        "worker-b",
        takeover["claim_token"],
        takeover["claim_fencing_token"],
        50,
        "CANCELLATION_NOT_STABLE:RuntimeError",
        takeover["status_version"],
    )
    assert deferred["claim_owner"] is None
    assert deferred["claim_token"] is None
    assert deferred["claim_expires_ts_ms"] is None
    assert deferred["available_ts_ms"] == NOW + 250
    assert takeover_store.claim_pending_graph_cancellations("worker-c", 100) == []

    store._clock.value = NOW + 250
    reclaimed = takeover_store.claim_pending_graph_cancellations(
        "worker-c",
        100,
        limit=1,
    )[0]
    assert reclaimed["claim_fencing_token"] == 3
    assert reclaimed["attempt_count"] == 3
    assert reclaimed["intent"]["intent_ref"] == intent_ref
    with pytest.raises(TaskStoreError, match="before its canonical decision"):
        takeover_store.complete_graph_cancellation(
            intent_ref,
            "worker-c",
            reclaimed["claim_token"],
            reclaimed["claim_fencing_token"],
            reclaimed["status_version"],
        )


def test_graph_cancellation_continuation_claim_is_single_owner_across_connections(
    tmp_path: Path,
) -> None:
    first_store = _store(tmp_path)
    _request(first_store)
    second_store = DurableTaskStore(first_store.db_path, clock=Clock())

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            first_store.claim_pending_graph_cancellations,
            "worker-a",
            1_000,
            limit=1,
        )
        second = executor.submit(
            second_store.claim_pending_graph_cancellations,
            "worker-b",
            1_000,
            limit=1,
        )
        results = (first.result(), second.result())

    assert sorted(len(result) for result in results) == [0, 1]
    winner = next(result[0] for result in results if result)
    assert winner["claim_owner"] in {"worker-a", "worker-b"}
    assert winner["claim_fencing_token"] == 1


def test_graph_cancellation_schema_readiness_is_cached_per_store(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.assert_graph_cancellation_ready()
    store.assert_graph_cancellation_continuation_ready()

    def unexpected_connect():
        raise AssertionError("cached readiness must not reopen SQLite")

    store._connect = unexpected_connect  # type: ignore[method-assign]
    store.assert_graph_cancellation_ready()
    store.assert_graph_cancellation_continuation_ready()
    with pytest.raises(AssertionError, match="must not reopen"):
        store.assert_graph_cancellation_ready(force_revalidate=True)


def test_graph_cancellation_continuation_rejects_weakened_table_guards(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        table_sql = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_graph_cancellation_continuation'
            """
        ).fetchone()[0]
        weakened = str(table_sql).replace(
            "last_error TEXT CHECK(\n"
            "        last_error IS NULL\n"
            "        OR (\n"
            "            typeof(last_error) = 'text'\n"
            "            AND length(trim(last_error)) > 0\n"
            "            AND length(last_error) <= 2000\n"
            "        )\n"
            "    )",
            "last_error TEXT",
        )
        weakened = weakened.replace(
            "intent_ref TEXT NOT NULL PRIMARY KEY",
            "intent_ref TEXT PRIMARY KEY",
        )
        weakened = weakened.replace(
            "graph_id TEXT NOT NULL",
            "graph_id TEXT",
        )
        assert weakened != table_sql
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE task_runtime_graph_cancellation_continuation")
        conn.execute(weakened)
        conn.execute(
            """
            CREATE UNIQUE INDEX
                uq_task_runtime_graph_cancellation_continuation_graph
            ON task_runtime_graph_cancellation_continuation(
                graph_id, graph_revision
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_task_runtime_graph_cancellation_continuation_ready
            ON task_runtime_graph_cancellation_continuation(
                available_ts_ms, claim_expires_ts_ms,
                graph_id, graph_revision, intent_ref
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_continuation(
                intent_ref, graph_id, graph_revision,
                claim_owner, claim_token, claim_expires_ts_ms,
                claim_fencing_token, attempt_count, available_ts_ms,
                last_error, status_version, created_ts_ms, updated_ts_ms
            ) VALUES (
                NULL, 'ghost-graph', 1,
                NULL, NULL, NULL,
                0, 0, 0,
                NULL, 0, 0, 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_continuation(
                intent_ref, graph_id, graph_revision,
                claim_owner, claim_token, claim_expires_ts_ms,
                claim_fencing_token, attempt_count, available_ts_ms,
                last_error, status_version, created_ts_ms, updated_ts_ms
            ) VALUES (
                'ghost-intent', NULL, 1,
                NULL, NULL, NULL,
                0, 0, 0,
                NULL, 0, 0, 0
            )
            """
        )
        conn.commit()

    with pytest.raises(TaskStoreError) as failure:
        DurableTaskStore(store.db_path, clock=Clock())

    assert failure.value.reason_code == "TASK_STORE_SCHEMA_NOT_READY"


@pytest.mark.parametrize(
    ("intent_ref", "graph_id"),
    ((None, "ghost-graph"), ("ghost-intent", None)),
)
def test_graph_cancellation_continuation_identity_is_not_nullable(
    tmp_path: Path,
    intent_ref: str | None,
    graph_id: str | None,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_continuation(
                    intent_ref, graph_id, graph_revision,
                    claim_owner, claim_token, claim_expires_ts_ms,
                    claim_fencing_token, attempt_count, available_ts_ms,
                    last_error, status_version, created_ts_ms, updated_ts_ms
                ) VALUES (
                    ?, ?, 1,
                    NULL, NULL, NULL,
                    0, 0, 0,
                    NULL, 0, 0, 0
                )
                """,
                (intent_ref, graph_id),
            )


def test_graph_cancellation_continuation_rejects_extra_business_status_column(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            ALTER TABLE task_runtime_graph_cancellation_continuation
            ADD COLUMN status TEXT NOT NULL DEFAULT 'COMPLETED'
            """
        )
        conn.commit()

    with pytest.raises(TaskStoreError) as failure:
        store.assert_graph_cancellation_continuation_ready(force_revalidate=True)

    assert failure.value.reason_code == "TASK_STORE_SCHEMA_NOT_READY"


def test_graph_cancellation_continuation_rejects_split_foreign_keys(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        table_sql = str(
            conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_graph_cancellation_continuation'
                """
            ).fetchone()[0]
        )
        composite = (
            "FOREIGN KEY (intent_ref, graph_id, graph_revision)\n"
            "        REFERENCES task_runtime_graph_cancellation_intent(\n"
            "            intent_ref, graph_id, graph_revision\n"
            "        ) ON DELETE RESTRICT"
        )
        split = (
            "FOREIGN KEY (intent_ref)\n"
            "        REFERENCES task_runtime_graph_cancellation_intent(intent_ref) "
            "ON DELETE RESTRICT,\n"
            "    FOREIGN KEY (graph_id)\n"
            "        REFERENCES task_runtime_graph_cancellation_intent(graph_id) "
            "ON DELETE RESTRICT,\n"
            "    FOREIGN KEY (graph_revision)\n"
            "        REFERENCES task_runtime_graph_cancellation_intent(graph_revision) "
            "ON DELETE RESTRICT"
        )
        weakened = table_sql.replace(composite, split)
        assert weakened != table_sql
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE task_runtime_graph_cancellation_continuation")
        conn.execute(weakened)
        conn.execute(
            """
            CREATE UNIQUE INDEX
                uq_task_runtime_graph_cancellation_continuation_graph
            ON task_runtime_graph_cancellation_continuation(
                graph_id, graph_revision
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_task_runtime_graph_cancellation_continuation_ready
            ON task_runtime_graph_cancellation_continuation(
                available_ts_ms, claim_expires_ts_ms,
                graph_id, graph_revision, intent_ref
            )
            """
        )
        conn.commit()

    with pytest.raises(TaskStoreError) as failure:
        store.assert_graph_cancellation_continuation_ready(force_revalidate=True)

    assert failure.value.reason_code == "TASK_STORE_SCHEMA_NOT_READY"


def test_graph_cancellation_request_advance_finalize_and_response_loss_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    requested, request_outbox = _request(store)
    assert requested["graph"]["graph"]["status"] == "BLOCKED"
    assert requested["nodes"][0]["status"] == "CANCEL_REQUESTED"
    intent = requested["intent"]
    continuation = store.claim_pending_graph_cancellations(
        "worker-finalizer",
        60_000,
        limit=1,
    )[0]
    advanced = store.advance_graph_cancellation(intent["intent_ref"])
    assert advanced["ready_to_finalize"] is True
    assert advanced["reconciliation_evidence_refs"] == (intent["intent_ref"],)

    graph_outbox_id = "outbox-cancel-final"
    budget_outbox_id = "outbox-cancel-budget"
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent["intent_ref"],
        "request_id": "request-cancel",
        "request_identity": "principal://user/alice",
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "BLOCKED",
        "graph_status_version_before": 2,
        "graph_target_status": "CANCELLED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "budget_target_status": "RELEASED",
        "node_states_before": {
            "node-1": {"status": "CANCEL_REQUESTED", "status_version": 1},
        },
        "node_states_after": {
            "node-1": {"status": "CANCELLED", "status_version": 2},
        },
        "reconciliation_evidence_refs": list(
            advanced["reconciliation_evidence_refs"]
        ),
        "graph_outbox_id": graph_outbox_id,
        "budget_outbox_id": budget_outbox_id,
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    evidence = (
        decision.decision_ref,
        intent["intent_ref"],
        decision_payload["request_identity"],
    )
    graph_outbox = _event(
        "TASK_GRAPH", "graph-cancel", 3, "TASK_GRAPH_CANCELLED",
        outbox_id=graph_outbox_id, evidence_refs=evidence,
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION", "budget-cancel", 2,
        "BUDGET_RESERVATION_RELEASED", outbox_id=budget_outbox_id,
        evidence_refs=evidence,
    )
    kwargs = dict(
        budget_id="budget-cancel",
        expected_graph_version=2,
        expected_budget_version=1,
        expected_node_states=decision_payload["node_states_before"],
        intent_ref=intent["intent_ref"],
        decision_ref=decision.decision_ref,
        decision_payload=decision_payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    finalized = store.finalize_graph_cancellation("graph-cancel", 1, **kwargs)
    assert finalized["graph"]["graph"]["status"] == "CANCELLED"
    assert finalized["budget"]["status"] == "RELEASED"
    assert finalized["decision"]["decision_ref"] == decision.decision_ref
    assert store.get_graph_cancellation_decision_for_intent(intent["intent_ref"])[
        "decision_ref"
    ] == decision.decision_ref
    completed_continuation = store.complete_graph_cancellation(
        intent["intent_ref"],
        "worker-finalizer",
        continuation["claim_token"],
        continuation["claim_fencing_token"],
        continuation["status_version"],
    )
    assert completed_continuation["claim_owner"] is None
    assert completed_continuation["intent"]["status"] == "FINALIZED"
    assert completed_continuation["decision_ref"] == decision.decision_ref
    assert store.get_graph_cancellation_continuation(intent["intent_ref"]) is None
    assert store.claim_pending_graph_cancellations("worker-replay", 100) == []

    # Simulate a process that committed the 0092 decision, then died before
    # its 0094 complete call.  The next scan removes the expired coordination
    # residue; the durable intent/decision remain the sole completion truth.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_continuation(
                intent_ref, graph_id, graph_revision,
                claim_owner, claim_token, claim_expires_ts_ms,
                claim_fencing_token, attempt_count, available_ts_ms,
                last_error, status_version, created_ts_ms, updated_ts_ms
            ) VALUES (?, 'graph-cancel', 1, 'crashed-worker', 'crashed-token', ?,
                      1, 1, ?, NULL, 1, ?, ?)
            """,
            (intent["intent_ref"], NOW + 1, NOW, NOW, NOW),
        )
        conn.commit()
    assert isinstance(store._clock, Clock)
    store._clock.value = NOW + 1
    assert store.claim_pending_graph_cancellations("cleanup-worker", 100) == []
    assert store.get_graph_cancellation_continuation(intent["intent_ref"]) is None

    replay = store.finalize_graph_cancellation("graph-cancel", 1, **kwargs)
    assert replay["replayed"] is True
    request_replay, _ = _request(store)
    assert request_replay["decision"]["decision_ref"] == decision.decision_ref
    assert request_replay["replayed"] is True
    assert request_outbox.outbox_id == intent["graph_outbox_id"]

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_budget_reservation SET status = 'ACTIVE' WHERE budget_id = ?",
            ("budget-cancel",),
        )
        conn.commit()
    with pytest.raises(TaskStoreError, match="Budget state drifted"):
        store.finalize_graph_cancellation("graph-cancel", 1, **kwargs)
    with pytest.raises(TaskStoreError, match="Budget binding drifted"):
        store.get_graph_cancellation_decision_for_intent(intent["intent_ref"])


def test_finalized_cancellation_getter_revalidates_operation_proof_signature(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = Ed25519ReconciliationProofVerifier(
        {"reconciler-cancel-v1": public_key}
    )
    store = _store(tmp_path, reconciliation_proof_verifier=verifier)
    lease, reserved = _running_operation(store)
    assert reserved["status"] == "RESERVED"
    interrupted = store.mark_worker_interruption_recovery_required(
        "reservation-cancel",
        attempt_id="attempt-cancel",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-cancel",
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        reason_code="GOVERNED_WORKER_FAILED",
    )
    assert interrupted["status"] == "RECOVERY_REQUIRED"
    assert interrupted["recovery_reason_code"] == "GOVERNED_WORKER_FAILED"
    requested = _request_snapshot(
        store,
        before_status="RUNNING",
        before_version=2,
        after_status="CANCEL_REQUESTED",
        after_version=3,
    )
    intent = requested["intent"]
    reservation = store.get_operation_reservation("reservation-cancel")
    assert reservation["status"] == "RECOVERY_REQUIRED"

    proof_ref = "proof://graph-cancellation/reconciliation/reservation-cancel"
    actor_id = "reconciler://graph-cancellation/test"
    proof_binding = build_reconciliation_attestation_binding(
        reservation_id="reservation-cancel",
        business_scope="operation:cancel-test",
        operation_fingerprint="sha256:" + "a" * 64,
        recovery_version=int(reservation["status_version"]),
        reconciler_actor_id=actor_id,
        attestation_ref=proof_ref,
        recovery_action="RELEASE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    proof_signature = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(proof_binding)
    ).decode("ascii")
    released = store.recover_operation_reservation(
        "reservation-cancel",
        action="RELEASE",
        attempt_id="attempt-cancel",
        expected_version=int(reservation["status_version"]),
        reconciler_actor_id=actor_id,
        attestation_ref=proof_ref,
        attestation_key_id="reconciler-cancel-v1",
        attestation_signature=proof_signature,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-cancel",
            int(reservation["status_version"]) + 1,
            "OPERATION_RECOVERY_RELEASE",
            outbox_id="outbox-operation-recovery-release",
            attempt_id="attempt-cancel",
        ),
    )
    assert released["status"] == "RELEASED"

    advanced = store.advance_graph_cancellation(intent["intent_ref"])
    assert advanced["ready_to_finalize"] is True
    assert proof_ref in advanced["reconciliation_evidence_refs"]
    graph = store.get_graph("graph-cancel", 1)
    node = store.get_node("graph-cancel", 1, "node-1")
    budget = store.read_scheduler_snapshot("graph-cancel", 1)["budgets"][0]
    node_states_before = {
        "node-1": {
            "status": node["status"],
            "status_version": node["status_version"],
        }
    }
    node_states_after = {
        "node-1": {
            "status": "CANCELLED",
            "status_version": int(node["status_version"]) + 1,
        }
    }
    graph_outbox_id = "outbox-cancel-proof-final"
    budget_outbox_id = "outbox-cancel-proof-budget"
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent["intent_ref"],
        "request_id": intent["payload"]["request_id"],
        "request_identity": intent["payload"]["request_identity"],
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "BLOCKED",
        "graph_status_version_before": graph["status_version"],
        "graph_target_status": "CANCELLED",
        "budget_id": "budget-cancel",
        "budget_status_before": budget["status"],
        "budget_status_version_before": budget["status_version"],
        "budget_target_status": "RELEASED",
        "node_states_before": node_states_before,
        "node_states_after": node_states_after,
        "reconciliation_evidence_refs": list(
            advanced["reconciliation_evidence_refs"]
        ),
        "graph_outbox_id": graph_outbox_id,
        "budget_outbox_id": budget_outbox_id,
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    authority_evidence = (
        decision.decision_ref,
        intent["intent_ref"],
        intent["payload"]["request_identity"],
    )
    store.finalize_graph_cancellation(
        "graph-cancel",
        1,
        budget_id="budget-cancel",
        expected_graph_version=int(graph["status_version"]),
        expected_budget_version=int(budget["status_version"]),
        expected_node_states=node_states_before,
        intent_ref=intent["intent_ref"],
        decision_ref=decision.decision_ref,
        decision_payload=decision_payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            "graph-cancel",
            int(graph["status_version"]) + 1,
            "TASK_GRAPH_CANCELLED",
            outbox_id=graph_outbox_id,
            evidence_refs=authority_evidence,
        ),
        budget_outbox=_event(
            "BUDGET_RESERVATION",
            "budget-cancel",
            int(budget["status_version"]) + 1,
            "BUDGET_RESERVATION_RELEASED",
            outbox_id=budget_outbox_id,
            evidence_refs=authority_evidence,
        ),
    )
    assert store.get_graph_cancellation_for_graph("graph-cancel", 1) is not None
    assert store.get_graph_cancellation_decision_for_intent(intent["intent_ref"])

    invalid_signature = "ed25519:" + "A" * 86 + "=="
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_idempotency_reservation
            SET reconciliation_proof_signature = ?
            WHERE reservation_id = ?
            """,
            (invalid_signature, "reservation-cancel"),
        )
        conn.commit()

    with pytest.raises(TaskStoreError) as graph_getter_error:
        store.get_graph_cancellation_for_graph("graph-cancel", 1)
    assert graph_getter_error.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with pytest.raises(TaskStoreError) as decision_getter_error:
        store.get_graph_cancellation_decision_for_intent(intent["intent_ref"])
    assert decision_getter_error.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_cancellation_intent_fences_new_execution_and_success(tmp_path: Path) -> None:
    store = _store(tmp_path)
    requested, _ = _request(store)
    with pytest.raises(TaskStoreError) as exc_info:
        store.create_attempt(
            attempt_id="attempt-after-cancel",
            graph_id="graph-cancel",
            graph_revision=1,
            node_id="node-1",
            attempt_no=1,
            expected_node_version=1,
            outbox=_event(
                "TASK_ATTEMPT", "attempt-after-cancel", 0,
                "TASK_ATTEMPT_CREATED", outbox_id="outbox-attempt-after-cancel",
            ),
        )
    assert exc_info.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    assert store.get_graph_cancellation_decision_for_intent(
        requested["intent"]["intent_ref"]
    ) is None

    with pytest.raises(TaskStoreError) as graph_exc:
        store.transition_graph(
            "graph-cancel",
            1,
            expected_version=2,
            target_status=TaskGraphStatus.COMPLETED,
            outbox=_event(
                "TASK_GRAPH", "graph-cancel", 3, "TASK_GRAPH_COMPLETED",
                outbox_id="outbox-success-after-cancel",
            ),
        )
    assert graph_exc.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT


def test_success_state_wins_when_it_commits_before_cancellation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.transition_graph(
        "graph-cancel",
        1,
        expected_version=1,
        target_status=TaskGraphStatus.COMPLETED,
        outbox=_event(
            "TASK_GRAPH", "graph-cancel", 2, "TASK_GRAPH_COMPLETED",
            outbox_id="outbox-success-first",
        ),
    )
    with pytest.raises(TaskStoreError):
        _request(store)
    assert store.get_graph("graph-cancel", 1)["graph"]["status"] == "COMPLETED"


def test_cancel_first_forces_operation_reconciliation_and_rejects_normal_commit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    lease, _ = _running_operation(store)
    requested = _request_snapshot(
        store,
        before_status="RUNNING",
        before_version=2,
        after_status="CANCEL_REQUESTED",
        after_version=3,
    )
    reservation = store.get_operation_reservation("reservation-cancel")
    assert reservation["status"] == "RECOVERY_REQUIRED"
    with pytest.raises(TaskStoreError) as exc_info:
        store.commit_operation(
            "reservation-cancel",
            attempt_id="attempt-cancel",
            lease_id=str(lease["lease_id"]),
            owner_id="worker-cancel",
            fencing_token=int(lease["fencing_token"]),
            expected_version=int(reservation["status_version"]),
            receipt_ref="receipt://operation/late",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-cancel",
                int(reservation["status_version"]) + 1,
                "OPERATION_COMMITTED",
                outbox_id="outbox-operation-late",
                attempt_id="attempt-cancel",
            ),
        )
    assert exc_info.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    advanced = store.advance_graph_cancellation(requested["intent"]["intent_ref"])
    assert advanced["ready_to_finalize"] is False
    assert advanced["pending_operation_ids"] == ("reservation-cancel",)


def test_committed_operation_is_preserved_when_it_wins_before_cancel(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lease, reservation = _running_operation(store)
    store.commit_operation(
        "reservation-cancel",
        attempt_id="attempt-cancel",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-cancel",
        fencing_token=int(lease["fencing_token"]),
        expected_version=int(reservation["status_version"]),
        receipt_ref="receipt://operation/committed",
        outbox=_event(
            "OPERATION_RESERVATION", "reservation-cancel", 1, "OPERATION_COMMITTED",
            outbox_id="outbox-operation-commit", attempt_id="attempt-cancel",
        ),
    )
    requested = _request_snapshot(
        store,
        before_status="RUNNING",
        before_version=2,
        after_status="CANCEL_REQUESTED",
        after_version=3,
    )
    assert store.get_operation_reservation("reservation-cancel")["status"] == "COMMITTED"
    advanced = store.advance_graph_cancellation(requested["intent"]["intent_ref"])
    assert advanced["ready_to_finalize"] is True
    assert "receipt://operation/committed" in advanced["reconciliation_evidence_refs"]


def test_unauthorized_pending_effect_is_fenced_from_dispatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/unauthorized"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    with pytest.raises(TaskStoreError) as exc_info:
        store.claim_acceptance_effect_intent(
            owner_id="effect-dispatcher",
            effect_id=effect_id,
        )
    assert exc_info.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    assert _effect_fence(store, effect_id) == {
        "observed_status": "PENDING",
        "resolution_status": "CANCELLED",
        "observed_status_version": 0,
        "resolved_ts_ms": NOW,
    }


def test_legacy_retry_pending_effect_fails_closed_and_cannot_finalize(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/retry-pending"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    # The canonical dispatcher cannot create this state: an unaccepted Node
    # has no delivery authority.  Seed a legacy/corrupt but row-canonical
    # retry epoch and verify that cancellation never treats it as virgin.
    assert (
        store.claim_acceptance_effect_intent(
            owner_id="dispatcher-retry-pending",
            effect_id=effect_id,
        )
        is None
    )
    _seed_legacy_retry_pending_effect(store, effect_id=effect_id)

    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    replayed = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    assert requested["replayed"] is False
    assert replayed["replayed"] is True
    assert replayed["graph"] == requested["graph"]
    assert replayed["budget"] == requested["budget"]
    assert replayed["nodes"] == requested["nodes"]
    assert replayed["intent"] == requested["intent"]

    assert _effect_fence(store, effect_id) == {
        "observed_status": "PENDING",
        "resolution_status": "RECONCILIATION_REQUIRED",
        "observed_status_version": 2,
        "resolved_ts_ms": None,
    }
    expected_effect_ref = _effect_evidence_ref(
        effect_id=effect_id,
        status="PENDING",
        status_version=2,
        resolution_status="RECONCILIATION_REQUIRED",
    )
    advanced_once = store.advance_graph_cancellation(
        requested["intent"]["intent_ref"]
    )
    advanced_twice = store.advance_graph_cancellation(
        requested["intent"]["intent_ref"]
    )
    for advanced in (advanced_once, advanced_twice):
        assert advanced["ready_to_finalize"] is False
        assert advanced["pending_effect_ids"] == (effect_id,)
        assert effect_id in advanced["reconciliation_evidence_refs"]
        assert expected_effect_ref in advanced["reconciliation_evidence_refs"]
    assert (
        advanced_twice["reconciliation_evidence_refs"]
        == advanced_once["reconciliation_evidence_refs"]
    )
    assert _effect_fence(store, effect_id) == {
        "observed_status": "PENDING",
        "resolution_status": "RECONCILIATION_REQUIRED",
        "observed_status_version": 2,
        "resolved_ts_ms": None,
    }
    _assert_direct_cancellation_finalization_blocked(
        store,
        requested=requested,
        advanced=advanced_twice,
        suffix="legacy-retry-pending",
    )


def test_failed_effect_observed_at_cancel_requires_reconciliation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/failed-before-request"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'dispatcher-before-request',
                lease_expires_ts_ms = NULL, fencing_token = 1,
                attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'transport response was lost',
                updated_ts_ms = ?, failed_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, NOW, effect_id),
        )
        conn.commit()

    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )

    assert _effect_fence(store, effect_id) == {
        "observed_status": "FAILED",
        "resolution_status": "RECONCILIATION_REQUIRED",
        "observed_status_version": 2,
        "resolved_ts_ms": None,
    }
    advanced = store.advance_graph_cancellation(requested["intent"]["intent_ref"])
    assert advanced["ready_to_finalize"] is False
    assert advanced["pending_effect_ids"] == (effect_id,)
    assert _effect_fence(store, effect_id)["resolution_status"] == (
        "RECONCILIATION_REQUIRED"
    )


def test_handler_failure_after_cancel_does_not_resolve_uncertain_effect(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/failed-after-request"
    owner_id = "dispatcher-in-flight"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'CLAIMED', status_version = 1,
                lease_owner = ?, lease_expires_ts_ms = ?,
                fencing_token = 1, attempt_count = 1,
                updated_ts_ms = ?
            WHERE effect_id = ?
            """,
            (owner_id, NOW + 60_000, NOW, effect_id),
        )
        conn.commit()
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )

    failed = store.fail_acceptance_effect_intent(
        effect_id,
        owner_id=owner_id,
        fencing_token=1,
        reason_code="ACCEPTANCE_EFFECT_HANDLER_FAILED",
        detail="downstream timed out after request transmission",
    )

    assert failed["status"] == "FAILED"
    assert _effect_fence(store, effect_id)["resolution_status"] == (
        "RECONCILIATION_REQUIRED"
    )
    advanced = store.advance_graph_cancellation(requested["intent"]["intent_ref"])
    assert advanced["ready_to_finalize"] is False
    assert advanced["pending_effect_ids"] == (effect_id,)


def test_commit_after_cancel_is_positive_evidence_and_resolves_effect_fence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/committed-after-request"
    owner_id = "dispatcher-in-flight"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'CLAIMED', status_version = 1,
                lease_owner = ?, lease_expires_ts_ms = ?,
                fencing_token = 1, attempt_count = 1,
                updated_ts_ms = ?
            WHERE effect_id = ?
            """,
            (owner_id, NOW + 60_000, NOW, effect_id),
        )
        conn.commit()
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )

    committed = store.commit_acceptance_effect_intent(
        effect_id,
        owner_id=owner_id,
        fencing_token=1,
        receipt_ref="effect-receipt://cancel/committed-after-request",
    )

    assert committed["status"] == "COMMITTED"
    assert _effect_fence(store, effect_id)["resolution_status"] == (
        "COMMITTED_EVIDENCE"
    )
    advanced = store.advance_graph_cancellation(requested["intent"]["intent_ref"])
    assert advanced["ready_to_finalize"] is True
    assert advanced["pending_effect_ids"] == ()


def test_forward_failed_evidence_fence_is_forbidden_and_cancellation_stays_blocked(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/legacy-failed-evidence"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'dispatcher-forward-guard',
                lease_expires_ts_ms = NULL, fencing_token = 1,
                attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'downstream outcome is uncertain',
                failed_ts_ms = ?, updated_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, NOW, effect_id),
        )
        conn.commit()
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_effect_fence
                SET resolution_status = 'FAILED_EVIDENCE', resolved_ts_ms = ?
                WHERE effect_id = ?
                """,
                (NOW, effect_id),
            )

    advanced = store.advance_graph_cancellation(requested["intent"]["intent_ref"])

    assert advanced["ready_to_finalize"] is False
    assert advanced["pending_effect_ids"] == (effect_id,)
    assert _effect_fence(store, effect_id)["resolution_status"] == (
        "RECONCILIATION_REQUIRED"
    )

    intent = requested["intent"]
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent["intent_ref"],
        "request_id": intent["payload"]["request_id"],
        "request_identity": intent["payload"]["request_identity"],
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "BLOCKED",
        "graph_status_version_before": 2,
        "graph_target_status": "CANCELLED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "budget_target_status": "RELEASED",
        "node_states_before": {
            "node-1": {"status": "CANCEL_REQUESTED", "status_version": 1},
        },
        "node_states_after": {
            "node-1": {"status": "CANCELLED", "status_version": 2},
        },
        "reconciliation_evidence_refs": list(
            advanced["reconciliation_evidence_refs"]
        ),
        "graph_outbox_id": "outbox-legacy-effect-final",
        "budget_outbox_id": "outbox-legacy-effect-budget",
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    authority_evidence = (
        decision.decision_ref,
        intent["intent_ref"],
        intent["payload"]["request_identity"],
    )
    with pytest.raises(TaskStoreError) as blocked:
        store.finalize_graph_cancellation(
            "graph-cancel",
            1,
            budget_id="budget-cancel",
            expected_graph_version=2,
            expected_budget_version=1,
            expected_node_states=decision_payload["node_states_before"],
            intent_ref=intent["intent_ref"],
            decision_ref=decision.decision_ref,
            decision_payload=decision_payload,
            graph_outbox=_event(
                "TASK_GRAPH",
                "graph-cancel",
                3,
                "TASK_GRAPH_CANCELLED",
                outbox_id="outbox-legacy-effect-final",
                evidence_refs=authority_evidence,
            ),
            budget_outbox=_event(
                "BUDGET_RESERVATION",
                "budget-cancel",
                2,
                "BUDGET_RESERVATION_RELEASED",
                outbox_id="outbox-legacy-effect-budget",
                evidence_refs=authority_evidence,
            ),
        )
    assert blocked.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT


def test_accepted_node_retry_effect_remains_dispatchable_after_graph_cancel(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://cancel/accepted-node"
    _stage_effect(store, effect_id=effect_id, accepted=True)
    claimed = store.claim_acceptance_effect_intent(
        owner_id="effect-dispatcher-first",
        effect_id=effect_id,
    )
    assert claimed is not None
    assert claimed["status"] == "CLAIMED"
    retry = store.reschedule_acceptance_effect_intent(
        effect_id,
        owner_id="effect-dispatcher-first",
        fencing_token=int(claimed["fencing_token"]),
        reason_code="ACCEPTANCE_EFFECT_HANDLER_FAILED",
        detail="retry remains owned by accepted NODE authority",
        delay_ms=0,
    )
    assert retry["status"] == "PENDING"
    assert retry["attempt_count"] == 1
    assert retry["status_version"] == 2
    _request_snapshot(
        store,
        before_status="ACCEPTED",
        before_version=1,
        after_status="ACCEPTED",
        after_version=1,
    )
    with sqlite3.connect(store.db_path) as conn:
        fence_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_graph_cancellation_effect_fence
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()[0]
    assert fence_count == 0
    reclaimed = store.claim_acceptance_effect_intent(
        owner_id="effect-dispatcher-second",
        effect_id=effect_id,
    )
    assert reclaimed is not None
    assert reclaimed["status"] == "CLAIMED"
    assert reclaimed["attempt_count"] == 2
    assert reclaimed["fencing_token"] == 2


def test_review_revocation_is_scoped_to_exact_graph_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_review(store, review_id="review-revision-1", revision=1)
    _insert_review(store, review_id="review-revision-2", revision=2)
    _request(store)
    with sqlite3.connect(store.db_path) as conn:
        statuses = dict(
            conn.execute(
                "SELECT review_id, status FROM agent_runtime_review ORDER BY review_id"
            ).fetchall()
        )
    assert statuses == {
        "review-revision-1": "CANCELLED",
        "review-revision-2": "PENDING",
    }


def test_cancellation_atomically_enqueues_canonical_review_continuation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.transition_node(
        "graph-cancel",
        1,
        "node-1",
        expected_version=0,
        target_status=TaskNodeStatus.WAITING_APPROVAL,
        outbox=_event(
            "TASK_NODE",
            "graph-cancel@1/node-1",
            1,
            "TASK_NODE_WAITING_APPROVAL",
            outbox_id="outbox-node-waiting-approval",
        ),
    )
    review = ReviewItem(
        review_id="review-cancellation-continuation",
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=(
            "task-node://graph-cancel/1/node-1"
            "?root=work-order-cancel&attempt=not-started"
        ),
        subject_version=1,
        requester="operator-test",
        reason="approval required before cancellation",
        risk_level=4,
        side_effects=("host.sys_mode_transition",),
        evidence_refs=("evidence://review/cancel",),
        status=ReviewStatus.PENDING,
        expires_at=NOW + 60_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=(
            "task-checkpoint://graph-cancel/1/node-1/pre_execution"
            "?root=work-order-cancel&attempt=not-started"
        ),
    )
    ReviewStore(store.db_path, clock=Clock()).create_review(
        review,
        work_order_id="work-order-cancel",
        task_graph_id="graph-cancel",
        task_node_id="node-1",
    )

    requested = _request_snapshot(
        store,
        before_status="WAITING_APPROVAL",
        before_version=1,
        after_status="CANCELLED",
        after_version=2,
    )

    continuation_store = ReviewContinuationStore(store.db_path, clock=Clock())
    continuation = continuation_store.get_review_continuation(review.review_id)
    assert continuation is not None
    assert continuation["review"]["status"] == "CANCELLED"
    assert continuation["node"]["status"] == "CANCELLED"
    assert continuation["checkpoint_crossed"] is True
    claim = continuation_store.claim_pending_review_continuations(
        worker_id="review-cancellation-worker",
        lease_ms=30_000,
        limit=1,
    )[0]
    authority = continuation_store.validate_review_continuation_claim(claim)
    assert authority["review_status"] == "CANCELLED"
    assert requested["intent"]["intent_ref"].startswith(
        "taskcancel-intent://sha256/"
    )

    # A response-loss/legacy gap that drops only delivery coordination must
    # be reconstructed from the immutable Review + cancellation authorities.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "DELETE FROM task_runtime_review_continuation WHERE review_id = ?",
            (review.review_id,),
        )
    recovered_store = ReviewContinuationStore(store.db_path, clock=Clock())
    reconciliation = recovered_store.reconcile_terminal_reviews()
    assert reconciliation["enqueued_count"] == 1
    assert recovered_store.get_review_continuation(review.review_id) is not None


def test_review_revocation_uses_canonical_encoded_graph_subject(tmp_path: Path) -> None:
    store = _store(tmp_path)
    graph_id = "graph/cancel encoded"
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id="work-order-cancel-encoded",
        revision=1,
        planner_ref="planner://cancel/encoded-subject",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id="node-1",
                dependencies=(),
                route="skill:test",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
        result_node_id="node-1",
    )
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            0,
            "TASK_GRAPH_CREATED",
            outbox_id="outbox-create-encoded",
        ),
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            1,
            "TASK_GRAPH_ACTIVE",
            outbox_id="outbox-active-encoded",
        ),
    )
    _insert_review(
        store,
        review_id="review-encoded-graph",
        revision=1,
        graph_id=graph_id,
    )
    payload = {
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": "request-cancel-encoded",
        "request_identity": "principal://user/alice",
        "source": "USER",
        "reason_code": "USER_CANCELLED_TASK",
        "graph_id": graph_id,
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 1,
        "graph_target_status": "BLOCKED",
        "budget_id": None,
        "budget_status_before": None,
        "budget_status_version_before": None,
        "node_states_before": {
            "node-1": {"status": "READY", "status_version": 0},
        },
        "node_states_after": {
            "node-1": {"status": "CANCEL_REQUESTED", "status_version": 1},
        },
        "graph_outbox_id": "outbox-cancel-request-encoded",
    }
    intent = normalize_graph_cancellation_intent(payload)
    store.request_graph_cancellation(
        graph_id,
        1,
        budget_id=None,
        expected_graph_version=1,
        expected_budget_version=None,
        expected_node_states=payload["node_states_before"],
        intent_ref=intent.intent_ref,
        intent_payload=payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            graph_id,
            2,
            "TASK_GRAPH_CANCELLATION_REQUESTED",
            outbox_id="outbox-cancel-request-encoded",
            evidence_refs=(intent.intent_ref,),
        ),
    )

    with sqlite3.connect(store.db_path) as conn:
        status = conn.execute(
            "SELECT status FROM agent_runtime_review WHERE review_id = ?",
            ("review-encoded-graph",),
        ).fetchone()[0]
    assert status == "CANCELLED"


@pytest.mark.parametrize(
    ("action", "outcome", "receipt_ref", "terminal_status"),
    (
        ("RELEASE", "NO_SIDE_EFFECT", None, "RELEASED"),
        (
            "COMMIT",
            "SIDE_EFFECT_CONFIRMED",
            "receipt://graph-cancellation/downstream-confirmed",
            "COMMITTED",
        ),
    ),
)
def test_cancellation_operation_service_settles_signed_outcome_and_exactly_replays(
    tmp_path: Path,
    action: str,
    outcome: str,
    receipt_ref: str | None,
    terminal_status: str,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    store = _store(
        tmp_path,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {"reconciler-cancel-v1": public_key}
        ),
    )
    _running_operation(store)
    requested = _request_snapshot(
        store,
        before_status="RUNNING",
        before_version=2,
        after_status="CANCEL_REQUESTED",
        after_version=3,
    )
    intent_ref = str(requested["intent"]["intent_ref"])
    reservation = store.get_operation_reservation("reservation-cancel")
    assert reservation["status"] == "RECOVERY_REQUIRED"
    expected_version = int(reservation["status_version"])
    proof_ref = (
        "proof://graph-cancellation/reconciliation/service-"
        f"{action.lower()}"
    )
    actor_id = "reconciler://graph-cancellation/operator"
    proof_binding = build_reconciliation_attestation_binding(
        reservation_id="reservation-cancel",
        business_scope="operation:cancel-test",
        operation_fingerprint="sha256:" + "a" * 64,
        recovery_version=expected_version,
        reconciler_actor_id=actor_id,
        attestation_ref=proof_ref,
        recovery_action=action,
        reconciliation_outcome=outcome,
        receipt_ref=receipt_ref,
    )
    signature = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(proof_binding)
    ).decode("ascii")
    wakes: list[str] = []
    service = GraphCancellationOperationReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: wakes.append("wake"),
    )
    request = {
        "intent_ref": intent_ref,
        "reservation_id": "reservation-cancel",
        "expected_version": expected_version,
        "action": action,
        "reconciler_actor_id": actor_id,
        "downstream_receipt_ref": receipt_ref,
        "attestation_ref": proof_ref,
        "attestation_key_id": "reconciler-cancel-v1",
        "attestation_signature": signature,
    }

    with pytest.raises(GraphCancellationOperationReconciliationError) as forged_actor:
        service.reconcile_graph_cancellation_operation(
            **{**request, "reconciler_actor_id": "reconciler://forged/actor"}
        )
    assert forged_actor.value.side_effects_may_have_run is False
    assert store.get_operation_reservation("reservation-cancel")["status"] == (
        "RECOVERY_REQUIRED"
    )

    first = service.reconcile_graph_cancellation_operation(**request)
    replay = service.reconcile_graph_cancellation_operation(**request)

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert first["operation"] == replay["operation"]
    assert first["operation"] == {
        "reservation_id": "reservation-cancel",
        "status": terminal_status,
        "status_version": expected_version + 1,
        "reconciliation_action": action,
        "reconciliation_outcome": outcome,
        "receipt_ref": receipt_ref,
        "attestation_ref": proof_ref,
        "attestation_key_id": "reconciler-cancel-v1",
    }
    assert wakes == ["wake", "wake"]
    advanced = store.advance_graph_cancellation(intent_ref)
    assert advanced["ready_to_finalize"] is True
    expected_evidence = receipt_ref or proof_ref
    assert expected_evidence in advanced["reconciliation_evidence_refs"]


def test_cancellation_operation_store_rejects_reacquire_and_wrong_intent_without_mutation(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    store = _store(
        tmp_path,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {"reconciler-cancel-v1": public_key}
        ),
    )
    _running_operation(store)
    requested = _request_snapshot(
        store,
        before_status="RUNNING",
        before_version=2,
        after_status="CANCEL_REQUESTED",
        after_version=3,
    )
    intent_ref = str(requested["intent"]["intent_ref"])
    reservation = store.get_operation_reservation("reservation-cancel")
    expected_version = int(reservation["status_version"])
    base_kwargs = {
        "expected_version": expected_version,
        "reconciler_actor_id": "reconciler://graph-cancellation/operator",
        "attestation_ref": "proof://graph-cancellation/reconciliation/reject",
        "attestation_key_id": "reconciler-cancel-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
        "outbox": _event(
            "OPERATION_RESERVATION",
            "reservation-cancel",
            expected_version + 1,
            "OPERATION_RECOVERY_RELEASE",
            outbox_id="outbox-cancellation-operation-rejected",
            attempt_id="attempt-cancel",
        ),
    }

    with pytest.raises(GraphCancellationOperationStoreRejected):
        store.reconcile_graph_cancellation_operation(
            intent_ref,
            "reservation-cancel",
            action="REACQUIRE",
            **base_kwargs,
        )
    with pytest.raises(GraphCancellationOperationStoreRejected):
        store.reconcile_graph_cancellation_operation(
            "cancel://intent/not-found",
            "reservation-cancel",
            action="RELEASE",
            **base_kwargs,
        )

    unchanged = store.get_operation_reservation("reservation-cancel")
    assert unchanged["status"] == "RECOVERY_REQUIRED"
    assert unchanged["status_version"] == expected_version
