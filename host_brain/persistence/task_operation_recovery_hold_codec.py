"""Canonical identity codec for durable nonterminal Operation recovery holds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

from .task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)


OPERATION_RECOVERY_HOLD_SCHEMA = "Natsume.task.operation_recovery_hold.v1"
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,119}$")


def build_operation_recovery_hold_subject(
    *,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    attempt: Mapping[str, Any],
    operation: Mapping[str, Any],
    budget: Mapping[str, Any],
    cancellation: Mapping[str, Any] | None,
    active_leases: Sequence[Mapping[str, Any]],
    reason_code: str,
    checkpoint_state: str | None,
) -> tuple[dict[str, Any], str, str, str]:
    """Return canonical subject, JSON, digest, and deterministic case ref."""

    reason = str(reason_code or "").strip().upper()
    checkpoint = str(checkpoint_state or "").strip().upper() or None
    operation_status = _text(operation, "status").upper()
    if not _REASON.fullmatch(reason):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Operation recovery hold reason_code is invalid",
        )
    if checkpoint not in {None, "CONFLICT", "INVALID"}:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Operation recovery hold checkpoint_state is invalid",
        )
    if checkpoint == "CONFLICT":
        case_kind = "CHECKPOINT_BUDGET_CONFLICT"
        classification = "QUARANTINED_CHECKPOINT_CONFLICT"
    elif checkpoint == "INVALID":
        case_kind = "CHECKPOINT_AUTHORITY_CONFLICT"
        classification = "QUARANTINED_CHECKPOINT_CONFLICT"
    elif cancellation is not None:
        case_kind = "CANCELLATION_CROSSING"
        classification = "SIGNED_CANCELLATION_RECONCILIATION_REQUIRED"
    else:
        case_kind = "UNKNOWN_SIDE_EFFECT"
        classification = "SIGNED_OPERATION_RECONCILIATION_REQUIRED"
    if (
        checkpoint is not None
        and operation_status != "COMMITTED"
    ) or (
        checkpoint is None
        and operation_status not in {"RESERVED", "RECOVERY_REQUIRED"}
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Operation recovery hold classification does not match Operation status",
        )

    subject = {
        "schema_version": OPERATION_RECOVERY_HOLD_SCHEMA,
        "case_kind": case_kind,
        "classification": classification,
        "reason_code": reason,
        "checkpoint_state": checkpoint,
        "graph": _projection(
            graph,
            ("graph_id", "revision", "status", "status_version"),
        ),
        "node": _projection(
            node,
            ("node_id", "status", "status_version"),
        ),
        "attempt": _projection(
            attempt,
            ("attempt_id", "status", "status_version", "owner_id"),
        ),
        "operation": _projection(
            operation,
            (
                "reservation_id",
                "status",
                "status_version",
                "business_scope",
                "operation_fingerprint",
                "receipt_ref",
                "recovery_reason_code",
            ),
        ),
        "budget": _projection(
            budget,
            ("budget_id", "status", "status_version"),
        ),
        "cancellation": (
            _projection(
                cancellation,
                ("intent_ref", "status", "status_version"),
            )
            if cancellation is not None
            else None
        ),
        "active_leases": [
            _projection(
                item,
                (
                    "lease_id",
                    "status",
                    "status_version",
                    "owner_id",
                    "fencing_token",
                    "expires_ts_ms",
                ),
            )
            for item in sorted(
                active_leases,
                key=lambda value: str(value.get("lease_id") or ""),
            )
        ],
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest_hex = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    digest = f"sha256:{digest_hex}"
    return (
        subject,
        encoded,
        digest,
        f"operation-recovery-hold://sha256/{digest_hex}",
    )


def validate_operation_recovery_hold_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one immutable row without consulting mutable current state."""

    record = dict(row)
    try:
        subject = json.loads(str(record["subject_json"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "Operation recovery hold subject is invalid",
        ) from exc
    if not isinstance(subject, dict):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "Operation recovery hold subject is not an object",
        )
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest_hex = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    digest = f"sha256:{digest_hex}"
    case_ref = f"operation-recovery-hold://sha256/{digest_hex}"
    expected = {
        "schema_version": OPERATION_RECOVERY_HOLD_SCHEMA,
        "case_kind": str(record.get("case_kind") or ""),
        "classification": str(record.get("classification") or ""),
        "reason_code": str(record.get("reason_code") or ""),
        "checkpoint_state": record.get("checkpoint_state"),
    }
    if (
        str(record.get("schema_version") or "") != OPERATION_RECOVERY_HOLD_SCHEMA
        or str(record.get("subject_sha256") or "") != digest
        or str(record.get("case_ref") or "") != case_ref
        or any(subject.get(key) != value for key, value in expected.items())
        or int(record.get("created_ts_ms") or -1) < 0
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "Operation recovery hold identity or digest drifted",
        )
    return {
        **record,
        "subject": subject,
        "subject_sha256": digest,
        "case_ref": case_ref,
    }


def _projection(
    source: Mapping[str, Any],
    fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "Operation recovery hold source projection is invalid",
        )
    return {field: source.get(field) for field in fields}


def _text(source: Mapping[str, Any], field: str) -> str:
    return str(source.get(field) or "").strip()


__all__ = [
    "OPERATION_RECOVERY_HOLD_SCHEMA",
    "build_operation_recovery_hold_subject",
    "validate_operation_recovery_hold_row",
]
