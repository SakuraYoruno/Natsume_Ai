from __future__ import annotations

from typing import Any

import pytest

from host_brain.app.accepted_operation_reconciliation_handler import (
    AcceptedOperationReconciliationHandler,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
)


_DIGEST = "sha256:" + "a" * 64
_EVIDENCE_REF = "operation-reconcile-evidence://sha256/" + "a" * 64
_SIGNATURE = "ed25519:" + "A" * 86 + "=="


def _evidence() -> dict[str, Any]:
    return {
        "request_id": "request-operation-handler",
        "evidence_ref": _EVIDENCE_REF,
        "evidence_digest": _DIGEST,
        "ingress_audit_ref": "secure-ingress-audit://operation/handler",
        "body_sha256": "b" * 64,
        "intent_ref": None,
        "reservation_id": "reservation-operation-handler",
        "expected_version": 3,
        "action": "REACQUIRE",
        "reconciler_actor_id": "operator.operation.handler",
        "downstream_receipt_ref": None,
        "attestation_ref": "proof://operation/handler",
        "attestation_key_id": "operation-handler-v1",
        "attestation_signature": _SIGNATURE,
    }


def _payload() -> dict[str, Any]:
    evidence = _evidence()
    return {
        "operator_command_id": "task_operation.reconcile",
        **{
            key: evidence[key]
            for key in (
                "request_id",
                "evidence_ref",
                "evidence_digest",
                "reservation_id",
                "expected_version",
                "action",
                "reconciler_actor_id",
            )
        },
    }


class _EvidenceStore:
    def __init__(self, evidence: dict[str, Any] | None = None) -> None:
        self.evidence = dict(evidence or _evidence())
        self.calls: list[str] = []

    def get_operation_reconciliation_evidence(
        self,
        evidence_ref: str,
    ) -> dict[str, Any]:
        self.calls.append(evidence_ref)
        return dict(self.evidence)


class _Service:
    def __init__(self, *, already_committed: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.already_committed = already_committed

    def reconcile_operation(self, **request: Any) -> dict[str, Any]:
        self.calls.append(dict(request))
        committed = self.already_committed or len(self.calls) > 1
        return {
            "graph_id": "graph-operation-target",
            "graph_revision": 2,
            "operation": {
                "reservation_id": request["reservation_id"],
                "status": "COMMITTED" if committed else "RESERVED",
                "status_version": 5 if committed else 4,
                "fencing_token": 9,
            },
            "replayed": committed,
        }


class _Continuation:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def continue_operation_reacquisition(
        self,
        reconciliation: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(dict(reconciliation))
        return {
            "execution_receipt": {
                "accepted": True,
                "status": "ACCEPTED",
                "graph_id": "graph-operation-target",
                "graph_revision": 2,
            }
        }


@pytest.mark.asyncio
async def test_handler_reopens_evidence_and_continues_target_exactly_once() -> None:
    evidence_store = _EvidenceStore()
    service = _Service()
    continuation = _Continuation()
    handler = AcceptedOperationReconciliationHandler(
        service=service,
        evidence_store=evidence_store,
        continuation_provider=lambda: continuation,
    )

    result = await handler(_payload())

    assert result["status"] == "COMMITTED"
    assert result["operation_status"] == "COMMITTED"
    assert result["evidence_ref"] == _EVIDENCE_REF
    assert result["receipt_ref"].startswith(
        "operation-reconcile://receipt/sha256/"
    )
    assert evidence_store.calls == [_EVIDENCE_REF]
    assert len(service.calls) == 2
    assert service.calls[0]["attestation_signature"] == _SIGNATURE
    assert len(continuation.calls) == 1


@pytest.mark.asyncio
async def test_exact_terminal_replay_does_not_reenter_target_runtime() -> None:
    service = _Service(already_committed=True)
    continuation = _Continuation()
    handler = AcceptedOperationReconciliationHandler(
        service=service,
        evidence_store=_EvidenceStore(),
        continuation_provider=lambda: continuation,
    )

    result = await handler(_payload())

    assert result["exact_replay"] is True
    assert len(service.calls) == 1
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_uncertain_reconciliation_maps_to_unknown_acceptance_effect() -> None:
    class _UncertainService:
        def reconcile_operation(self, **_request: Any) -> None:
            raise GraphCancellationOperationReconciliationError(
                "OPERATION_RECONCILIATION_UNCERTAIN",
                "response was lost",
                side_effects_may_have_run=True,
            )

    handler = AcceptedOperationReconciliationHandler(
        service=_UncertainService(),
        evidence_store=_EvidenceStore(),
        continuation_provider=lambda: _Continuation(),
    )

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        await handler(_payload())


@pytest.mark.asyncio
async def test_effect_evidence_digest_drift_fails_before_service_call() -> None:
    service = _Service()
    payload = _payload()
    payload["evidence_digest"] = "sha256:" + "c" * 64
    handler = AcceptedOperationReconciliationHandler(
        service=service,
        evidence_store=_EvidenceStore(),
        continuation_provider=lambda: _Continuation(),
    )

    with pytest.raises(ValueError):
        await handler(payload)

    assert service.calls == []
