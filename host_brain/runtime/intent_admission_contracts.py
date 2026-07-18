"""Shared immutable contracts and canonical helpers for Intent admission.

This module deliberately owns no runtime port, repository, or write authority.
The production verifier and its private Review continuation behavior import the
same schema constants and canonicalization rules from here so receipt identity
cannot drift during focused module splits.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from host_brain.contracts.agent_runtime.work_order import WorkOrderV3


INTENT_ADMISSION_INVALID = "INTENT_ADMISSION_INVALID"
INTENT_ADMISSION_BLOCKED = "INTENT_ADMISSION_BLOCKED"
INTENT_ADMISSION_REVIEW_REQUIRED = "INTENT_ADMISSION_REVIEW_REQUIRED"

_RECEIPT_SCHEMA = "natsume.intent-admission-receipt.v1"
_EVENT_TYPE = "IntentAdmissionArbitrated"
_AGGREGATE_TYPE = "TaskNodeIntentAdmission"
_PREPARED_NODE_EVENT_TYPE = "IntentAdmissionNodePrepared"
_PREPARED_NODE_AGGREGATE_TYPE = "TaskNodeIntentPreparation"
_PREPARED_SET_EVENT_TYPE = "IntentAdmissionReceiptSetPrepared"
_PREPARED_SET_AGGREGATE_TYPE = "TaskGraphIntentPreparation"
_COMPLETE_SET_EVENT_TYPE = "IntentAdmissionReceiptSetCompleted"
_COMPLETE_SET_AGGREGATE_TYPE = "TaskGraphIntentAdmission"
_CONTINUATION_EVENT_TYPE = "IntentAdmissionReviewContinued"
_CONTINUATION_AGGREGATE_TYPE = "TaskNodeIntentContinuation"
_PREPARED_SET_SCHEMA = "natsume.intent-admission-prepared-set.v1"
_COMPLETE_SET_SCHEMA = "natsume.intent-admission-complete-set.v1"
_CONTINUATION_SCHEMA = "natsume.intent-admission-review-continuation.v1"
_DEFAULT_REVIEW_TTL_MS = 7 * 24 * 60 * 60 * 1000
_ALLOWED_DECISIONS = frozenset(
    {"BLOCK", "REVIEW", "RESTRICT", "NO_ADDITIONAL_RESTRICTION"}
)
_PROCEED_DECISIONS = frozenset({"RESTRICT", "NO_ADDITIONAL_RESTRICTION"})
_CONSTRAINT_DISPOSITIONS = frozenset({"BLOCK", "REVIEW", "RESTRICT"})
_REVIEW_CONTINUATION_AUTHORITY_FIELDS = frozenset(
    {
        "schema_version",
        "review_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "subject_version",
        "resume_phase",
        "review_status",
        "review_status_version",
        "claim_owner",
        "claim_fencing_token",
        "claim_expires_ts_ms",
        "claim_token_sha256",
        "status_version",
    }
)


class IntentAdmissionError(RuntimeError):
    """One graph cannot cross the Intent admission boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or INTENT_ADMISSION_INVALID)
        self.detail = str(detail or "Intent admission failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise IntentAdmissionError(
            INTENT_ADMISSION_INVALID,
            "canonical Intent admission identity is not finite JSON",
        ) from exc


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


def _text(value: object, *, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise IntentAdmissionError(INTENT_ADMISSION_INVALID, f"{field} is missing")
    return result


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise IntentAdmissionError(INTENT_ADMISSION_INVALID, f"{field} is invalid")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise IntentAdmissionError(
            INTENT_ADMISSION_INVALID,
            f"{field} is invalid",
        ) from exc
    if result < minimum:
        raise IntentAdmissionError(INTENT_ADMISSION_INVALID, f"{field} is invalid")
    return result


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentAdmissionError(INTENT_ADMISSION_INVALID, f"{field} is missing")
    return dict(value)


def _contract_digest(value: object, *, field: str) -> str:
    serializer = getattr(value, "to_dict", None)
    if not callable(serializer):
        raise IntentAdmissionError(
            INTENT_ADMISSION_INVALID,
            f"{field} is not a typed canonical contract",
        )
    return _digest(serializer())


def _receipt_payload_digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("receipt_digest", None)
    return _digest(body)


def _payload_digest_without(payload: Mapping[str, Any], field: str) -> str:
    body = dict(payload)
    body.pop(str(field), None)
    return _digest(body)


def _scene_query(work_order: WorkOrderV3) -> dict[str, Any]:
    pack = _mapping(work_order.input_pack, field="WorkOrder input_pack")
    identity = _mapping(pack.get("scene_identity_v2"), field="scene_identity_v2")
    if identity.get("schema") != "natsume.scene-identity.v2":
        raise IntentAdmissionError(
            INTENT_ADMISSION_INVALID,
            "frozen Scene identity schema drifted",
        )
    workspace_id = _text(identity.get("workspace_id"), field="Scene workspace_id")
    subject_id = _text(identity.get("subject_id"), field="Scene subject_id")
    scene_id = _text(identity.get("scene_id"), field="Scene scene_id")
    revision = _integer(identity.get("revision"), field="Scene revision", minimum=1)
    decision_digest = _text(
        identity.get("decision_digest"),
        field="Scene decision_digest",
    )
    if identity != {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": workspace_id,
        "subject_id": subject_id,
        "scene_id": scene_id,
        "revision": revision,
        "decision_digest": decision_digest,
    }:
        raise IntentAdmissionError(
            INTENT_ADMISSION_INVALID,
            "frozen Scene identity contains unrecognized authority fields",
        )
    return identity


__all__ = [
    "INTENT_ADMISSION_BLOCKED",
    "INTENT_ADMISSION_INVALID",
    "INTENT_ADMISSION_REVIEW_REQUIRED",
    "IntentAdmissionError",
]
