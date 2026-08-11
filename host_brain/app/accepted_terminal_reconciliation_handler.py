"""Acceptance Effect handler for a signed terminal reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


class AcceptedTerminalReconciliationHandler:
    """Apply the exact signed request only after TaskGraph Acceptance."""

    def __init__(self, *, service: Any, task_store: Any, command_store: Any) -> None:
        if not callable(getattr(service, "reconcile_terminal_state", None)):
            raise TypeError("terminal reconciliation service is required")
        if not callable(getattr(task_store, "read_terminal_reconciliation_case", None)):
            raise TypeError("terminal reconciliation read authority is required")
        if not callable(getattr(command_store, "read", None)):
            raise TypeError("terminal signed command authority is required")
        self._service = service
        self._store = task_store
        self._command_store = command_store

    def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = _request(payload, command_store=self._command_store)
        try:
            result = self._service.reconcile_terminal_state(**request)
            if hasattr(result, "__await__"):
                raise TypeError("async terminal reconciliation service is unsupported")
        except Exception as exc:
            try:
                return self.reconcile(payload)
            except AcceptanceEffectOutcomeUnknown as reconciliation:
                raise AcceptanceEffectOutcomeUnknown(
                    "signed terminal reconciliation outcome is unknown after "
                    f"{type(exc).__name__}; {reconciliation.detail}"
                ) from exc
        reopened = dict(self.reconcile(payload))
        reopened["status"] = (
            "REPLAYED"
            if isinstance(result, Mapping) and result.get("replayed") is True
            else "RECONCILED"
        )
        return reopened

    def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = _request(payload, command_store=self._command_store)
        try:
            aggregate = self._store.read_terminal_reconciliation_case(
                request["case_id"]
            )
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "terminal reconciliation proof cannot be reopened"
            ) from exc
        proof_raw = aggregate.get("proof") if isinstance(aggregate, Mapping) else None
        proof = dict(proof_raw) if isinstance(proof_raw, Mapping) else {}
        expected = {
            "case_snapshot_sha256": request["expected_case_snapshot_sha256"],
            "subject_snapshot_sha256": request[
                "expected_subject_snapshot_sha256"
            ],
            "action": request["action"],
            "target_graph_status": request["target_graph_status"],
            "target_budget_status": request["target_budget_status"],
            "reconciler_actor_id": request["reconciler_actor_id"],
            "issued_ts_ms": request["issued_ts_ms"],
            "attestation_ref": request["attestation_ref"],
            "attestation_key_id": request["attestation_key_id"],
            "attestation_signature": request["attestation_signature"],
        }
        if (
            proof.get("authority_kind") != "SIGNED_OPERATOR"
            or any(proof.get(key) != value for key, value in expected.items())
        ):
            raise AcceptanceEffectOutcomeUnknown(
                "terminal reconciliation proof is absent or binding drifted"
            )
        proof_id = str(proof.get("proof_id") or "").strip()
        proof_digest = str(proof.get("proof_digest") or "").strip().lower()
        if not proof_id or not proof_digest.startswith("sha256:") or len(proof_digest) != 71:
            raise AcceptanceEffectOutcomeUnknown(
                "terminal reconciliation proof identity is invalid"
            )
        return {
            "status": "RECONCILED",
            "receipt_ref": proof_id,
            "proof_digest": proof_digest,
            "authority_kind": "SIGNED_OPERATOR",
        }


def _request(
    payload: Mapping[str, Any],
    *,
    command_store: Any,
) -> dict[str, Any]:
    source = dict(payload or {})
    if str(source.get("operator_command_id") or "").strip() != (
        "task_terminal.reconcile"
    ):
        raise ValueError("terminal reconciliation command binding is invalid")
    bundle_ref = str(source.get("signed_command_ref") or "").strip()
    command_sha256 = str(source.get("signed_command_sha256") or "").strip()
    request_id = str(source.get("request_id") or "").strip()
    case_id = str(source.get("case_id") or "").strip()
    if not bundle_ref or not command_sha256 or not request_id or not case_id:
        raise ValueError("terminal reconciliation signed command reference is absent")
    reopened = command_store.read(bundle_ref)
    command_raw = reopened.get("command") if isinstance(reopened, Mapping) else None
    command = dict(command_raw) if isinstance(command_raw, Mapping) else {}
    if (
        reopened.get("request_id") != request_id
        or reopened.get("case_id") != case_id
        or reopened.get("command_sha256") != command_sha256
        or command.get("operator_command_id") != "task_terminal.reconcile"
    ):
        raise ValueError("terminal reconciliation signed command binding drifted")
    command.pop("operator_command_id", None)
    return command


__all__ = ["AcceptedTerminalReconciliationHandler"]
