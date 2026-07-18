from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    normalize_graph_cancellation_decision,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.persistence.task_repository import (
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    EFFECT_RECONCILIATION_STATE_CONFLICT,
    GraphCancellationEffectReconciliationError,
    GraphCancellationEffectReconciliationService,
)
from tests.contract.test_ne01_graph_cancellation_store import (
    NOW,
    _event,
    _request_snapshot,
    _stage_effect,
    _store,
)


def _sign_request(
    service: GraphCancellationEffectReconciliationService,
    private_key: Ed25519PrivateKey,
    request: dict,
) -> None:
    normalized = service._normalize_request(**request)
    snapshot = service._read_snapshot(normalized)
    artifacts = service._build_artifacts(snapshot=snapshot, request=normalized)
    request["attestation_signature"] = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(artifacts["binding_bytes"])
    ).decode("ascii")


def _replace_fence_with_legacy_state(
    store,
    *,
    effect_id: str,
    observed_status: str,
    observed_status_version: int,
    resolution_status: str,
) -> None:
    trigger_name = "trg_task_effect_failed_evidence_forbidden_update"
    with sqlite3.connect(store.db_path) as conn:
        trigger_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()[0]
        conn.execute(f"DROP TRIGGER {trigger_name}")
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_effect_fence
            SET observed_status = ?, observed_status_version = ?,
                resolution_status = ?, resolved_ts_ms = ?
            WHERE effect_id = ?
            """,
            (
                observed_status,
                observed_status_version,
                resolution_status,
                NOW,
                effect_id,
            ),
        )
        conn.execute(trigger_sql)
        conn.commit()


def _inventory_current_legacy_case(store, *, intent_ref: str, effect_id: str) -> None:
    """Mirror the 0098 migration inventory after constructing a legacy fixture."""

    with store._transaction() as conn:
        subject = store._effect_reconciliation_subject(
            conn,
            intent_ref=intent_ref,
            effect_id=effect_id,
        )
        case = store._derive_effect_reconciliation_case(conn, subject=subject)
        store._insert_effect_reconciliation_case(conn, case)


def test_signed_effect_proof_round_trips_through_real_sqlite_store(
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
            {"effect-reconciliation-v1": public_key}
        ),
    )
    effect_id = "effect://reconciliation/integration-failed"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'effect-dispatcher-integration',
                lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'transport response was lost',
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
    intent_ref = str(requested["intent"]["intent_ref"])
    aggregate = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )
    # A live RECONCILIATION_REQUIRED row is already governed by the virtual
    # case/fence authority; production readiness must not require a proof
    # before the operator has had a chance to record one.
    store.assert_effect_reconciliation_production_ready()
    wakes: list[str] = []
    service = GraphCancellationEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: wakes.append("wake"),
        clock_ms=lambda: NOW,
    )
    request = {
        "intent_ref": intent_ref,
        "effect_id": effect_id,
        "case_id": aggregate["case"]["case_id"],
        "expected_case_snapshot_sha256": aggregate["case"]["snapshot_sha256"],
        "expected_subject_snapshot_sha256": aggregate[
            "subject_snapshot_sha256"
        ],
        "purpose": "PENDING_CANCELLATION",
        "outcome": "NO_SIDE_EFFECT",
        "reconciler_actor_id": "reconciler://integration/effect",
        "issued_ts_ms": NOW,
        "downstream_receipt_ref": None,
        "executor_quiescence_ref": None,
        "attestation_ref": "proof://effect/integration-no-side-effect",
        "attestation_key_id": "effect-reconciliation-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }
    _sign_request(service, private_key, request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result(timeout=15)
            for future in (
                pool.submit(service.reconcile_graph_cancellation_effect, **request),
                pool.submit(service.reconcile_graph_cancellation_effect, **request),
            )
        )
    first = next(item for item in results if item["replayed"] is False)
    replay = next(item for item in results if item["replayed"] is True)

    assert first["status"] == "PROOF_RECORDED"
    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["proof"] == first["proof"]
    assert "attestation_signature" not in first["proof"]
    assert wakes == ["wake", "wake"]
    persisted = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )["proof"]
    assert persisted is not None
    assert persisted["proof_id"] == first["proof"]["proof_id"]
    assert persisted["outcome"] == "NO_SIDE_EFFECT"
    assert "transport response was lost" not in persisted["proof_binding_json"]
    assert "last_error_sha256" in persisted["proof_binding_json"]
    assert json.loads(persisted["proof_binding_json"])["outbox"]["graph_revision"] == 1
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_effect_reconciliation_proof"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE outbox_id = ? AND graph_revision = 1
            """,
            (persisted["outbox_id"],),
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_effect_reconciliation_proof
                SET outcome = 'SIDE_EFFECT_CONFIRMED'
                WHERE proof_id = ?
                """,
                (persisted["proof_id"],),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                DELETE FROM task_runtime_effect_reconciliation_proof
                WHERE proof_id = ?
                """,
                (persisted["proof_id"],),
            )

    advanced = store.advance_graph_cancellation(intent_ref)
    assert advanced["ready_to_finalize"] is True
    assert advanced["pending_effect_ids"] == ()
    assert persisted["proof_id"] in advanced["reconciliation_evidence_refs"]
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent_ref,
        "request_id": requested["intent"]["payload"]["request_id"],
        "request_identity": requested["intent"]["payload"]["request_identity"],
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
        "graph_outbox_id": "outbox-effect-proof-final",
        "budget_outbox_id": "outbox-effect-proof-budget",
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    authority_evidence = (
        decision.decision_ref,
        intent_ref,
        decision_payload["request_identity"],
    )
    finalized = store.finalize_graph_cancellation(
        "graph-cancel",
        1,
        budget_id="budget-cancel",
        expected_graph_version=2,
        expected_budget_version=1,
        expected_node_states=decision_payload["node_states_before"],
        intent_ref=intent_ref,
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
    assert finalized["decision"]["decision_ref"] == decision.decision_ref
    assert store.get_graph_cancellation_intent(intent_ref)["status"] == "FINALIZED"
    assert (
        store.get_graph_cancellation_decision_for_intent(intent_ref)["decision_ref"]
        == decision.decision_ref
    )

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER trg_task_effect_reconciliation_proof_no_update")
        conn.execute(
            """
            UPDATE task_runtime_effect_reconciliation_proof
            SET attestation_signature = ?
            WHERE proof_id = ?
            """,
            ("ed25519:" + "A" * 86 + "==", persisted["proof_id"]),
        )
        conn.commit()
    with pytest.raises(TaskStoreError) as tampered:
        store.get_graph_cancellation_decision_for_intent(intent_ref)
    assert tampered.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_real_store_requires_expired_claim_and_quiescence_before_proof(
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
            {"effect-reconciliation-v1": public_key}
        ),
    )
    effect_id = "effect://reconciliation/integration-claimed"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'CLAIMED', status_version = 1,
                lease_owner = 'effect-dispatcher-expired',
                lease_expires_ts_ms = ?, fencing_token = 1,
                attempt_count = 1, reason_code = NULL, last_error = NULL,
                updated_ts_ms = ?
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
    intent_ref = str(requested["intent"]["intent_ref"])
    aggregate = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )
    service = GraphCancellationEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: None,
        clock_ms=lambda: NOW,
    )
    request = {
        "intent_ref": intent_ref,
        "effect_id": effect_id,
        "case_id": aggregate["case"]["case_id"],
        "expected_case_snapshot_sha256": aggregate["case"]["snapshot_sha256"],
        "expected_subject_snapshot_sha256": aggregate[
            "subject_snapshot_sha256"
        ],
        "purpose": "PENDING_CANCELLATION",
        "outcome": "NO_SIDE_EFFECT",
        "reconciler_actor_id": "reconciler://integration/claimed",
        "issued_ts_ms": NOW,
        "downstream_receipt_ref": None,
        "executor_quiescence_ref": None,
        "attestation_ref": "proof://effect/integration-claimed",
        "attestation_key_id": "effect-reconciliation-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        service.reconcile_graph_cancellation_effect(**request)
    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert (
        store.read_graph_cancellation_effect_reconciliation(intent_ref, effect_id)[
            "proof"
        ]
        is None
    )

    request["executor_quiescence_ref"] = (
        "executor-quiescence://effect/integration-claimed"
    )
    _sign_request(service, private_key, request)
    result = service.reconcile_graph_cancellation_effect(**request)

    assert result["status"] == "PROOF_RECORDED"
    assert result["proof"]["executor_quiescence_ref"] == (
        "executor-quiescence://effect/integration-claimed"
    )


@pytest.mark.parametrize(
    ("case_kind", "resolution_status"),
    (
        ("LEGACY_FAILED_EVIDENCE", "FAILED_EVIDENCE"),
        ("LEGACY_RETRY_PENDING_CANCELLED", "CANCELLED"),
    ),
)
def test_real_store_records_each_pending_legacy_case_kind(
    tmp_path: Path,
    case_kind: str,
    resolution_status: str,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    store = _store(
        tmp_path,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {"effect-reconciliation-v1": public_key}
        ),
    )
    suffix = case_kind.lower().replace("_", "-")
    effect_id = f"effect://reconciliation/{suffix}"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'effect-dispatcher-legacy',
                lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'legacy response uncertainty',
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
    if case_kind == "LEGACY_RETRY_PENDING_CANCELLED":
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'PENDING', status_version = 2,
                    lease_owner = NULL, lease_expires_ts_ms = NULL,
                    failed_ts_ms = NULL
                WHERE effect_id = ?
                """,
                (effect_id,),
            )
            conn.commit()
    _replace_fence_with_legacy_state(
        store,
        effect_id=effect_id,
        observed_status=(
            "PENDING" if case_kind == "LEGACY_RETRY_PENDING_CANCELLED" else "FAILED"
        ),
        observed_status_version=2,
        resolution_status=resolution_status,
    )
    intent_ref = str(requested["intent"]["intent_ref"])
    _inventory_current_legacy_case(
        store,
        intent_ref=intent_ref,
        effect_id=effect_id,
    )
    aggregate = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )
    assert aggregate["case"]["case_kind"] == case_kind
    assert aggregate["case"]["classification"] == "REPAIRABLE_REQUESTED"
    blocked = store.advance_graph_cancellation(intent_ref)
    assert blocked["ready_to_finalize"] is False
    assert blocked["pending_effect_ids"] == (effect_id,)
    service = GraphCancellationEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: None,
        clock_ms=lambda: NOW,
    )
    request = {
        "intent_ref": intent_ref,
        "effect_id": effect_id,
        "case_id": aggregate["case"]["case_id"],
        "expected_case_snapshot_sha256": aggregate["case"]["snapshot_sha256"],
        "expected_subject_snapshot_sha256": aggregate[
            "subject_snapshot_sha256"
        ],
        "purpose": "PENDING_CANCELLATION",
        "outcome": "NO_SIDE_EFFECT",
        "reconciler_actor_id": "reconciler://integration/legacy",
        "issued_ts_ms": NOW,
        "downstream_receipt_ref": None,
        "executor_quiescence_ref": None,
        "attestation_ref": f"proof://effect/{suffix}",
        "attestation_key_id": "effect-reconciliation-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }
    _sign_request(service, private_key, request)

    result = service.reconcile_graph_cancellation_effect(**request)

    assert result["case"]["case_kind"] == case_kind
    assert result["proof"]["target_resolution_status"] == "CANCELLED"
    advanced = store.advance_graph_cancellation(intent_ref)
    assert advanced["ready_to_finalize"] is True
    assert advanced["pending_effect_ids"] == ()
    assert result["proof"]["proof_id"] in advanced["reconciliation_evidence_refs"]
    store.assert_effect_reconciliation_production_ready()


def test_real_store_records_finalized_correction_without_rewriting_decision(
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
            {"effect-reconciliation-v1": public_key}
        ),
    )
    effect_id = "effect://reconciliation/finalized-correction"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'effect-dispatcher-finalized',
                lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'legacy finalized uncertainty',
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
    intent_ref = str(requested["intent"]["intent_ref"])
    _replace_fence_with_legacy_state(
        store,
        effect_id=effect_id,
        observed_status="FAILED",
        observed_status_version=2,
        resolution_status="FAILED_EVIDENCE",
    )
    with store._transaction() as conn:
        legacy_evidence = store._graph_cancellation_evidence_refs(
            conn,
            intent_ref=intent_ref,
            graph_id="graph-cancel",
            revision=1,
        )
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent_ref,
        "request_id": requested["intent"]["payload"]["request_id"],
        "request_identity": requested["intent"]["payload"]["request_identity"],
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
        "reconciliation_evidence_refs": list(legacy_evidence),
        "graph_outbox_id": "outbox-legacy-effect-final",
        "budget_outbox_id": "outbox-legacy-effect-budget",
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    decision_ref = decision.decision_ref
    decision_digest = decision.payload_sha256
    authority_evidence = (
        decision_ref,
        intent_ref,
        decision_payload["request_identity"],
    )
    graph_outbox = _event(
        "TASK_GRAPH",
        "graph-cancel",
        3,
        "TASK_GRAPH_CANCELLED",
        outbox_id=decision_payload["graph_outbox_id"],
        evidence_refs=authority_evidence,
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        "budget-cancel",
        2,
        "BUDGET_RESERVATION_RELEASED",
        outbox_id=decision_payload["budget_outbox_id"],
        evidence_refs=authority_evidence,
    )
    with store._transaction() as conn:
        node_row = conn.execute(
            """
            SELECT node_json FROM task_runtime_node
            WHERE graph_id = 'graph-cancel' AND graph_revision = 1
              AND node_id = 'node-1'
            """
        ).fetchone()
        node_payload = json.loads(str(node_row["node_json"]))
        node_payload["status"] = "CANCELLED"
        node_payload["status_version"] = 2
        conn.execute(
            """
            UPDATE task_runtime_node
            SET status = 'CANCELLED', status_version = 2,
                node_json = ?, updated_ts_ms = ?
            WHERE graph_id = 'graph-cancel' AND graph_revision = 1
              AND node_id = 'node-1'
            """,
            (
                json.dumps(
                    node_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                NOW,
            ),
        )
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = 'RELEASED', status_version = 2, updated_ts_ms = ?
            WHERE budget_id = 'budget-cancel' AND status = 'ACTIVE'
              AND status_version = 1
            """,
            (NOW,),
        )
        store._insert_outbox(
            conn,
            budget_outbox,
            ts_ms=NOW,
            expected_aggregate=("BUDGET_RESERVATION", "budget-cancel", 2),
            expected_event_type="BUDGET_RESERVATION_RELEASED",
            expected_attempt_id=None,
        )
        store._transition_graph_in_transaction(
            conn,
            graph_id="graph-cancel",
            revision=1,
            expected_version=2,
            target_status=TaskGraphStatus.CANCELLED,
            outbox=graph_outbox,
            ts_ms=NOW,
        )
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_decision(
                decision_ref, schema_version, intent_ref, graph_id,
                graph_revision, graph_version_before, graph_version_after,
                budget_id, budget_version_before, budget_version_after,
                payload_json, payload_sha256, graph_outbox_id,
                budget_outbox_id, created_ts_ms
            ) VALUES (?, ?, ?, 'graph-cancel', 1, 2, 3,
                      'budget-cancel', 1, 2, ?, ?, ?, ?, ?)
            """,
            (
                decision_ref,
                GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
                intent_ref,
                decision.payload_json,
                decision_digest,
                graph_outbox.outbox_id,
                budget_outbox.outbox_id,
                NOW,
            ),
        )
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_intent
            SET status = 'FINALIZED', status_version = 1, finalized_ts_ms = ?
            WHERE intent_ref = ? AND status = 'REQUESTED' AND status_version = 0
            """,
            (NOW, intent_ref),
        )
    _inventory_current_legacy_case(
        store,
        intent_ref=intent_ref,
        effect_id=effect_id,
    )
    aggregate = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )
    assert aggregate["case"]["classification"] == "QUARANTINED_FINALIZED"
    assert aggregate["case"]["legacy_decision_ref"] == decision_ref
    finalize_kwargs = {
        "budget_id": "budget-cancel",
        "expected_graph_version": 2,
        "expected_budget_version": 1,
        "expected_node_states": decision_payload["node_states_before"],
        "intent_ref": intent_ref,
        "decision_ref": decision_ref,
        "decision_payload": decision_payload,
        "graph_outbox": graph_outbox,
        "budget_outbox": budget_outbox,
    }
    for blocked_read in (
        lambda: store.get_graph_cancellation_intent(intent_ref),
        lambda: store.get_graph_cancellation_decision(decision_ref),
        lambda: store.get_graph_cancellation_decision_for_intent(intent_ref),
        lambda: store.advance_graph_cancellation(intent_ref),
        lambda: _request_snapshot(
            store,
            before_status="READY",
            before_version=0,
            after_status="CANCEL_REQUESTED",
            after_version=1,
        ),
        lambda: store.finalize_graph_cancellation(
            "graph-cancel",
            1,
            **finalize_kwargs,
        ),
    ):
        with pytest.raises(TaskStoreError) as blocked:
            blocked_read()
        assert blocked.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with sqlite3.connect(store.db_path) as conn:
        frozen_before = conn.execute(
            """
            SELECT payload_json, payload_sha256
            FROM task_runtime_graph_cancellation_decision
            WHERE decision_ref = ?
            """,
            (decision_ref,),
        ).fetchone()
        fence_before = conn.execute(
            """
            SELECT observed_status, observed_status_version,
                   resolution_status, resolved_ts_ms
            FROM task_runtime_graph_cancellation_effect_fence
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
    service = GraphCancellationEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: pytest.fail("finalized correction must not wake"),
        clock_ms=lambda: NOW,
    )
    request = {
        "intent_ref": intent_ref,
        "effect_id": effect_id,
        "case_id": aggregate["case"]["case_id"],
        "expected_case_snapshot_sha256": aggregate["case"]["snapshot_sha256"],
        "expected_subject_snapshot_sha256": aggregate[
            "subject_snapshot_sha256"
        ],
        "purpose": "LEGACY_FINALIZED_CORRECTION",
        "outcome": "NO_SIDE_EFFECT",
        "reconciler_actor_id": "reconciler://integration/finalized",
        "issued_ts_ms": NOW,
        "downstream_receipt_ref": None,
        "executor_quiescence_ref": None,
        "attestation_ref": "proof://effect/finalized-correction",
        "attestation_key_id": "effect-reconciliation-v1",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }
    _sign_request(service, private_key, request)

    result = service.reconcile_graph_cancellation_effect(**request)

    assert result["status"] == "CORRECTION_PROOF_RECORDED"
    assert result["case"]["classification"] == "QUARANTINED_FINALIZED"
    proof_binding = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )["proof"]["proof_binding_json"]
    assert decision_ref in proof_binding
    assert store.get_graph_cancellation_intent(intent_ref)["status"] == "FINALIZED"
    assert store.get_graph_cancellation_decision(decision_ref)["decision_ref"] == (
        decision_ref
    )
    assert store.get_graph_cancellation_decision_for_intent(intent_ref)[
        "decision_ref"
    ] == decision_ref
    assert store.advance_graph_cancellation(intent_ref)["replayed"] is True
    assert _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )["replayed"] is True
    assert store.finalize_graph_cancellation(
        "graph-cancel",
        1,
        **finalize_kwargs,
    )["replayed"] is True
    with sqlite3.connect(store.db_path) as conn:
        frozen_after = conn.execute(
            """
            SELECT payload_json, payload_sha256
            FROM task_runtime_graph_cancellation_decision
            WHERE decision_ref = ?
            """,
            (decision_ref,),
        ).fetchone()
        fence_after = conn.execute(
            """
            SELECT observed_status, observed_status_version,
                   resolution_status, resolved_ts_ms
            FROM task_runtime_graph_cancellation_effect_fence
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
    assert frozen_after == frozen_before == (decision.payload_json, decision_digest)
    assert fence_after == fence_before
    assert result["proof"]["proof_id"] not in decision.payload[
        "reconciliation_evidence_refs"
    ]
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_decision
            SET payload_sha256 = ?
            WHERE decision_ref = ?
            """,
            ("sha256:" + "0" * 64, decision_ref),
        )
        conn.commit()
    with pytest.raises(TaskStoreError) as drifted:
        store.read_graph_cancellation_effect_reconciliation(intent_ref, effect_id)
    assert drifted.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
