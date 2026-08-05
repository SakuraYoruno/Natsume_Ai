"""Pure payload and receipt helpers for the relationship lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from natsume_contracts.life import (
    AffectiveRepairTargetV1,
    LifeDeliveryReceipt,
    LifeOutboxMessage,
    RelationshipEpisodeV1,
    RelationalAftereffectV1,
)


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    resolved = str(value or "").strip()
    return resolved[:limit] if resolved else default


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _aftereffect_from_payload(payload: Mapping[str, Any]) -> RelationalAftereffectV1:
    raw = _mapping(payload.get("aftereffect"))
    if not raw:
        raise ValueError("relational aftereffect is required")
    return RelationalAftereffectV1(
        aftereffect_id=raw.get("aftereffect_id"),
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_receipt_ref=raw.get("source_receipt_ref"),
        user_input_ref=raw.get("user_input_ref"),
        outcome=raw.get("outcome"),
        helpfulness=raw.get("helpfulness"),
        boundary_respected=raw.get("boundary_respected"),
        repair_acknowledged=raw.get("repair_acknowledged"),
        observed_at_ms=raw.get("observed_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        affect_repair_target=(
            None
            if raw.get("affect_repair_target") is None
            else AffectiveRepairTargetV1.from_mapping(
                _mapping(raw.get("affect_repair_target"))
            )
        ),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _relationship_episode_from_payload(
    raw: Mapping[str, Any],
) -> RelationshipEpisodeV1:
    if not raw:
        raise ValueError("relationship episode is required")
    return RelationshipEpisodeV1(
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_kind=raw.get("source_kind"),
        response_receipt_ref=raw.get("response_receipt_ref"),
        response_digest=raw.get("response_digest"),
        opened_at_ms=raw.get("opened_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        affect_repair_target=(
            None
            if raw.get("affect_repair_target") is None
            else AffectiveRepairTargetV1.from_mapping(
                _mapping(raw.get("affect_repair_target"))
            )
        ),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _opaque_ref(prefix: str, value: Any) -> str:
    return f"{prefix}://sha256/{_digest(value)}"


def _companion_target_id(
    *,
    scope: Any,
    platform: str,
    conversation_id: str,
) -> str:
    identity = {
        "schema": "companion-proactive-target.v1",
        "aggregate_id": scope.aggregate_id,
        "workspace_id": scope.workspace_id,
        "relationship_subject_id": scope.relationship_subject_id,
        "platform": str(platform or "").strip().lower(),
        "conversation_id": str(conversation_id or "").strip(),
    }
    return f"companion_target_{_digest(identity)[:32]}"


def _validate_delivery_receipt(
    *,
    message: LifeOutboxMessage,
    receipt: LifeDeliveryReceipt,
) -> None:
    if (
        receipt.outbox_id != message.outbox_id
        or receipt.delivery_key != message.delivery_key
        or receipt.payload_digest != message.payload_digest
        or receipt.authorization_digest != message.authorization_digest
    ):
        raise ValueError("companion proactive delivery receipt drift")


def _delivery_result_is_success(result: Mapping[str, Any]) -> bool:
    payload = _mapping(result)
    if payload.get("success") is False or payload.get("ok") is False:
        return False
    status = _text(payload.get("status"), limit=80).upper()
    return status not in {"FAILED", "ERROR", "REJECTED", "DEGRADED", "IN_DOUBT"}
