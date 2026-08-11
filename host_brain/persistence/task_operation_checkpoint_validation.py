"""Pure evidence validation for a committed Operation checkpoint."""

from __future__ import annotations

import re
from typing import Any, Mapping

from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


_EXECUTION_DIGEST_REFERENCE_PATTERN = re.compile(
    r"^sha256://execution/[0-9a-f]{64}$"
)


def validate_committed_operation_outbox(
    *,
    outbox: Mapping[str, Any],
    reservation: Mapping[str, Any],
    attempt_id: str,
) -> str:
    """Return the receipt-derived digest ref or reject mixed evidence."""

    if (
        int(outbox.get("aggregate_version") or -1)
        != int(reservation["status_version"])
        or str(outbox.get("attempt_id") or "") != attempt_id
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "committed operation checkpoint Outbox Attempt binding drifted",
        )
    evidence_refs = tuple(outbox.get("evidence_refs") or ())
    receipt_ref = str(reservation["receipt_ref"])
    receipt_tail = receipt_ref.rsplit("/", 1)[-1]
    checkpoint_digest_ref = (
        f"sha256://execution/{receipt_tail}"
        if len(receipt_tail) == 64
        and all(char in "0123456789abcdef" for char in receipt_tail.lower())
        else None
    )
    normal_commit = (
        outbox.get("event_type") == "OPERATION_COMMITTED"
        and len(evidence_refs) == 2
        and evidence_refs[0] == receipt_ref
        and _EXECUTION_DIGEST_REFERENCE_PATTERN.fullmatch(
            str(evidence_refs[1])
        )
        is not None
        and evidence_refs[1] == checkpoint_digest_ref
    )
    signed_checkpoint_commit = (
        outbox.get("event_type") == "OPERATION_RECOVERY_COMMIT"
        and str(reservation.get("reconciliation_action") or "") == "COMMIT"
        and str(reservation.get("reconciliation_outcome") or "")
        == "SIDE_EFFECT_CONFIRMED"
        and str(reservation.get("reconciliation_proof_ref") or "") != ""
        and evidence_refs
        == (
            str(reservation["reconciliation_proof_ref"]),
            receipt_ref,
        )
        and receipt_ref.startswith("worker-projection://")
        and checkpoint_digest_ref is not None
    )
    if not normal_commit and not signed_checkpoint_commit:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "committed operation checkpoint evidence binding is incomplete or drifted",
        )
    if len(evidence_refs) != 2 or checkpoint_digest_ref is None:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "committed operation checkpoint evidence binding is incomplete or drifted",
        )
    return checkpoint_digest_ref


__all__ = ["validate_committed_operation_outbox"]
