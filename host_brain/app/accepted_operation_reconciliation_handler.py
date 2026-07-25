"""Acceptance-bound execution of one signed Operation reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
import json
from typing import Any

from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationError,
)


OPERATION_RECONCILIATION_EFFECT_TYPE = "task.operation_reconciliation"


class AcceptedOperationReconciliationHandler:
    """Apply or exactly replay a signed request only after Task acceptance."""

    __slots__ = ("_continuation_provider", "_evidence_store", "_service")

    def __init__(
        self,
        *,
        service: Any,
        evidence_store: Any,
        continuation_provider: Callable[[], Any],
    ) -> None:
        if not callable(getattr(service, "reconcile_operation", None)):
            raise TypeError(
                "operation reconciliation handler requires reconcile_operation"
            )
        if not callable(continuation_provider):
            raise TypeError(
                "operation reconciliation continuation provider must be callable"
            )
        if not callable(
            getattr(
                evidence_store,
                "get_operation_reconciliation_evidence",
                None,
            )
        ):
            raise TypeError(
                "operation reconciliation handler requires evidence store"
            )
        self._service = service
        self._evidence_store = evidence_store
        self._continuation_provider = continuation_provider

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._apply(payload)

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._apply(payload)

    async def _apply(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        durable_request = _request(payload)
        evidence_raw = (
            self._evidence_store.get_operation_reconciliation_evidence(
                durable_request["evidence_ref"]
            )
        )
        if not isinstance(evidence_raw, Mapping):
            raise ValueError(
                "operation reconciliation evidence could not be reopened"
            )
        evidence = dict(evidence_raw)
        if (
            _durable_request_from_evidence(evidence) != durable_request
            or str(evidence.get("request_id") or "").strip()
            != durable_request["request_id"]
        ):
            raise ValueError(
                "operation reconciliation evidence binding drifted"
            )
        request = {
            key: evidence.get(key)
            for key in (
                "intent_ref",
                "reservation_id",
                "expected_version",
                "action",
                "reconciler_actor_id",
                "downstream_receipt_ref",
                "attestation_ref",
                "attestation_key_id",
                "attestation_signature",
            )
        }
        try:
            reconciliation_raw = self._service.reconcile_operation(**request)
            reconciliation = (
                await reconciliation_raw
                if inspect.isawaitable(reconciliation_raw)
                else reconciliation_raw
            )
        except GraphCancellationOperationReconciliationError as exc:
            if exc.side_effects_may_have_run:
                raise AcceptanceEffectOutcomeUnknown(
                    "signed Operation reconciliation outcome is uncertain"
                ) from exc
            raise
        if not isinstance(reconciliation, Mapping):
            raise TypeError("operation reconciliation result must be a mapping")
        result = dict(reconciliation)
        continuation: dict[str, Any] | None = None
        initial_operation_raw = result.get("operation")
        initial_operation = (
            dict(initial_operation_raw)
            if isinstance(initial_operation_raw, Mapping)
            else {}
        )
        reacquisition_continuation = (
            request["action"] == "REACQUIRE"
            and str(initial_operation.get("status") or "").upper()
            != "COMMITTED"
        )
        checkpoint_continuation = (
            request["action"] == "COMMIT"
            and request["intent_ref"] is None
            and str(initial_operation.get("status") or "").upper()
            == "COMMITTED"
        )
        if reacquisition_continuation or checkpoint_continuation:
            runtime = self._continuation_provider()
            continuation_method = (
                "continue_operation_reacquisition"
                if reacquisition_continuation
                else "continue_operation_reconciliation"
            )
            continue_target = getattr(
                runtime,
                continuation_method,
                None,
            )
            if not callable(continue_target):
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled Operation has no app-local continuation owner"
                )
            try:
                continued_raw = continue_target(result)
                continued = (
                    await continued_raw
                    if inspect.isawaitable(continued_raw)
                    else continued_raw
                )
            except Exception as exc:
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled Operation continuation outcome is uncertain"
                ) from exc
            if not isinstance(continued, Mapping):
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled Operation continuation result is invalid"
                )
            continuation = dict(continued)
            receipt_raw = continuation.get("execution_receipt")
            receipt = (
                dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
            )
            if (
                receipt.get("accepted") is not True
                or str(receipt.get("status") or "").strip().upper()
                != "ACCEPTED"
                or str(receipt.get("graph_id") or "").strip()
                != str(result.get("graph_id") or "").strip()
                or receipt.get("graph_revision") != result.get("graph_revision")
            ):
                raise AcceptanceEffectOutcomeUnknown(
                    "reconciled target graph has no exact accepted receipt"
                )
            if reacquisition_continuation:
                try:
                    settled_raw = self._service.reconcile_operation(**request)
                    settled = (
                        await settled_raw
                        if inspect.isawaitable(settled_raw)
                        else settled_raw
                    )
                except Exception as exc:
                    raise AcceptanceEffectOutcomeUnknown(
                        "reacquired Operation final state cannot be reopened"
                    ) from exc
                if not isinstance(settled, Mapping):
                    raise AcceptanceEffectOutcomeUnknown(
                        "reacquired Operation final state is invalid"
                    )
                result = dict(settled)
        operation_raw = result.get("operation")
        operation = (
            dict(operation_raw) if isinstance(operation_raw, Mapping) else {}
        )
        receipt_ref = operation_reconciliation_handler_receipt_ref(
            request=request,
            operation=operation,
        )
        return {
            "receipt_ref": receipt_ref,
            "status": "COMMITTED",
            "reservation_id": request["reservation_id"],
            "action": request["action"],
            "evidence_ref": durable_request["evidence_ref"],
            "operation_status": str(operation.get("status") or "").upper(),
            "operation_status_version": operation.get("status_version"),
            "target_continuation": continuation,
            "exact_replay": bool(result.get("replayed")),
        }


def operation_reconciliation_handler_receipt_ref(
    *,
    request: Mapping[str, Any],
    operation: Mapping[str, Any],
) -> str:
    checkpoint_commit = (
        str(request.get("action") or "").strip().upper() == "COMMIT"
        and not str(request.get("intent_ref") or "").strip()
    )
    material: dict[str, Any] = {
        "schema_version": (
            "Natsume.task.operation_reconciliation_receipt.v2"
            if checkpoint_commit
            else "Natsume.task.operation_reconciliation_receipt.v1"
        ),
        "reservation_id": str(request.get("reservation_id") or "").strip(),
        "expected_version": request.get("expected_version"),
        "action": str(request.get("action") or "").strip().upper(),
        "reconciler_actor_id": str(
            request.get("reconciler_actor_id") or ""
        ).strip(),
        "attestation_ref": str(request.get("attestation_ref") or "").strip(),
        "attestation_key_id": str(
            request.get("attestation_key_id") or ""
        ).strip(),
        "operation_status": str(operation.get("status") or "").strip().upper(),
        "operation_status_version": operation.get("status_version"),
        "operation_fencing_token": operation.get("fencing_token"),
    }
    if checkpoint_commit:
        material.update(
            {
                "downstream_receipt_ref": str(
                    request.get("downstream_receipt_ref") or ""
                ).strip(),
                "operation_receipt_ref": str(
                    operation.get("receipt_ref") or ""
                ).strip(),
                "operation_reconciliation_action": str(
                    operation.get("reconciliation_action") or ""
                )
                .strip()
                .upper(),
                "operation_reconciliation_outcome": str(
                    operation.get("reconciliation_outcome") or ""
                )
                .strip()
                .upper(),
            }
        )
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "operation-reconcile://receipt/sha256/" + hashlib.sha256(
        encoded
    ).hexdigest()


def _request(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload or {})
    expected_version = value.get("expected_version")
    evidence_ref = str(value.get("evidence_ref") or "").strip()
    evidence_digest = str(
        value.get("evidence_digest") or ""
    ).strip().lower()
    if evidence_digest.startswith("sha256:"):
        evidence_digest = evidence_digest.removeprefix("sha256:")
    request = {
        "request_id": str(value.get("request_id") or "").strip(),
        "evidence_ref": evidence_ref,
        "evidence_digest": f"sha256:{evidence_digest}",
        "reservation_id": str(value.get("reservation_id") or "").strip(),
        "expected_version": expected_version,
        "action": str(value.get("action") or "").strip().upper(),
        "reconciler_actor_id": str(
            value.get("reconciler_actor_id") or ""
        ).strip(),
    }
    if (
        str(value.get("operator_command_id") or "").strip()
        != "task_operation.reconcile"
        or not request["request_id"]
        or not evidence_ref.startswith(
            "operation-reconcile-evidence://sha256/"
        )
        or evidence_ref.rsplit("/", 1)[-1] != evidence_digest
        or len(evidence_digest) != 64
        or any(char not in "0123456789abcdef" for char in evidence_digest)
        or not request["reservation_id"]
        or type(expected_version) is not int
        or expected_version < 0
        or request["action"] not in {"COMMIT", "RELEASE", "REACQUIRE"}
        or not request["reconciler_actor_id"]
    ):
        raise ValueError("operation reconciliation effect payload is invalid")
    return request


def _durable_request_from_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "request_id": str(value.get("request_id") or "").strip(),
        "evidence_ref": str(value.get("evidence_ref") or "").strip(),
        "evidence_digest": str(
            value.get("evidence_digest") or ""
        ).strip().lower(),
        "reservation_id": str(value.get("reservation_id") or "").strip(),
        "expected_version": value.get("expected_version"),
        "action": str(value.get("action") or "").strip().upper(),
        "reconciler_actor_id": str(
            value.get("reconciler_actor_id") or ""
        ).strip(),
    }


__all__ = [
    "AcceptedOperationReconciliationHandler",
    "OPERATION_RECONCILIATION_EFFECT_TYPE",
    "operation_reconciliation_handler_receipt_ref",
]
