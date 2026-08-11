"""Signed settlement of uncertain operations blocking Graph cancellation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import re
from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    GraphCancellationOperationStoreRejected,
)

from .ports import (
    GraphCancellationOperationReconciliationStorePort,
    TaskOutboxFactoryPort,
)


OPERATION_RECONCILIATION_INVALID = "OPERATION_RECONCILIATION_INVALID"
OPERATION_RECONCILIATION_REJECTED = "OPERATION_RECONCILIATION_REJECTED"
OPERATION_RECONCILIATION_STATE_CONFLICT = (
    "OPERATION_RECONCILIATION_STATE_CONFLICT"
)
OPERATION_RECONCILIATION_UNCERTAIN = "OPERATION_RECONCILIATION_UNCERTAIN"
OPERATION_RECONCILIATION_WAKE_FAILED = "OPERATION_RECONCILIATION_WAKE_FAILED"

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SIGNATURE_PATTERN = re.compile(r"^ed25519:[A-Za-z0-9_-]{86}==$")


class GraphCancellationOperationReconciliationError(RuntimeError):
    """Nominal application failure with explicit mutation uncertainty."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        side_effects_may_have_run: bool,
    ) -> None:
        self.reason_code = str(reason_code or OPERATION_RECONCILIATION_REJECTED)
        self.detail = str(detail or "operation reconciliation failed")
        self.side_effects_may_have_run = bool(side_effects_may_have_run)
        super().__init__(f"{self.reason_code}: {self.detail}")


class GraphCancellationOperationReconciliationService:
    """Apply one authority-signed COMMIT/RELEASE and wake cancellation."""

    __slots__ = ("_outbox_factory", "_store", "_wake_cancellation")

    def __init__(
        self,
        *,
        task_store: GraphCancellationOperationReconciliationStorePort,
        outbox_factory: TaskOutboxFactoryPort,
        wake_cancellation: Callable[[], None],
    ) -> None:
        if not callable(wake_cancellation):
            raise TypeError("wake_cancellation must be callable")
        self._store = task_store
        self._outbox_factory = outbox_factory
        self._wake_cancellation = wake_cancellation

    def reconcile_graph_cancellation_operation(
        self,
        *,
        intent_ref: str,
        reservation_id: str,
        expected_version: int,
        action: str,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None = None,
        attestation_ref: str,
        attestation_key_id: str,
        attestation_signature: str,
    ) -> Mapping[str, Any]:
        normalized = self._normalize_request(
            intent_ref=intent_ref,
            reservation_id=reservation_id,
            expected_version=expected_version,
            action=action,
            reconciler_actor_id=reconciler_actor_id,
            downstream_receipt_ref=downstream_receipt_ref,
            attestation_ref=attestation_ref,
            attestation_key_id=attestation_key_id,
            attestation_signature=attestation_signature,
        )
        try:
            operation = self._record(
                self._store.get_operation_reservation(normalized["reservation_id"]),
                field_name="OperationReservation",
            )
            intent = self._validate_lineage(
                intent_ref=normalized["intent_ref"],
                operation=operation,
                allow_finalized=True,
            )
        except GraphCancellationOperationReconciliationError:
            raise
        except Exception as exc:
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_REJECTED,
                "durable reconciliation prerequisites could not be read",
                side_effects_may_have_run=False,
            ) from exc

        if self._is_exact_terminal(operation, normalized):
            return self._success(
                operation=operation,
                intent=intent,
                replayed=True,
            )
        self._require_recovery_candidate(operation, intent=intent, request=normalized)
        try:
            outbox = self._build_outbox(operation=operation, request=normalized)
        except Exception as exc:
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_REJECTED,
                "canonical reconciliation Outbox could not be constructed",
                side_effects_may_have_run=False,
            ) from exc
        try:
            settled_raw = self._store.reconcile_graph_cancellation_operation(
                normalized["intent_ref"],
                normalized["reservation_id"],
                action=normalized["action"],
                expected_version=normalized["expected_version"],
                outbox=outbox,
                reconciler_actor_id=normalized["reconciler_actor_id"],
                downstream_receipt_ref=normalized["downstream_receipt_ref"],
                attestation_ref=normalized["attestation_ref"],
                attestation_key_id=normalized["attestation_key_id"],
                attestation_signature=normalized["attestation_signature"],
            )
        except GraphCancellationOperationStoreRejected as exc:
            raise GraphCancellationOperationReconciliationError(
                exc.reason_code,
                exc.detail,
                side_effects_may_have_run=False,
            ) from exc
        except Exception as exc:
            replay = self._terminal_after_unknown_outcome(normalized)
            if replay is not None:
                replay_operation, replay_intent = replay
                return self._success(
                    operation=replay_operation,
                    intent=replay_intent,
                    replayed=True,
                )
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "operation reconciliation outcome is uncertain",
                side_effects_may_have_run=True,
            ) from exc

        try:
            settled = self._record(settled_raw, field_name="settled OperationReservation")
            if not self._is_exact_terminal(settled, normalized):
                raise ValueError("settled operation differs from the signed request")
        except Exception as exc:
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_UNCERTAIN,
                "store returned a non-terminal or mismatched reconciliation result",
                side_effects_may_have_run=True,
            ) from exc
        return self._success(operation=settled, intent=intent, replayed=False)

    def _terminal_after_unknown_outcome(
        self,
        request: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
        try:
            operation = self._record(
                self._store.get_operation_reservation(request["reservation_id"]),
                field_name="OperationReservation replay",
            )
            if not self._is_exact_terminal(operation, request):
                return None
            intent = self._validate_lineage(
                intent_ref=request["intent_ref"],
                operation=operation,
                allow_finalized=True,
            )
            return operation, intent
        except Exception:
            return None

    def _validate_lineage(
        self,
        *,
        intent_ref: str,
        operation: Mapping[str, Any],
        allow_finalized: bool,
    ) -> Mapping[str, Any]:
        attempt_id = self._text(operation.get("attempt_id"))
        if not attempt_id:
            self._reject("operation reservation is not attempt-bound")
        attempt = self._record(
            self._store.get_attempt(attempt_id),
            field_name="operation Attempt",
        )
        intent = self._record(
            self._store.get_graph_cancellation_intent(intent_ref),
            field_name="Graph cancellation intent",
        )
        status = self._text(intent.get("status")).upper()
        allowed_statuses = {"REQUESTED", "FINALIZED"} if allow_finalized else {"REQUESTED"}
        if (
            self._text(intent.get("intent_ref")) != intent_ref
            or status not in allowed_statuses
            or self._text(intent.get("graph_id"))
            != self._text(attempt.get("graph_id"))
            or self._positive_int(intent.get("graph_revision"))
            != self._positive_int(attempt.get("graph_revision"))
        ):
            self._reject("operation does not belong to the exact cancellation graph revision")
        return intent

    def _require_recovery_candidate(
        self,
        operation: Mapping[str, Any],
        *,
        intent: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> None:
        if self._text(intent.get("status")).upper() != "REQUESTED":
            self._reject("new reconciliation requires a REQUESTED cancellation intent")
        if (
            self._text(operation.get("status")).upper() != "RECOVERY_REQUIRED"
            or self._non_negative_int(operation.get("status_version"))
            != request["expected_version"]
        ):
            self._reject("operation is not the exact RECOVERY_REQUIRED version")

    def _is_exact_terminal(
        self,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> bool:
        action = request["action"]
        expected_status = "COMMITTED" if action == "COMMIT" else "RELEASED"
        expected_outcome = (
            "SIDE_EFFECT_CONFIRMED" if action == "COMMIT" else "NO_SIDE_EFFECT"
        )
        expected_receipt = request["downstream_receipt_ref"] if action == "COMMIT" else None
        return (
            self._text(operation.get("reservation_id")) == request["reservation_id"]
            and self._text(operation.get("status")).upper() == expected_status
            and self._text(operation.get("reconciliation_action")).upper() == action
            and self._text(operation.get("reconciliation_outcome")).upper()
            == expected_outcome
            and self._optional_text(operation.get("receipt_ref")) == expected_receipt
            and self._text(operation.get("reconciler_actor_id"))
            == request["reconciler_actor_id"]
            and self._text(operation.get("reconciliation_proof_ref"))
            == request["attestation_ref"]
            and self._text(operation.get("reconciliation_proof_key_id"))
            == request["attestation_key_id"]
            and self._text(operation.get("reconciliation_proof_signature"))
            == request["attestation_signature"]
            and self._non_negative_int(
                operation.get("reconciliation_recovery_version")
            )
            == request["expected_version"]
            and self._non_negative_int(operation.get("status_version"))
            == request["expected_version"] + 1
        )

    def _build_outbox(
        self,
        *,
        operation: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> Any:
        version = request["expected_version"] + 1
        material = "\n".join(
            (
                request["intent_ref"],
                request["reservation_id"],
                str(request["expected_version"]),
                request["action"],
                request["reconciler_actor_id"],
                request["attestation_ref"],
                request["attestation_key_id"],
                request["downstream_receipt_ref"] or "",
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return self._outbox_factory.create(
            aggregate_type="OPERATION_RESERVATION",
            aggregate_id=request["reservation_id"],
            aggregate_version=version,
            event_type=f"OPERATION_RECOVERY_{request['action']}",
            payload_ref=(
                "taskstore://event/OPERATION_RESERVATION/"
                f"{request['reservation_id']}/{version}"
            ),
            idempotency_key=f"operation-reconciliation:{digest}",
            attempt_id=self._text(operation.get("attempt_id")) or None,
            evidence_refs=tuple(
                item
                for item in (
                    request["intent_ref"],
                    request["attestation_ref"],
                    request["downstream_receipt_ref"],
                )
                if item
            ),
            outbox_id=f"outbox::operation-reconciliation::{digest}",
        )

    def _success(
        self,
        *,
        operation: Mapping[str, Any],
        intent: Mapping[str, Any],
        replayed: bool,
    ) -> Mapping[str, Any]:
        try:
            self._wake_cancellation()
        except Exception as exc:
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_WAKE_FAILED,
                "operation settled but cancellation continuation could not be woken",
                side_effects_may_have_run=True,
            ) from exc
        operation_projection = {
            "reservation_id": self._text(operation.get("reservation_id")),
            "status": self._text(operation.get("status")).upper(),
            "status_version": self._non_negative_int(
                operation.get("status_version")
            ),
            "reconciliation_action": self._text(
                operation.get("reconciliation_action")
            ).upper(),
            "reconciliation_outcome": self._text(
                operation.get("reconciliation_outcome")
            ).upper(),
            "receipt_ref": self._optional_text(operation.get("receipt_ref")),
            "attestation_ref": self._text(
                operation.get("reconciliation_proof_ref")
            ),
            "attestation_key_id": self._text(
                operation.get("reconciliation_proof_key_id")
            ),
        }
        return {
            "status": "RECONCILED",
            "intent_ref": self._text(intent.get("intent_ref")),
            "graph_id": self._text(intent.get("graph_id")),
            "graph_revision": self._positive_int(intent.get("graph_revision")),
            "operation": operation_projection,
            "replayed": bool(replayed),
        }

    @classmethod
    def _normalize_request(cls, **raw: Any) -> dict[str, Any]:
        intent_ref = cls._text(raw.get("intent_ref"))
        reservation_id = cls._text(raw.get("reservation_id"))
        action = cls._text(raw.get("action")).upper()
        actor = cls._text(raw.get("reconciler_actor_id"))
        attestation_ref = cls._text(raw.get("attestation_ref"))
        key_id = cls._text(raw.get("attestation_key_id"))
        signature = cls._text(raw.get("attestation_signature"))
        receipt = cls._optional_text(raw.get("downstream_receipt_ref"))
        expected_version = raw.get("expected_version")
        if (
            not intent_ref
            or not reservation_id
            or type(expected_version) is not int
            or expected_version < 0
            or action not in {"COMMIT", "RELEASE"}
            or not actor
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(attestation_ref)
            or not key_id
            or not _SIGNATURE_PATTERN.fullmatch(signature)
            or (action == "COMMIT" and not receipt)
            or (receipt is not None and not _DURABLE_REFERENCE_PATTERN.fullmatch(receipt))
            or (action == "RELEASE" and receipt is not None)
        ):
            raise GraphCancellationOperationReconciliationError(
                OPERATION_RECONCILIATION_INVALID,
                "reconciliation request fields or action/receipt matrix are invalid",
                side_effects_may_have_run=False,
            )
        return {
            "intent_ref": intent_ref,
            "reservation_id": reservation_id,
            "expected_version": expected_version,
            "action": action,
            "reconciler_actor_id": actor,
            "downstream_receipt_ref": receipt,
            "attestation_ref": attestation_ref,
            "attestation_key_id": key_id,
            "attestation_signature": signature,
        }

    @staticmethod
    def _record(value: Any, *, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{field_name} must be a mapping")
        return value

    @staticmethod
    def _text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").strip()

    @classmethod
    def _optional_text(cls, value: Any) -> str | None:
        return cls._text(value) or None

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        if type(value) is not int or value < 0:
            return -1
        return value

    @staticmethod
    def _positive_int(value: Any) -> int:
        if type(value) is not int or value < 1:
            return 0
        return value

    @staticmethod
    def _reject(detail: str) -> None:
        raise GraphCancellationOperationReconciliationError(
            OPERATION_RECONCILIATION_STATE_CONFLICT,
            detail,
            side_effects_may_have_run=False,
        )


__all__ = [
    "GraphCancellationOperationReconciliationError",
    "GraphCancellationOperationReconciliationService",
    "OPERATION_RECONCILIATION_INVALID",
    "OPERATION_RECONCILIATION_REJECTED",
    "OPERATION_RECONCILIATION_STATE_CONFLICT",
    "OPERATION_RECONCILIATION_UNCERTAIN",
    "OPERATION_RECONCILIATION_WAKE_FAILED",
]
