from __future__ import annotations

import base64
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.effect_reconciliation import (
    EFFECT_RECONCILIATION_BINDING_DOMAIN,
    EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
    EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
    GraphCancellationEffectStoreRejected,
    build_graph_cancellation_effect_reconciliation_binding,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.task_runtime.graph_cancellation_effect_reconciliation import (
    EFFECT_RECONCILIATION_INVALID,
    EFFECT_RECONCILIATION_STATE_CONFLICT,
    EFFECT_RECONCILIATION_UNCERTAIN,
    EFFECT_RECONCILIATION_WAKE_FAILED,
    GraphCancellationEffectReconciliationError,
    GraphCancellationEffectReconciliationService,
)


NOW = 1_950_000_000_000
SIGNATURE = "ed25519:" + base64.urlsafe_b64encode(b"e" * 64).decode("ascii")
CASE_DIGEST = "sha256:" + "a" * 64
SUBJECT_DIGEST = "sha256:" + "b" * 64


def _snapshot() -> dict[str, Any]:
    return {
        "case": {
            "schema_version": EFFECT_RECONCILIATION_CASE_SCHEMA_VERSION,
            "case_id": "effect-reconciliation-case://sha256/case-1",
            "intent_ref": "cancel://intent/effect-1",
            "effect_id": "effect://delivery/effect-1",
            "graph_id": "graph-1",
            "graph_revision": 2,
            "node_id": "node-1",
            "attempt_id": "attempt-1",
            "purpose": "PENDING_CANCELLATION",
            "classification": "REPAIRABLE_REQUESTED",
            "case_kind": "RECONCILIATION_REQUIRED",
            "reason_code": "EFFECT_OUTCOME_UNCERTAIN",
            "legacy_decision_ref": None,
            "legacy_decision_payload_sha256": None,
            "snapshot_sha256": CASE_DIGEST,
        },
        "intent": {
            "intent_ref": "cancel://intent/effect-1",
            "request_id": "request-effect-1",
            "payload_sha256": "sha256:" + "c" * 64,
            "status": "REQUESTED",
            "status_version": 0,
            "graph_id": "graph-1",
            "graph_revision": 2,
        },
        "effect": {
            "effect_id": "effect://delivery/effect-1",
            "graph_id": "graph-1",
            "graph_revision": 2,
            "node_id": "node-1",
            "attempt_id": "attempt-1",
            "commit_scope": "GRAPH",
            "effect_type": "surface.reply_delivery",
            "payload_ref": "effect-payload://delivery/effect-1",
            "payload_sha256": "sha256:" + "d" * 64,
            "idempotency_key": "effect-idempotency-1",
            "status": "FAILED",
            "status_version": 2,
            "attempt_count": 1,
            "fencing_token": 1,
            "available_ts_ms": NOW - 1_000,
            "committed_ts_ms": None,
            "created_ts_ms": NOW - 3_000,
            "failed_ts_ms": NOW - 500,
            "lease_owner": "effect-dispatcher-1",
            "lease_expires_ts_ms": None,
            "receipt_ref": None,
            "reason_code": "ACCEPTANCE_EFFECT_HANDLER_FAILED",
            "last_error_sha256": "sha256:" + "e" * 64,
            "updated_ts_ms": NOW - 500,
        },
        "fence": {
            "effect_id": "effect://delivery/effect-1",
            "intent_ref": "cancel://intent/effect-1",
            "observed_status": "CLAIMED",
            "observed_status_version": 1,
            "resolution_status": "RECONCILIATION_REQUIRED",
        },
        "attempt": {
            "attempt_id": "attempt-1",
            "graph_id": "graph-1",
            "graph_revision": 2,
            "node_id": "node-1",
            "status": "SUCCEEDED",
            "status_version": 3,
        },
        "subject_snapshot_sha256": SUBJECT_DIGEST,
        "proof": None,
    }


class _Store:
    def __init__(self, *, mode: str = "success", snapshot: Mapping[str, Any] | None = None) -> None:
        self.mode = mode
        self.snapshot = deepcopy(dict(snapshot or _snapshot()))
        self.calls = 0
        self.kwargs: dict[str, Any] | None = None
        self.outbox: OutboxMessage | None = None
        self.binding_bytes: bytes | None = None

    def read_graph_cancellation_effect_reconciliation(
        self,
        intent_ref: str,
        effect_id: str,
    ) -> Mapping[str, Any]:
        assert intent_ref == self.snapshot["intent"]["intent_ref"]
        assert effect_id == self.snapshot["effect"]["effect_id"]
        return deepcopy(self.snapshot)

    def record_graph_cancellation_effect_reconciliation_proof(
        self,
        intent_ref: str,
        effect_id: str,
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        self.calls += 1
        self.kwargs = dict(kwargs)
        self.outbox = kwargs["outbox"]
        if self.mode == "rejected":
            raise GraphCancellationEffectStoreRejected(
                "TASK_STORE_RECONCILIATION_PROOF_INVALID",
                "signature is invalid",
            )
        if self.mode == "unknown-before":
            raise RuntimeError("database result unavailable")
        proof = self._proof(intent_ref=intent_ref, effect_id=effect_id, **kwargs)
        self.snapshot["proof"] = proof
        if self.mode == "response-loss":
            raise RuntimeError("response lost after proof commit")
        returned = deepcopy(proof)
        if self.mode == "mismatched-return":
            returned["outcome"] = "SIDE_EFFECT_CONFIRMED"
        return {
            "case": deepcopy(self.snapshot["case"]),
            "proof": returned,
            "replayed": False,
        }

    def _proof(self, *, intent_ref: str, effect_id: str, **kwargs: Any) -> dict[str, Any]:
        outbox = kwargs["outbox"]
        outbox_binding = {
            "proof_id": kwargs["proof_id"],
            "aggregate_type": outbox.aggregate_type,
            "aggregate_id": outbox.aggregate_id,
            "aggregate_version": outbox.aggregate_version,
            "graph_revision": outbox.graph_revision,
            "attempt_id": outbox.attempt_id,
            "evidence_refs": sorted(set(outbox.evidence_refs)),
            "event_type": outbox.event_type,
            "payload_ref": outbox.payload_ref,
            "idempotency_key": outbox.idempotency_key,
            "outbox_id": outbox.outbox_id,
        }
        binding = {
            "schema_version": EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
            "domain": EFFECT_RECONCILIATION_BINDING_DOMAIN,
            "subject_snapshot_sha256": kwargs[
                "expected_subject_snapshot_sha256"
            ],
            "case": deepcopy(self.snapshot["case"]),
            "intent": deepcopy(self.snapshot["intent"]),
            "effect": deepcopy(self.snapshot["effect"]),
            "fence": deepcopy(self.snapshot["fence"]),
            "attempt": deepcopy(self.snapshot["attempt"]),
            "resolution": {
                "purpose": kwargs["purpose"],
                "outcome": kwargs["outcome"],
                "target_resolution_status": kwargs["target_resolution_status"],
                "receipt_ref": kwargs["receipt_ref"],
                "executor_quiescence_ref": kwargs[
                    "executor_quiescence_ref"
                ],
            },
            "authority": {
                "reconciler_actor_id": kwargs["reconciler_actor_id"],
                "attestation_ref": kwargs["attestation_ref"],
                "attestation_key_id": kwargs["attestation_key_id"],
                "issued_ts_ms": kwargs["issued_ts_ms"],
            },
            "outbox": outbox_binding,
        }
        self.binding_bytes = build_graph_cancellation_effect_reconciliation_binding(
            binding
        )
        return {
            "schema_version": EFFECT_RECONCILIATION_PROOF_SCHEMA_VERSION,
            "proof_id": kwargs["proof_id"],
            "case_id": kwargs["case_id"],
            "intent_ref": intent_ref,
            "effect_id": effect_id,
            "purpose": kwargs["purpose"],
            "outcome": kwargs["outcome"],
            "target_resolution_status": kwargs["target_resolution_status"],
            "receipt_ref": kwargs["receipt_ref"],
            "executor_quiescence_ref": kwargs["executor_quiescence_ref"],
            "reconciler_actor_id": kwargs["reconciler_actor_id"],
            "attestation_ref": kwargs["attestation_ref"],
            "attestation_key_id": kwargs["attestation_key_id"],
            "attestation_signature": kwargs["attestation_signature"],
            "issued_ts_ms": kwargs["issued_ts_ms"],
            "case_snapshot_sha256": kwargs["expected_case_snapshot_sha256"],
            "subject_snapshot_sha256": kwargs[
                "expected_subject_snapshot_sha256"
            ],
            "proof_digest": "sha256:"
            + hashlib.sha256(self.binding_bytes).hexdigest(),
            "outbox_id": outbox.outbox_id,
            "proof_binding_json": self.binding_bytes.decode("utf-8"),
        }


def _service(
    store: _Store,
    wakes: list[str],
    *,
    wake: Any | None = None,
) -> GraphCancellationEffectReconciliationService:
    return GraphCancellationEffectReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=(wake or (lambda: wakes.append("wake"))),
        clock_ms=lambda: NOW,
    )


def _request(**overrides: Any) -> dict[str, Any]:
    request = {
        "intent_ref": "cancel://intent/effect-1",
        "effect_id": "effect://delivery/effect-1",
        "case_id": "effect-reconciliation-case://sha256/case-1",
        "expected_case_snapshot_sha256": CASE_DIGEST,
        "expected_subject_snapshot_sha256": SUBJECT_DIGEST,
        "purpose": "PENDING_CANCELLATION",
        "outcome": "NO_SIDE_EFFECT",
        "reconciler_actor_id": "reconciler://operator/alice",
        "issued_ts_ms": NOW - 100,
        "downstream_receipt_ref": None,
        "executor_quiescence_ref": None,
        "attestation_ref": "proof://effect/no-side-effect-1",
        "attestation_key_id": "effect-reconciliation-v1",
        "attestation_signature": SIGNATURE,
    }
    request.update(overrides)
    return request


def test_no_side_effect_records_domain_bound_proof_and_wakes() -> None:
    store = _Store()
    wakes: list[str] = []

    result = _service(store, wakes).reconcile_graph_cancellation_effect(**_request())

    assert result["status"] == "PROOF_RECORDED"
    assert result["replayed"] is False
    assert result["proof"]["outcome"] == "NO_SIDE_EFFECT"
    assert result["proof"]["target_resolution_status"] == "CANCELLED"
    assert "proof_digest" not in result["proof"]
    assert "reconciler_actor_id" not in result["proof"]
    assert "attestation_ref" not in result["proof"]
    assert "attestation_signature" not in result["proof"]
    assert wakes == ["wake"]
    assert store.kwargs is not None
    assert store.kwargs["expected_subject_snapshot_sha256"] == SUBJECT_DIGEST
    assert store.outbox is not None
    assert store.outbox.aggregate_type == "GRAPH_CANCELLATION_EFFECT_RECONCILIATION"
    assert store.outbox.aggregate_version == 0
    assert store.outbox.graph_revision == 2
    assert store.outbox.event_type == "GRAPH_CANCELLATION_EFFECT_PROOF_RECORDED"
    assert store.outbox.attempt_id == "attempt-1"
    assert set(store.outbox.evidence_refs) == {
        result["proof"]["proof_id"],
        "effect-reconciliation-case://sha256/case-1",
        "cancel://intent/effect-1",
        "effect://delivery/effect-1",
        "proof://effect/no-side-effect-1",
    }
    assert store.binding_bytes is not None
    assert json.loads(store.binding_bytes)["outbox"]["graph_revision"] == 2
    assert b'"domain":"Natsume.task_runtime.acceptance_effect_reconciliation"' in (
        store.binding_bytes
    )
    assert b"legacy transport failure detail" not in store.binding_bytes


def test_side_effect_confirmed_requires_and_binds_receipt() -> None:
    store = _Store()
    result = _service(store, []).reconcile_graph_cancellation_effect(
        **_request(
            outcome="SIDE_EFFECT_CONFIRMED",
            downstream_receipt_ref="effect-receipt://delivery/confirmed-1",
        )
    )

    assert result["proof"]["target_resolution_status"] == "COMMITTED_EVIDENCE"
    assert result["proof"]["receipt_ref"] == "effect-receipt://delivery/confirmed-1"
    assert store.outbox is not None
    assert "effect-receipt://delivery/confirmed-1" in store.outbox.evidence_refs


@pytest.mark.parametrize(
    ("case_kind", "effect_status", "fence_status", "observed_status"),
    (
        (
            "RECONCILIATION_REQUIRED",
            "FAILED",
            "RECONCILIATION_REQUIRED",
            "CLAIMED",
        ),
        ("LEGACY_FAILED_EVIDENCE", "FAILED", "FAILED_EVIDENCE", "FAILED"),
        (
            "LEGACY_RETRY_PENDING_CANCELLED",
            "PENDING",
            "CANCELLED",
            "PENDING",
        ),
    ),
)
def test_pending_purpose_accepts_each_frozen_case_kind(
    case_kind: str,
    effect_status: str,
    fence_status: str,
    observed_status: str,
) -> None:
    snapshot = _snapshot()
    snapshot["case"]["case_kind"] = case_kind
    snapshot["effect"]["status"] = effect_status
    snapshot["fence"].update(
        {
            "observed_status": observed_status,
            "resolution_status": fence_status,
        }
    )
    if effect_status == "PENDING":
        snapshot["effect"].update(
            {
                "lease_owner": None,
                "failed_ts_ms": None,
            }
        )

    result = _service(_Store(snapshot=snapshot), []).reconcile_graph_cancellation_effect(
        **_request()
    )

    assert result["case"]["case_kind"] == case_kind


def test_case_kind_and_fence_mismatch_is_rejected() -> None:
    snapshot = _snapshot()
    snapshot["case"]["case_kind"] = "LEGACY_FAILED_EVIDENCE"
    store = _Store(snapshot=snapshot)

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**_request())

    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert store.calls == 0


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("intent", "graph_id", "graph-drift"),
        ("case", "node_id", "node-drift"),
        ("fence", "effect_id", "effect://delivery/other"),
        ("attempt", "attempt_id", "attempt-other"),
    ),
)
def test_subject_lineage_drift_is_rejected_before_store_mutation(
    section: str,
    field: str,
    value: Any,
) -> None:
    snapshot = _snapshot()
    snapshot[section][field] = value
    store = _Store(snapshot=snapshot)

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**_request())

    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert store.calls == 0


def test_neutral_binding_rejects_a_dropped_exact_subject_field() -> None:
    store = _Store()
    _service(store, []).reconcile_graph_cancellation_effect(**_request())
    assert store.binding_bytes is not None
    binding = json.loads(store.binding_bytes)
    del binding["effect"]["payload_sha256"]

    with pytest.raises(ValueError):
        build_graph_cancellation_effect_reconciliation_binding(binding)


def test_neutral_binding_rejects_missing_signed_outbox_graph_revision() -> None:
    store = _Store()
    _service(store, []).reconcile_graph_cancellation_effect(**_request())
    assert store.binding_bytes is not None
    binding = json.loads(store.binding_bytes)
    del binding["outbox"]["graph_revision"]

    with pytest.raises(ValueError):
        build_graph_cancellation_effect_reconciliation_binding(binding)


@pytest.mark.parametrize(
    "overrides",
    (
        {"purpose": "EXECUTE_EFFECT"},
        {"outcome": "UNKNOWN"},
        {"outcome": "SIDE_EFFECT_CONFIRMED", "downstream_receipt_ref": None},
        {"outcome": "NO_SIDE_EFFECT", "downstream_receipt_ref": "receipt://wrong/1"},
        {"executor_quiescence_ref": "not-a-durable-ref"},
        {"attestation_signature": "ed25519:not-valid"},
        {"issued_ts_ms": True},
    ),
)
def test_invalid_request_is_rejected_before_store_mutation(
    overrides: Mapping[str, Any],
) -> None:
    store = _Store()

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(
            **_request(**dict(overrides))
        )

    assert raised.value.reason_code == EFFECT_RECONCILIATION_INVALID
    assert raised.value.side_effects_may_have_run is False
    assert store.calls == 0


@pytest.mark.parametrize("outcome", ("NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"))
def test_live_claimed_effect_rejects_every_outcome(outcome: str) -> None:
    snapshot = _snapshot()
    snapshot["effect"].update(
        {
            "status": "CLAIMED",
            "status_version": 1,
            "lease_owner": "effect-dispatcher-live",
            "lease_expires_ts_ms": NOW + 1,
            "failed_ts_ms": None,
            "reason_code": None,
            "last_error_sha256": None,
        }
    )
    request = _request(
        outcome=outcome,
        downstream_receipt_ref=(
            "effect-receipt://delivery/live" if outcome == "SIDE_EFFECT_CONFIRMED" else None
        ),
        executor_quiescence_ref="executor-quiescence://effect/live",
    )
    store = _Store(snapshot=snapshot)

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**request)

    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert store.calls == 0


@pytest.mark.parametrize("outcome", ("NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"))
def test_expired_claimed_effect_requires_quiescence_for_every_outcome(
    outcome: str,
) -> None:
    snapshot = _snapshot()
    snapshot["effect"].update(
        {
            "status": "CLAIMED",
            "status_version": 1,
            "lease_owner": "effect-dispatcher-expired",
            "lease_expires_ts_ms": NOW,
            "failed_ts_ms": None,
            "reason_code": None,
            "last_error_sha256": None,
        }
    )
    store = _Store(snapshot=snapshot)
    request = _request(
        outcome=outcome,
        downstream_receipt_ref=(
            "effect-receipt://delivery/expired"
            if outcome == "SIDE_EFFECT_CONFIRMED"
            else None
        ),
    )

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**request)

    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert store.calls == 0

    result = _service(store, []).reconcile_graph_cancellation_effect(
        **{
            **request,
            "executor_quiescence_ref": "executor-quiescence://effect/expired",
        }
    )
    assert result["proof"]["executor_quiescence_ref"] == (
        "executor-quiescence://effect/expired"
    )


def test_response_loss_exactly_rereads_same_proof() -> None:
    store = _Store(mode="response-loss")
    wakes: list[str] = []

    result = _service(store, wakes).reconcile_graph_cancellation_effect(**_request())

    assert result["replayed"] is True
    assert store.calls == 1
    assert wakes == ["wake"]


def test_unknown_write_without_exact_proof_is_uncertain() -> None:
    store = _Store(mode="unknown-before")

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**_request())

    assert raised.value.reason_code == EFFECT_RECONCILIATION_UNCERTAIN
    assert raised.value.side_effects_may_have_run is True


def test_definite_store_rejection_preserves_no_commit_certainty() -> None:
    store = _Store(mode="rejected")

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**_request())

    assert raised.value.reason_code == "TASK_STORE_RECONCILIATION_PROOF_INVALID"
    assert raised.value.side_effects_may_have_run is False


def test_exact_pending_proof_replays_after_intent_finalized_without_new_write() -> None:
    store = _Store()
    service = _service(store, [])
    first = service.reconcile_graph_cancellation_effect(**_request())
    store.snapshot["intent"]["status"] = "FINALIZED"
    store.snapshot["intent"]["status_version"] = 1
    store.calls = 0

    replay = service.reconcile_graph_cancellation_effect(**_request())

    assert replay["replayed"] is True
    assert replay["proof"] == first["proof"]
    assert store.calls == 0


def test_existing_proof_requires_exact_raw_signature_replay() -> None:
    store = _Store()
    service = _service(store, [])
    service.reconcile_graph_cancellation_effect(**_request())
    store.calls = 0
    different_signature = "ed25519:" + base64.urlsafe_b64encode(
        b"x" * 64
    ).decode("ascii")

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        service.reconcile_graph_cancellation_effect(
            **_request(attestation_signature=different_signature)
        )

    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert raised.value.side_effects_may_have_run is False
    assert store.calls == 0


def test_new_pending_proof_is_forbidden_after_finalization() -> None:
    snapshot = _snapshot()
    snapshot["intent"].update({"status": "FINALIZED", "status_version": 1})
    store = _Store(snapshot=snapshot)

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**_request())

    assert raised.value.reason_code == EFFECT_RECONCILIATION_STATE_CONFLICT
    assert store.calls == 0


def test_legacy_finalized_correction_binds_decision_and_does_not_wake() -> None:
    snapshot = _snapshot()
    snapshot["case"].update(
        {
            "purpose": "LEGACY_FINALIZED_CORRECTION",
            "classification": "QUARANTINED_FINALIZED",
            "case_kind": "LEGACY_RETRY_PENDING_CANCELLED",
            "legacy_decision_ref": "task-cancel-decision://sha256/legacy-1",
            "legacy_decision_payload_sha256": "sha256:" + "f" * 64,
        }
    )
    snapshot["intent"].update({"status": "FINALIZED", "status_version": 1})
    snapshot["effect"].update(
        {
            "status": "PENDING",
            "lease_owner": None,
            "lease_expires_ts_ms": None,
            "failed_ts_ms": None,
        }
    )
    snapshot["fence"].update(
        {
            "observed_status": "PENDING",
            "observed_status_version": 2,
            "resolution_status": "CANCELLED",
        }
    )
    store = _Store(snapshot=snapshot)
    wakes: list[str] = []

    result = _service(store, wakes).reconcile_graph_cancellation_effect(
        **_request(purpose="LEGACY_FINALIZED_CORRECTION")
    )

    assert result["status"] == "CORRECTION_PROOF_RECORDED"
    assert result["case"]["classification"] == "QUARANTINED_FINALIZED"
    assert wakes == []
    assert store.binding_bytes is not None
    assert b"task-cancel-decision://sha256/legacy-1" in store.binding_bytes


def test_wake_failure_reports_proof_commit_without_claiming_completion() -> None:
    store = _Store()

    def fail_wake() -> None:
        raise RuntimeError("wake unavailable")

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, [], wake=fail_wake).reconcile_graph_cancellation_effect(
            **_request()
        )

    assert raised.value.reason_code == EFFECT_RECONCILIATION_WAKE_FAILED
    assert raised.value.side_effects_may_have_run is True
    assert store.snapshot["proof"] is not None


def test_store_returning_mismatched_proof_is_uncertain() -> None:
    store = _Store(mode="mismatched-return")

    with pytest.raises(GraphCancellationEffectReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_effect(**_request())

    assert raised.value.reason_code == EFFECT_RECONCILIATION_UNCERTAIN
    assert raised.value.side_effects_may_have_run is True
