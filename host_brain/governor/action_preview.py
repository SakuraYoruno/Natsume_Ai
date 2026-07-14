"""Dry-run preview for ScenePolicy + ActionArbiter decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.governor.action_arbiter import ActionArbiter


ACTION_PREVIEW_SCHEMA_VERSION = "Natsume.action.preview.v1"
ACTION_ARBITER_EVENT_TYPE = "ACTION_ARBITER_DECIDED"


def _refs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


def build_action_arbiter_runtime_event(
    *,
    preview_id: str,
    arbiter_decision: Mapping[str, Any],
    evidence_refs: Sequence[str] = (),
    ts_ms: int,
) -> Any:
    """Build a sanitized RuntimeEvent; no action payload/body is persisted."""
    from host_brain.integrations.runtime_events import RuntimeEvent  # noqa: PLC0415

    decision = dict(arbiter_decision or {})
    source_ref = f"action-preview://{preview_id}"
    payload = {
        "preview_id": preview_id,
        "decision_id": decision.get("decision_id"),
        "decision": decision.get("decision"),
        "reason_codes": list(decision.get("reason_codes") or []),
        "action_kind": decision.get("effective_action_kind"),
        "scope": decision.get("effective_scope"),
        "risk_level": decision.get("effective_risk_level"),
        "governor_decision": decision.get("governor_decision"),
        "committed_route_token": decision.get("committed_route_token"),
        "authority_binding_ref": decision.get("authority_binding_ref"),
        "scene_decision_ref": decision.get("scene_decision_ref"),
        "explicit_user_goal": bool(decision.get("explicit_user_goal")),
        "user_goal_preserved": bool(decision.get("user_goal_preserved")),
        "side_effects_executed": False,
    }
    return RuntimeEvent.create(
        event_type=ACTION_ARBITER_EVENT_TYPE,
        source_kind="SYSTEM",
        source_ref=source_ref,
        aggregate_type="ACTION_PREVIEW",
        aggregate_id=preview_id,
        aggregate_version=1,
        payload_ref=source_ref,
        payload=payload,
        evidence_refs=_refs(evidence_refs),
        redaction_level="INTERNAL_REDACTED",
        ts_ms=int(ts_ms),
    )


def preview_action(
    *,
    governor_decision: Mapping[str, Any],
    proposed_action: Mapping[str, Any],
    intent_envelope: Any = None,
    scene_decision: Any = None,
    user_intent: Mapping[str, Any] | None = None,
    evidence_refs: Sequence[str] = (),
    now_ms: int,
    arbiter: ActionArbiter | None = None,
    runtime_event_store: Any = None,
    record_runtime_event: bool = False,
) -> dict[str, Any]:
    """Preview the final decision without invoking an executor or side effect."""
    resolved_arbiter = arbiter or ActionArbiter()
    decision = resolved_arbiter.decide(
        governor_decision=governor_decision,
        proposed_action=proposed_action,
        intent_envelope=intent_envelope,
        scene_decision=scene_decision,
        user_intent=user_intent,
        now_ms=int(now_ms),
    )
    seed = json.dumps(
        {
            "decision_id": decision["decision_id"],
            "evidence_refs": list(_refs(evidence_refs)),
            "ts_ms": int(now_ms),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    preview_id = f"action_preview_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"
    runtime_event = build_action_arbiter_runtime_event(
        preview_id=preview_id,
        arbiter_decision=decision,
        evidence_refs=evidence_refs,
        ts_ms=int(now_ms),
    )
    recorded_event: dict[str, Any] | None = None
    if record_runtime_event:
        if runtime_event_store is None or not callable(getattr(runtime_event_store, "append_event", None)):
            raise ValueError("record_runtime_event requires RuntimeEventStore.append_event")
        recorded_event = dict(runtime_event_store.append_event(runtime_event) or {})

    return {
        "schema_version": ACTION_PREVIEW_SCHEMA_VERSION,
        "preview_id": preview_id,
        "status": "DRY_RUN",
        "arbiter_decision": decision,
        "would_execute": (
            decision.get("decision") != "BLOCK"
            and decision.get("execution_state") == "EXECUTABLE"
            and bool(decision.get("authority_binding_valid"))
        ),
        "execution_performed": False,
        "external_side_effects": False,
        "evidence_refs": list(_refs(evidence_refs)),
        "runtime_event": {
            "event_id": runtime_event.event_id,
            "event_type": runtime_event.event_type,
            "source_ref": runtime_event.source_ref,
            "payload_ref": runtime_event.payload_ref,
            "dedupe_key": runtime_event.dedupe_key,
            "payload": dict(runtime_event.payload),
            "recorded": recorded_event is not None,
            "cursor": recorded_event.get("cursor") if recorded_event else None,
        },
    }


class ActionPreviewService:
    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return preview_action(**kwargs)


__all__ = [
    "ACTION_ARBITER_EVENT_TYPE",
    "ACTION_PREVIEW_SCHEMA_VERSION",
    "ActionPreviewService",
    "build_action_arbiter_runtime_event",
    "preview_action",
]
