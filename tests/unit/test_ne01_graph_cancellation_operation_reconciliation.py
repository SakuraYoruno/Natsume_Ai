from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    GraphCancellationOperationStoreRejected,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
    GraphCancellationOperationReconciliationService,
    OPERATION_RECONCILIATION_INVALID,
    OPERATION_RECONCILIATION_UNCERTAIN,
)


SIGNATURE = "ed25519:" + base64.urlsafe_b64encode(b"s" * 64).decode("ascii")


class _Store:
    def __init__(self, *, mode: str = "success", intent_status: str = "REQUESTED") -> None:
        self.mode = mode
        self.calls = 0
        self.outbox: Any | None = None
        self.operation: dict[str, Any] = {
            "reservation_id": "reservation-1",
            "attempt_id": "attempt-1",
            "status": "RECOVERY_REQUIRED",
            "status_version": 4,
        }
        self.attempt = {
            "attempt_id": "attempt-1",
            "graph_id": "graph-1",
            "graph_revision": 2,
        }
        self.intent = {
            "intent_ref": "cancel://intent/1",
            "graph_id": "graph-1",
            "graph_revision": 2,
            "status": intent_status,
        }

    def get_operation_reservation(self, reservation_id: str) -> Mapping[str, Any]:
        assert reservation_id == "reservation-1"
        return dict(self.operation)

    def get_attempt(self, attempt_id: str) -> Mapping[str, Any]:
        assert attempt_id == "attempt-1"
        return dict(self.attempt)

    def get_graph_cancellation_intent(self, intent_ref: str) -> Mapping[str, Any]:
        assert intent_ref == "cancel://intent/1"
        return dict(self.intent)

    def reconcile_graph_cancellation_operation(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self.calls += 1
        self.outbox = kwargs["outbox"]
        if self.mode == "rejected":
            raise GraphCancellationOperationStoreRejected(
                "TASK_STORE_RECONCILIATION_PROOF_INVALID",
                "signature is invalid",
            )
        if self.mode == "unknown-before":
            raise RuntimeError("database response unavailable")
        action = kwargs["action"]
        self.operation.update(
            {
                "status": "COMMITTED" if action == "COMMIT" else "RELEASED",
                "status_version": kwargs["expected_version"] + 1,
                "receipt_ref": kwargs["downstream_receipt_ref"],
                "reconciliation_action": action,
                "reconciliation_outcome": (
                    "SIDE_EFFECT_CONFIRMED" if action == "COMMIT" else "NO_SIDE_EFFECT"
                ),
                "reconciler_actor_id": kwargs["reconciler_actor_id"],
                "reconciliation_proof_ref": kwargs["attestation_ref"],
                "reconciliation_proof_key_id": kwargs["attestation_key_id"],
                "reconciliation_proof_signature": kwargs["attestation_signature"],
                "reconciliation_recovery_version": kwargs["expected_version"],
            }
        )
        if self.mode == "response-loss":
            raise RuntimeError("response lost after commit")
        return dict(self.operation)


def _service(store: _Store, wakes: list[str]) -> GraphCancellationOperationReconciliationService:
    return GraphCancellationOperationReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: wakes.append("wake"),
    )


def _request(**overrides: Any) -> dict[str, Any]:
    request = {
        "intent_ref": "cancel://intent/1",
        "reservation_id": "reservation-1",
        "expected_version": 4,
        "action": "RELEASE",
        "reconciler_actor_id": "reconciler://operator/alice",
        "downstream_receipt_ref": None,
        "attestation_ref": "proof://operation/release-1",
        "attestation_key_id": "reconciliation-v1",
        "attestation_signature": SIGNATURE,
    }
    request.update(overrides)
    return request


def test_release_builds_canonical_outbox_and_wakes_continuation() -> None:
    store = _Store()
    wakes: list[str] = []

    result = _service(store, wakes).reconcile_graph_cancellation_operation(**_request())

    assert result["replayed"] is False
    assert result["operation"]["status"] == "RELEASED"
    assert set(result["operation"]) == {
        "reservation_id",
        "status",
        "status_version",
        "reconciliation_action",
        "reconciliation_outcome",
        "receipt_ref",
        "attestation_ref",
        "attestation_key_id",
    }
    assert "reconciliation_proof_signature" not in result["operation"]
    assert "owner_id" not in result["operation"]
    assert wakes == ["wake"]
    assert store.outbox.aggregate_type == "OPERATION_RESERVATION"
    assert store.outbox.aggregate_version == 5
    assert store.outbox.event_type == "OPERATION_RECOVERY_RELEASE"
    assert store.outbox.attempt_id == "attempt-1"
    assert store.outbox.evidence_refs == (
        "cancel://intent/1",
        "proof://operation/release-1",
    )


def test_commit_requires_and_binds_durable_receipt() -> None:
    store = _Store()
    wakes: list[str] = []
    result = _service(store, wakes).reconcile_graph_cancellation_operation(
        **_request(
            action="COMMIT",
            downstream_receipt_ref="receipt://downstream/confirmed-1",
        )
    )

    assert result["operation"]["status"] == "COMMITTED"
    assert result["operation"]["receipt_ref"] == "receipt://downstream/confirmed-1"
    assert store.outbox.evidence_refs[-1] == "receipt://downstream/confirmed-1"


@pytest.mark.parametrize(
    "overrides",
    (
        {"action": "REACQUIRE"},
        {"action": "COMMIT", "downstream_receipt_ref": None},
        {"action": "RELEASE", "downstream_receipt_ref": "receipt://unexpected/1"},
        {"attestation_signature": "ed25519:not-a-signature"},
        {"expected_version": True},
    ),
)
def test_invalid_request_is_definitely_rejected_before_store_mutation(
    overrides: Mapping[str, Any],
) -> None:
    store = _Store()

    with pytest.raises(GraphCancellationOperationReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_operation(
            **_request(**dict(overrides))
        )

    assert raised.value.reason_code == OPERATION_RECONCILIATION_INVALID
    assert raised.value.side_effects_may_have_run is False
    assert store.calls == 0


def test_store_signature_rejection_remains_definite() -> None:
    store = _Store(mode="rejected")

    with pytest.raises(GraphCancellationOperationReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_operation(**_request())

    assert raised.value.reason_code == "TASK_STORE_RECONCILIATION_PROOF_INVALID"
    assert raised.value.side_effects_may_have_run is False


def test_response_loss_exactly_replays_terminal_signed_result() -> None:
    store = _Store(mode="response-loss")
    wakes: list[str] = []

    result = _service(store, wakes).reconcile_graph_cancellation_operation(**_request())

    assert result["replayed"] is True
    assert result["operation"]["status"] == "RELEASED"
    assert store.calls == 1
    assert wakes == ["wake"]


def test_unknown_store_failure_without_exact_terminal_proof_is_uncertain() -> None:
    store = _Store(mode="unknown-before")

    with pytest.raises(GraphCancellationOperationReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_operation(**_request())

    assert raised.value.reason_code == OPERATION_RECONCILIATION_UNCERTAIN
    assert raised.value.side_effects_may_have_run is True


def test_exact_terminal_replay_survives_cancellation_finalization() -> None:
    store = _Store(intent_status="FINALIZED")
    store.reconcile_graph_cancellation_operation(
        "cancel://intent/1",
        "reservation-1",
        action="RELEASE",
        expected_version=4,
        outbox=object(),
        reconciler_actor_id="reconciler://operator/alice",
        downstream_receipt_ref=None,
        attestation_ref="proof://operation/release-1",
        attestation_key_id="reconciliation-v1",
        attestation_signature=SIGNATURE,
    )
    store.calls = 0

    result = _service(store, []).reconcile_graph_cancellation_operation(**_request())

    assert result["replayed"] is True
    assert store.calls == 0


def test_mismatched_terminal_proof_is_not_treated_as_response_loss_replay() -> None:
    store = _Store(intent_status="FINALIZED")
    store.reconcile_graph_cancellation_operation(
        "cancel://intent/1",
        "reservation-1",
        action="RELEASE",
        expected_version=4,
        outbox=object(),
        reconciler_actor_id="reconciler://operator/alice",
        downstream_receipt_ref=None,
        attestation_ref="proof://operation/other",
        attestation_key_id="reconciliation-v1",
        attestation_signature=SIGNATURE,
    )
    store.calls = 0

    with pytest.raises(GraphCancellationOperationReconciliationError) as raised:
        _service(store, []).reconcile_graph_cancellation_operation(**_request())

    assert raised.value.side_effects_may_have_run is False
    assert store.calls == 0
