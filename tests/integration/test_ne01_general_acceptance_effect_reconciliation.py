from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.persistence.task_repository import (
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TaskStoreError,
)
from host_brain.persistence.effect_reconciliation_query_repository import (
    EffectReconciliationQueryRepository,
)
from host_brain.task_runtime.acceptance_effect_reconciliation import (
    AcceptanceEffectReconciliationError,
    AcceptanceEffectReconciliationService,
)
from tests.contract.test_ne01_graph_cancellation_store import (
    NOW,
    _stage_effect,
    _store,
)


def _unknown_effect(
    store: Any,
    *,
    effect_id: str,
    status: str = "PENDING",
) -> None:
    _stage_effect(store, effect_id=effect_id, accepted=True)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = ?, status_version = 2,
                lease_owner = CASE WHEN ? = 'FAILED'
                    THEN 'effect-owner-failed' ELSE NULL END,
                lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = CASE WHEN ? = 'PENDING'
                    THEN 'ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED'
                    ELSE 'ACCEPTANCE_EFFECT_HANDLER_FAILED' END,
                last_error = 'opaque:sha256:response-lost',
                available_ts_ms = ?, updated_ts_ms = ?,
                failed_ts_ms = CASE WHEN ? = 'FAILED' THEN ? ELSE NULL END
            WHERE effect_id = ?
            """,
            (
                status,
                status,
                status,
                NOW,
                NOW,
                status,
                NOW,
                effect_id,
            ),
        )
        conn.commit()


def _service(
    tmp_path: Path,
) -> tuple[
    Any,
    AcceptanceEffectReconciliationService,
    Ed25519PrivateKey,
]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    store = _store(
        tmp_path,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {"general-effect-v2": public_key}
        ),
    )
    service = AcceptanceEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        clock_ms=lambda: NOW,
    )
    return store, service, private_key


def _request(
    store: Any,
    *,
    effect_id: str,
    outcome: str,
) -> dict[str, Any]:
    subject = store.read_acceptance_effect_reconciliation(effect_id)
    return {
        "effect_id": effect_id,
        "case_id": subject["case"]["case_id"],
        "expected_case_snapshot_sha256": subject["case"][
            "snapshot_sha256"
        ],
        "expected_subject_snapshot_sha256": subject[
            "subject_snapshot_sha256"
        ],
        "purpose": "GENERAL_UNKNOWN_EFFECT",
        "outcome": outcome,
        "reconciler_actor_id": "reconciler://integration/general-effect",
        "issued_ts_ms": NOW,
        "downstream_receipt_ref": (
            "gateway-receipt://general-effect/committed"
            if outcome == "SIDE_EFFECT_CONFIRMED"
            else None
        ),
        "executor_quiescence_ref": (
            "executor-quiescence://general-effect/confirmed"
        ),
        "attestation_ref": (
            "attestation://general-effect/"
            + outcome.lower().replace("_", "-")
        ),
        "attestation_key_id": "general-effect-v2",
        "attestation_signature": "ed25519:" + "A" * 86 + "==",
    }


def _sign(
    service: AcceptanceEffectReconciliationService,
    private_key: Ed25519PrivateKey,
    request: dict[str, Any],
) -> None:
    normalized = service._normalize_request(**request)
    snapshot = service._read(normalized)
    artifacts = service._build_artifacts(
        snapshot=snapshot,
        request=normalized,
    )
    request["attestation_signature"] = (
        "ed25519:"
        + base64.urlsafe_b64encode(
            private_key.sign(artifacts["binding_bytes"])
        ).decode("ascii")
    )


class _ResponseLossStore:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._lost = False

    def read_acceptance_effect_reconciliation(
        self,
        effect_id: str,
        *,
        case_id: str | None = None,
    ) -> Any:
        return self._delegate.read_acceptance_effect_reconciliation(
            effect_id,
            case_id=case_id,
        )

    def record_acceptance_effect_reconciliation_proof(
        self,
        effect_id: str,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.record_acceptance_effect_reconciliation_proof(
            effect_id,
            **kwargs,
        )
        if not self._lost:
            self._lost = True
            raise TimeoutError("simulated response loss after SQLite commit")
        return result


def test_signed_no_side_effect_reopens_dispatch_without_blind_replay(
    tmp_path: Path,
) -> None:
    store, service, private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/no-side-effect"
    _unknown_effect(store, effect_id=effect_id)
    request = _request(
        store,
        effect_id=effect_id,
        outcome="NO_SIDE_EFFECT",
    )
    _sign(service, private_key, request)

    first = service.reconcile_acceptance_effect(**request)
    replay = service.reconcile_acceptance_effect(**request)

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert first["proof_id"] == replay["proof_id"]
    assert first["target_effect_status"] == "PENDING"
    effect = store.list_acceptance_effect_intents(
        status="PENDING",
        graph_id="graph-cancel",
        node_id="node-1",
        limit=10,
    )[0]
    assert (
        effect["reason_code"]
        == "ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT"
    )
    assert "response-lost" not in first["proof"]

    claimed = store.claim_acceptance_effect_intent(
        owner_id="effect-owner-after-proof",
        lease_ms=60_000,
        effect_id=effect_id,
    )
    assert claimed is not None
    assert claimed["status"] == "CLAIMED"
    assert claimed["claim_reconciliation_required"] is False
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM task_runtime_acceptance_effect_reconciliation_proof
            WHERE effect_id = ? AND outcome = 'NO_SIDE_EFFECT'
            """,
            (effect_id,),
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_reconciliation_proof
                SET outcome = 'SIDE_EFFECT_CONFIRMED'
                WHERE effect_id = ?
                """,
                (effect_id,),
            )


def test_response_loss_reopens_exact_general_effect_proof(
    tmp_path: Path,
) -> None:
    store, service, private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/response-loss"
    _unknown_effect(store, effect_id=effect_id)
    request = _request(
        store,
        effect_id=effect_id,
        outcome="SIDE_EFFECT_CONFIRMED",
    )
    _sign(service, private_key, request)
    response_loss_service = AcceptanceEffectReconciliationService(
        task_store=_ResponseLossStore(store),
        outbox_factory=OutboxMessage,
        clock_ms=lambda: NOW,
    )

    recovered = response_loss_service.reconcile_acceptance_effect(**request)

    assert recovered["replayed"] is True
    assert recovered["target_effect_status"] == "COMMITTED"
    assert recovered["receipt_ref"] == request["downstream_receipt_ref"]
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM task_runtime_acceptance_effect_reconciliation_proof
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()[0] == 1


def test_invalid_signature_is_zero_write(
    tmp_path: Path,
) -> None:
    store, service, _private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/invalid-signature"
    _unknown_effect(store, effect_id=effect_id)
    request = _request(
        store,
        effect_id=effect_id,
        outcome="SIDE_EFFECT_CONFIRMED",
    )
    with pytest.raises(AcceptanceEffectReconciliationError) as rejected:
        service.reconcile_acceptance_effect(**request)
    assert rejected.value.side_effects_may_have_run is False
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM task_runtime_acceptance_effect_reconciliation_proof
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()[0] == 0
        row = conn.execute(
            """
            SELECT status, status_version, receipt_ref
            FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
        assert row == ("PENDING", 2, None)


def test_signed_ingress_evidence_is_append_only_exact_replay(
    tmp_path: Path,
) -> None:
    store, service, private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/evidence-sidecar"
    _unknown_effect(store, effect_id=effect_id, status="FAILED")
    request = _request(
        store,
        effect_id=effect_id,
        outcome="SIDE_EFFECT_CONFIRMED",
    )
    _sign(service, private_key, request)
    stage = {
        "request_id": "request-general-effect-evidence",
        "ingress_audit_ref": "secure-ingress-audit://general/effect-evidence",
        "body_sha256": "a" * 64,
        **request,
    }

    first = store.stage_acceptance_effect_reconciliation_evidence(**stage)
    replay = store.stage_acceptance_effect_reconciliation_evidence(**stage)
    reopened = store.get_acceptance_effect_reconciliation_evidence(
        first["evidence_ref"]
    )

    assert first["exact_replay"] is False
    assert replay["exact_replay"] is True
    assert reopened["evidence_digest"] == first["evidence_digest"]
    assert reopened["attestation_signature"] == request[
        "attestation_signature"
    ]
    assert reopened["proof_id"].startswith(
        "effect-recon-proof://sha256/"
    )
    assert store.read_acceptance_effect_reconciliation(effect_id)[
        "proof"
    ] is None
    with pytest.raises(TaskStoreError, match="reused with drift"):
        store.stage_acceptance_effect_reconciliation_evidence(
            **{**stage, "body_sha256": "b" * 64}
        )
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="evidence is append-only",
        ):
            conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_reconciliation_evidence
                SET evidence_digest = ?
                WHERE evidence_ref = ?
                """,
                ("sha256:" + "c" * 64, first["evidence_ref"]),
            )


def test_unsigned_safe_retry_marker_is_rejected_by_sqlite(
    tmp_path: Path,
) -> None:
    store, _service_value, _private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/unsigned-retry"
    _unknown_effect(store, effect_id=effect_id)

    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="requires signed reconciliation proof",
        ):
            conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET reason_code =
                    'ACCEPTANCE_EFFECT_RECONCILED_NO_SIDE_EFFECT'
                WHERE effect_id = ?
                """,
                (effect_id,),
            )


def test_schema_readiness_rejects_weakened_same_name_trigger(
    tmp_path: Path,
) -> None:
    store, _service_value, _private_key = _service(tmp_path)
    store.assert_acceptance_effect_reconciliation_ready(
        force_revalidate=True
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            DROP TRIGGER
            trg_task_acceptance_effect_reconciled_retry_requires_proof
            """
        )
        conn.execute(
            """
            CREATE TRIGGER
            trg_task_acceptance_effect_reconciled_retry_requires_proof
            BEFORE UPDATE OF status, reason_code
            ON task_runtime_acceptance_effect_intent
            BEGIN
                SELECT 1;
            END
            """
        )
        conn.commit()

    with pytest.raises(
        TaskStoreError,
        match="physical schema is not ready",
    ):
        store.assert_acceptance_effect_reconciliation_ready(
            force_revalidate=True
        )


def test_query_only_repository_exposes_minimized_general_challenge(
    tmp_path: Path,
) -> None:
    store, _service_value, _private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/query-only"
    _unknown_effect(store, effect_id=effect_id)
    reader = EffectReconciliationQueryRepository(store.db_path)

    candidates = reader.list_acceptance_effect_reconciliation_candidates(
        limit=10
    )
    exact = reader.read_acceptance_effect_reconciliation(effect_id)

    assert len(candidates) == 1
    assert candidates[0]["case"]["case_id"] == exact["case"]["case_id"]
    assert exact["effect"]["last_error_sha256"].startswith("sha256:")
    assert "last_error" not in exact["effect"]
    assert "payload" not in exact["effect"]
    with pytest.raises(TaskStoreError, match="read-only"):
        reader.record_acceptance_effect_reconciliation_proof(effect_id)


def test_persisted_proof_signature_tamper_is_rejected_on_reopen(
    tmp_path: Path,
) -> None:
    store, service, private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/proof-tamper"
    _unknown_effect(store, effect_id=effect_id, status="FAILED")
    request = _request(
        store,
        effect_id=effect_id,
        outcome="SIDE_EFFECT_CONFIRMED",
    )
    _sign(service, private_key, request)
    service.reconcile_acceptance_effect(**request)

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            DROP TRIGGER
            trg_task_acceptance_effect_reconciliation_proof_no_update
            """
        )
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_reconciliation_proof
            SET attestation_signature = ?
            WHERE effect_id = ?
            """,
            ("ed25519:" + "B" * 86 + "==", effect_id),
        )
        conn.commit()

    with pytest.raises(
        TaskStoreError,
        match="proof signature is invalid",
    ):
        store.read_acceptance_effect_reconciliation(
            effect_id,
            case_id=request["case_id"],
        )


def test_signed_positive_attribution_is_atomic_concurrent_and_terminal(
    tmp_path: Path,
) -> None:
    store, service, private_key = _service(tmp_path)
    effect_id = "acceptance-effect://general/side-effect-confirmed"
    _unknown_effect(store, effect_id=effect_id, status="FAILED")
    request = _request(
        store,
        effect_id=effect_id,
        outcome="SIDE_EFFECT_CONFIRMED",
    )
    _sign(service, private_key, request)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            future.result(timeout=15)
            for future in (
                pool.submit(service.reconcile_acceptance_effect, **request),
                pool.submit(service.reconcile_acceptance_effect, **request),
            )
        )
    assert {result["replayed"] for result in results} == {False, True}
    assert len({result["proof_id"] for result in results}) == 1
    committed = store.list_acceptance_effect_intents(
        status="COMMITTED",
        graph_id="graph-cancel",
        node_id="node-1",
        limit=10,
    )[0]
    assert committed["effect_id"] == effect_id
    assert (
        committed["receipt_ref"]
        == request["downstream_receipt_ref"]
    )
    assert store.claim_acceptance_effect_intent(
        owner_id="forbidden-replay",
        effect_id=effect_id,
    ) is None

    forged = {
        **request,
        "downstream_receipt_ref": (
            "gateway-receipt://general-effect/substituted"
        ),
    }
    with pytest.raises(AcceptanceEffectReconciliationError):
        service.reconcile_acceptance_effect(**forged)
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'PENDING', receipt_ref = NULL,
                    reason_code =
                        'ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED',
                    last_error = 'tamper', committed_ts_ms = NULL
                WHERE effect_id = ?
                """,
                (effect_id,),
            )
