"""Neutral contracts for signed Operation reconciliation.

The application and persistence layers both depend on this module so a
transactional, definite store rejection cannot be confused with an unknown
transport/database failure after a mutation may have committed.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re


_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)


class OperationReconciliationContractError(ValueError):
    """Invalid canonical reconciliation material."""

    reason_code = "TASK_STORE_RECONCILIATION_PROOF_INVALID"


class OperationReconciliationStoreRejected(RuntimeError):
    """A fail-closed store rejection whose transaction did not commit."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or "OPERATION_RECONCILIATION_REJECTED")
        self.detail = str(detail or "operation reconciliation was rejected")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_reconciliation_attestation_binding(
    *,
    reservation_id: str,
    business_scope: str,
    operation_fingerprint: str,
    recovery_version: int,
    reconciler_actor_id: str,
    attestation_ref: str,
    recovery_action: str,
    reconciliation_outcome: str,
    receipt_ref: str | None = None,
) -> bytes:
    """Return the exact canonical bytes signed by a verification-only authority."""

    if isinstance(recovery_version, bool) or not isinstance(recovery_version, int):
        raise OperationReconciliationContractError(
            "recovery_version must be a non-negative integer"
        )
    action = str(recovery_action or "").strip().upper()
    outcome = str(reconciliation_outcome or "").strip().upper()
    receipt = str(receipt_ref or "").strip() or None
    expected_outcome = {
        "COMMIT": "SIDE_EFFECT_CONFIRMED",
        "RELEASE": "NO_SIDE_EFFECT",
        "REACQUIRE": "NO_SIDE_EFFECT",
    }.get(action)
    if expected_outcome is None or outcome != expected_outcome:
        raise OperationReconciliationContractError(
            "recovery_action and reconciliation_outcome are inconsistent"
        )
    if outcome == "SIDE_EFFECT_CONFIRMED":
        if receipt is None or not _DURABLE_REFERENCE_PATTERN.fullmatch(receipt):
            raise OperationReconciliationContractError(
                "SIDE_EFFECT_CONFIRMED attestation requires a durable receipt_ref"
            )
    elif receipt is not None:
        raise OperationReconciliationContractError(
            "NO_SIDE_EFFECT attestation cannot bind a receipt_ref"
        )
    binding: Mapping[str, object] = {
        "reservation_id": str(reservation_id or "").strip(),
        "business_scope": str(business_scope or "").strip(),
        "operation_fingerprint": str(operation_fingerprint or "").strip(),
        "recovery_version": recovery_version,
        "reconciler_actor_id": str(reconciler_actor_id or "").strip(),
        "attestation_ref": str(attestation_ref or "").strip(),
        "recovery_action": action,
        "reconciliation_outcome": outcome,
        "receipt_ref": receipt,
    }
    if (
        not binding["reservation_id"]
        or not binding["business_scope"]
        or not binding["operation_fingerprint"]
        or recovery_version < 0
        or not binding["reconciler_actor_id"]
        or not _DURABLE_REFERENCE_PATTERN.fullmatch(str(binding["attestation_ref"]))
    ):
        raise OperationReconciliationContractError(
            "reconciliation attestation binding fields are invalid"
        )
    return json.dumps(
        binding,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_operation_failure_outbox_identity(
    *,
    reservation_id: str,
    recovery_version: int,
    reconciler_actor_id: str,
    attestation_ref: str,
    attestation_key_id: str,
) -> tuple[str, str]:
    """Return the canonical Outbox id and idempotency key for raw failure."""

    values = (
        str(reservation_id or "").strip(),
        recovery_version,
        str(reconciler_actor_id or "").strip(),
        str(attestation_ref or "").strip(),
        str(attestation_key_id or "").strip(),
    )
    if (
        not values[0]
        or isinstance(recovery_version, bool)
        or not isinstance(recovery_version, int)
        or recovery_version < 0
        or not values[2]
        or not _DURABLE_REFERENCE_PATTERN.fullmatch(values[3])
        or not values[4]
    ):
        raise OperationReconciliationContractError(
            "operation failure Outbox identity fields are invalid"
        )
    material = "\n".join(
        (
            values[0],
            str(recovery_version),
            values[2],
            values[3],
            values[4],
            "TERMINAL_FAILURE",
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return (
        f"outbox::operation-failure-reconciliation::{digest}",
        f"operation-failure-reconciliation:{digest}",
    )


__all__ = [
    "OperationReconciliationContractError",
    "OperationReconciliationStoreRejected",
    "build_operation_failure_outbox_identity",
    "build_reconciliation_attestation_binding",
]
