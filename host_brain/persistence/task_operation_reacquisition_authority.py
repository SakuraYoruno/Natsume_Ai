"""Read-only authority checks for a signed Operation Lease reacquisition."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.operation_reconciliation import (
    OperationReconciliationContractError,
    build_reconciliation_attestation_binding,
)
from .reconciliation_proof_verifier import (
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
)
from .task_store_types import (
    IdempotencyStatus,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TaskStoreError,
)


def assert_signed_reacquisition_lease_authority(
    conn: sqlite3.Connection,
    *,
    operation: sqlite3.Row,
    attempt: sqlite3.Row,
    attempt_id: str,
    owner_id: str,
    expected_attempt_version: int,
    expected_lease_id: str,
    now_ts_ms: int,
    authorization: Mapping[str, Any],
    proof_verifier: Any,
) -> None:
    """Authorize the pre-mutation Lease step of signed ``REACQUIRE``.

    The Operation mutation follows in a second fail-closed transaction.  This
    read-only check therefore authenticates the same proof before the first
    Lease mutation and admits only an expired original fence or an exact
    response-loss replay of the deterministic replacement Lease.
    """

    request = _normalize_authorization(authorization)
    if (
        str(operation["status"]) != IdempotencyStatus.RECOVERY_REQUIRED.value
        or int(operation["status_version"]) != request["expected_version"]
        or str(operation["attempt_id"] or "") != str(attempt_id)
        or str(operation["business_scope"])
        != str(attempt["operation_scope"] or "")
        or str(operation["operation_fingerprint"])
        != str(attempt["operation_fingerprint"] or "")
    ):
        raise TaskStoreError(
            TASK_STORE_CAS_CONFLICT,
            "signed reacquisition requires the exact RECOVERY_REQUIRED Operation",
        )
    try:
        binding = build_reconciliation_attestation_binding(
            reservation_id=str(operation["reservation_id"]),
            business_scope=str(operation["business_scope"]),
            operation_fingerprint=str(operation["operation_fingerprint"]),
            recovery_version=request["expected_version"],
            reconciler_actor_id=request["reconciler_actor_id"],
            attestation_ref=request["attestation_ref"],
            recovery_action="REACQUIRE",
            reconciliation_outcome="NO_SIDE_EFFECT",
        )
    except OperationReconciliationContractError as exc:
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            str(exc),
        ) from exc
    if proof_verifier is None:
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
            "no reconciliation proof verifier is configured",
        )
    if (
        not proof_verifier.has_key(request["attestation_key_id"])
        or not proof_verifier.verify(
            key_id=request["attestation_key_id"],
            message=binding,
            signature=request["attestation_signature"],
        )
    ):
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            "reacquisition signature is invalid or binding was tampered",
        )

    active = conn.execute(
        """
        SELECT * FROM task_runtime_lease
        WHERE attempt_id = ? AND status = 'ACTIVE'
        ORDER BY fencing_token DESC, lease_id
        """,
        (str(attempt_id),),
    ).fetchall()
    if len(active) != 1:
        raise TaskStoreError(
            TASK_STORE_LEASE_CONFLICT,
            "signed reacquisition requires exactly one active Lease",
        )
    live = active[0]
    if _is_exact_lease_response_replay(
        attempt=attempt,
        lease=live,
        owner_id=owner_id,
        expected_attempt_version=expected_attempt_version,
        expected_lease_id=expected_lease_id,
        now_ts_ms=now_ts_ms,
    ):
        return
    if (
        int(attempt["status_version"]) != int(expected_attempt_version)
        or str(live["lease_id"]) != str(operation["lease_id"] or "")
        or str(live["owner_id"]) != str(operation["owner_id"] or "")
        or int(live["fencing_token"]) != int(operation["fencing_token"] or 0)
        or int(live["expires_ts_ms"]) > int(now_ts_ms)
    ):
        raise TaskStoreError(
            TASK_STORE_LEASE_CONFLICT,
            "signed reacquisition cannot supersede a live or drifting Lease",
        )


def _is_exact_lease_response_replay(
    *,
    attempt: sqlite3.Row,
    lease: sqlite3.Row,
    owner_id: str,
    expected_attempt_version: int,
    expected_lease_id: str,
    now_ts_ms: int,
) -> bool:
    return (
        int(attempt["status_version"]) == int(expected_attempt_version) + 1
        and str(attempt["status"]) == "LEASED"
        and str(attempt["owner_id"] or "") == str(owner_id)
        and str(lease["lease_id"]) == str(expected_lease_id)
        and str(lease["owner_id"]) == str(owner_id)
        and int(lease["expires_ts_ms"]) > int(now_ts_ms)
    )


def _normalize_authorization(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "operation_reacquisition_authorization must be a mapping",
        )
    expected_version = value.get("expected_version")
    normalized = {
        "expected_version": expected_version,
        "reconciler_actor_id": str(
            value.get("reconciler_actor_id") or ""
        ).strip(),
        "attestation_ref": str(value.get("attestation_ref") or "").strip(),
        "attestation_key_id": str(
            value.get("attestation_key_id") or ""
        ).strip(),
        "attestation_signature": str(
            value.get("attestation_signature") or ""
        ).strip(),
    }
    if (
        type(expected_version) is not int
        or expected_version < 0
        or not all(str(normalized[key]) for key in normalized if key != "expected_version")
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "signed reacquisition authorization fields are invalid",
        )
    return normalized


__all__ = ["assert_signed_reacquisition_lease_authority"]
