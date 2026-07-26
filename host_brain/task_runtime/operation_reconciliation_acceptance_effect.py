"""Pure validation for governed Operation reconciliation effect proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any


_EFFECT_TYPE = "task.operation_reconciliation"
_TARGET_ID = "operator_task_operation_reconcile"
_CORE_FIELDS = frozenset(
    {
        "evidence_ref",
        "evidence_digest",
        "reservation_id",
        "expected_version",
        "action",
        "reconciler_actor_id",
    }
)
_RECOVERY_CASE_FIELDS = frozenset(
    {
        "recovery_case_ref",
        "recovery_case_subject_sha256",
    }
)
_EVIDENCE_REF = re.compile(
    r"^operation-reconcile-evidence://sha256/[0-9a-f]{64}$"
)
_RECOVERY_CASE_REF = re.compile(
    r"^operation-recovery-hold://sha256/[0-9a-f]{64}$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate_operation_reconciliation_proposal(
    *,
    raw_effects: Any,
    frozen_intent: Mapping[str, Any],
    frozen_args: Mapping[str, Any],
    work_order_metadata: Mapping[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Return the exact authority-free payload or fail closed."""

    if not isinstance(raw_effects, Sequence) or isinstance(
        raw_effects,
        (str, bytes, bytearray),
    ):
        raise ValueError(
            "Operation reconciliation Operator must propose one effect"
        )
    if len(raw_effects) != 1:
        raise ValueError(
            "Operation reconciliation Operator must propose exactly one effect"
        )
    descriptor = _mapping(
        raw_effects[0],
        "Operation reconciliation effect descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise ValueError("Operation reconciliation descriptor fields drifted")
    if (
        descriptor.get("effect_type") != _EFFECT_TYPE
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise ValueError(
            "Operation reconciliation proposed an unsupported effect"
        )
    payload = _mapping(
        descriptor.get("payload"),
        "Operation reconciliation effect payload",
    )
    intent = _mapping(frozen_intent, "intent_draft")
    args = _mapping(frozen_args, "intent_draft.args")
    metadata = _mapping(work_order_metadata, "work_order.metadata")
    present_case_fields = _RECOVERY_CASE_FIELDS.intersection(payload)
    if present_case_fields not in (frozenset(), _RECOVERY_CASE_FIELDS):
        raise ValueError(
            "Operation reconciliation recovery case fields are incomplete"
        )
    request_fields = set(_CORE_FIELDS | present_case_fields)
    required_keys = {
        "operator_command_id",
        "request_id",
        *request_fields,
    }
    expected = {
        "operator_command_id": "task_operation.reconcile",
        "request_id": str(request_id or "").strip(),
        **{field: args.get(field) for field in request_fields},
    }
    action = str(expected.get("action") or "").strip().upper()
    expected_version = expected.get("expected_version")
    evidence_ref = str(expected.get("evidence_ref") or "")
    if (
        set(payload) != required_keys
        or payload != expected
        or not expected["request_id"]
        or not str(expected.get("reservation_id") or "").strip()
        or type(expected_version) is not int
        or expected_version < 0
        or action not in {"COMMIT", "RELEASE", "REACQUIRE"}
        or not str(expected.get("reconciler_actor_id") or "").strip()
        or not _EVIDENCE_REF.fullmatch(evidence_ref)
        or str(expected.get("evidence_digest") or "")
        != "sha256:" + evidence_ref.rsplit("/", 1)[-1]
        or str(intent.get("action") or "").strip()
        != "operator_task_operation_reconciliation"
        or str(intent.get("target") or "").strip().lower() != "task_runtime"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip() != _TARGET_ID
        or str(metadata.get("operator_command_id") or "").strip()
        != "task_operation.reconcile"
    ):
        raise ValueError(
            "Operation reconciliation effect differs from frozen intent"
        )
    if present_case_fields:
        case_ref = str(expected.get("recovery_case_ref") or "")
        case_digest = str(
            expected.get("recovery_case_subject_sha256") or ""
        )
        if (
            not _RECOVERY_CASE_REF.fullmatch(case_ref)
            or not _SHA256.fullmatch(case_digest)
            or case_digest
            != "sha256:" + case_ref.rsplit("/", 1)[-1]
        ):
            raise ValueError(
                "Operation reconciliation recovery case binding is invalid"
            )
    return payload


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be canonical JSON") from exc


__all__ = ["validate_operation_reconciliation_proposal"]
