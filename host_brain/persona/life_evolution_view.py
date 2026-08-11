"""Read-only UI projection for World, Appraisal, Bond, Scene and ActionArbiter."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any


LIFE_EVOLUTION_VIEW_SCHEMA_VERSION = "Natsume.life_evolution.view.v1"
_PRIVATE_KEYS = frozenset(
    {
        "raw_text",
        "content",
        "private_text",
        "memory_body",
        "message_text",
        "opening_message",
        "raw_body",
        "secret",
        "cookie",
        "password",
        "approval_token_ref",
    }
)


def _map(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        resolved = as_dict()
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    if is_dataclass(value):
        resolved = asdict(value)
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    return {}


def _refs(value: Any, *, limit: int = 32) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    return list(dict.fromkeys(item.strip()[:512] for item in list(value)[:limit] if isinstance(item, str) and item.strip()))


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    if not isinstance(value, str):
        return default
    resolved = value.strip()
    return resolved[:limit] if resolved else default


def _private_field_count(value: Any) -> int:
    if isinstance(value, Mapping):
        return sum(
            (1 if isinstance(key, str) and key.lower() in _PRIVATE_KEYS else 0)
            + _private_field_count(item)
            for key, item in list(value.items())[:128]
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_private_field_count(item) for item in list(value)[:128])
    return 0


def _number(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.strip()[:240]
    if isinstance(value, Mapping):
        return {
            key[:80]: _safe_value(item)
            for key, item in list(value.items())[:16]
            if isinstance(key, str) and key.lower() not in _PRIVATE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_safe_value(item) for item in list(value)[:16]]
    return "[REDACTED_UNSUPPORTED_TYPE]"


def _world_fact_view(raw: Any, *, now_ms: int) -> dict[str, Any] | None:
    fact = _map(raw)
    key = _text(fact.get("key"), limit=160)
    if not key:
        return None
    epistemic_type = _text(fact.get("epistemic_type"), default="unknown", limit=80).lower()
    conflict_state = _text(fact.get("conflict_state"), default="NONE", limit=80).upper()
    observed = _int(fact.get("observed_at_ms"))
    expires = _int(fact.get("expires_at_ms"))
    expired = bool(expires and now_ms >= expires) or "EXPIRED" in conflict_state
    conflicted = "CONFLICT" in conflict_state or conflict_state == "SOURCE_UNAVAILABLE"
    degraded = epistemic_type == "degraded" or conflict_state == "SOURCE_UNAVAILABLE"
    if expired:
        display_state = "EXPIRED"
    elif conflicted:
        display_state = "CONFLICT"
    elif degraded:
        display_state = "DEGRADED"
    else:
        display_state = "CURRENT"
    fact_class = "FACT" if epistemic_type in {"observed", "observed_feedback"} else "INFERENCE"
    source = _text(fact.get("source"), default="unknown", limit=120)
    value = _safe_value(fact.get("value"))
    if source == "state_arbiter":
        # StateArbiter values can originate in Canon/Episodic private state.
        # The cockpit needs provenance/conflict, never the adjudicated body.
        value = "[REDACTED_ADJUDICATED_VALUE]"
    raw_conflict_detail = _text(fact.get("conflict_detail"), limit=240)
    return {
        "key": key,
        "value": value,
        "fact_class": fact_class,
        "epistemic_type": epistemic_type,
        "source": source,
        "source_ref": _text(fact.get("source_ref"), limit=512),
        "confidence": round(max(0.0, min(1.0, _number(fact.get("confidence")))), 4),
        "observed_at_ms": observed,
        "expires_at_ms": expires,
        "ttl_remaining_ms": max(0, expires - now_ms) if expires else None,
        "expired": expired,
        "conflicted": conflicted,
        "conflict_state": conflict_state,
        "conflict_detail": "CONFLICT_DETAIL_REDACTED" if raw_conflict_detail else "",
        "conflict_detail_redacted": bool(raw_conflict_detail),
        "display_state": display_state,
        "reason_codes": _refs(fact.get("reason_codes")),
    }


def _world_view(raw: Any, *, now_ms: int) -> dict[str, Any]:
    world = _map(raw)
    facts = [
        item
        for item in (_world_fact_view(value, now_ms=now_ms) for value in list(world.get("facts") or []))
        if item is not None
    ]
    return {
        "schema_version": _text(world.get("schema_version"), limit=120),
        "status": _text(world.get("status"), default="UNAVAILABLE", limit=80).upper(),
        "generated_at_ms": _int(world.get("generated_at_ms")),
        "facts": facts,
        "observed_facts": [item for item in facts if item["fact_class"] == "FACT"],
        "inferences": [item for item in facts if item["fact_class"] == "INFERENCE"],
        "source_summary": _safe_value(world.get("source_summary") or {}),
        "conflict_count": sum(1 for item in facts if item["conflicted"]),
        "expired_count": sum(1 for item in facts if item["expired"]),
        "read_only": True,
        "write_api_available": False,
    }


def _appraisal_view(raw: Any) -> dict[str, Any] | None:
    record = _map(raw)
    appraisal_id = _text(record.get("appraisal_id"), limit=200)
    if not appraisal_id:
        return None
    appraisal = _map(record.get("appraisal")) or _map(record.get("summary"))
    bond_signal = _map(record.get("bond_signal"))
    return {
        "schema_version": _text(record.get("schema_version"), limit=120),
        "appraisal_id": appraisal_id,
        "source_event_kind": _text(record.get("source_event_kind"), limit=120),
        "source_event_ref": _text(record.get("source_event_ref"), limit=512),
        "source_event_digest": _text(record.get("source_event_digest"), limit=128),
        "parent_event_refs": _refs(record.get("parent_event_refs")),
        "ts_ms": _int(record.get("ts_ms")),
        "summary": {
            "kind": _text(appraisal.get("kind"), limit=120),
            "outcome": _text(appraisal.get("outcome"), limit=120),
            "reply_mode": _text(appraisal.get("reply_mode"), limit=120),
            "valence": round(_number(appraisal.get("valence")), 4),
            "arousal": round(_number(appraisal.get("arousal")), 4),
            "relevance": round(_number(appraisal.get("relevance")), 4),
            "confidence": round(max(0.0, min(1.0, _number(appraisal.get("confidence")))), 4),
            "epistemic_type": _text(appraisal.get("epistemic_type"), default="inferred", limit=80),
        },
        "bond_signal": {
            "direction": _text(bond_signal.get("direction"), default="none", limit=120),
            "strength": round(max(0.0, min(1.0, _number(bond_signal.get("strength")))), 4),
            "permission_effect": _text(bond_signal.get("permission_effect"), default="NONE", limit=80).upper(),
            "requires_governed_command": bool(bond_signal.get("requires_governed_command", True)),
        },
        "evidence_refs": _refs(record.get("evidence_refs")),
        "reason_tags": _refs(record.get("reason_tags")),
        "read_only": True,
    }


def build_appraisal_evidence_view(raw: Any) -> dict[str, Any] | None:
    """Public privacy boundary for one appraisal observation."""
    return _appraisal_view(raw)


def _appraisals_view(raw: Any) -> dict[str, Any]:
    values = list(raw or []) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) else []
    redacted_private_fields = sum(_private_field_count(value) for value in values)
    records = [item for item in (_appraisal_view(value) for value in values) if item is not None]
    records.sort(key=lambda item: (item["ts_ms"], item["appraisal_id"]), reverse=True)
    return {
        "records": records[:50],
        "record_count": len(records),
        "evidence_ref_count": len({ref for item in records for ref in item["evidence_refs"]}),
        "private_body_included": False,
        "sanitizer_enforced": True,
        "redacted_private_field_count": redacted_private_fields,
        "read_only": True,
    }


def _bond_view(raw: Any) -> dict[str, Any]:
    bond = _map(raw)
    compatibility = _map(bond.get("compatibility"))
    relationship = _map(bond.get("relationship"))
    return {
        "schema_version": _text(bond.get("schema_version"), limit=120),
        "projection_id": _text(bond.get("projection_id"), limit=200),
        "status": _text(bond.get("status"), default="UNAVAILABLE", limit=80).upper(),
        "projected_at_ms": _int(bond.get("projected_at_ms")),
        "reason_codes": _refs(bond.get("reason_codes")),
        "authority_label": "PROJECTION_ONLY",
        "authoritative": False,
        "permission_effect": "NONE",
        "compatibility": {
            "current_stage": compatibility.get("current_stage"),
            "total_score": compatibility.get("total_score"),
            "trend": compatibility.get("trend"),
            "event_count": _int(compatibility.get("event_count")),
            "last_event_ts_ms": _int(compatibility.get("last_event_ts_ms")),
            "epistemic_type": _text(compatibility.get("epistemic_type"), default="compatibility_projection", limit=120),
        },
        "relationship": {
            "vector": _safe_value(relationship.get("vector") or {}),
            "texture": _safe_value(relationship.get("texture") or {}),
            "rift": _safe_value(relationship.get("rift") or {}),
            "repair": _safe_value(relationship.get("repair") or {}),
            "boundary": _safe_value(relationship.get("boundary") or {}),
        },
        "evidence_refs": _refs(bond.get("evidence_refs")),
        "read_only": True,
        "write_api_available": False,
    }


def _scene_view(raw: Any, *, now_ms: int) -> dict[str, Any]:
    scene = _map(raw)
    decision_id = _text(scene.get("decision_id"), limit=200)
    if not decision_id:
        return {
            "schema_version": "",
            "status": "UNAVAILABLE",
            "decision_id": "",
            "scene_type": "unavailable",
            "scene_tags": [],
            "observed_at_ms": 0,
            "expires_at_ms": 0,
            "ttl_remaining_ms": None,
            "expired": False,
            "confidence": 0.0,
            "epistemic_type": "unavailable",
            "conflict_state": "SOURCE_UNAVAILABLE",
            "conflicted": True,
            "source_refs": [],
            "observation_source_ref": "",
            "observed_from_actual_producer": False,
            "recorded_proactive_policy": "UNAVAILABLE",
            "proactive_policy": "UNAVAILABLE",
            "expression_mode": "unavailable",
            "reason_codes": ["SCENE_OBSERVATION_UNAVAILABLE"],
            "user_goal_preserved": True,
            "permission_effect": "RESTRICT_ONLY",
            "read_only": True,
        }
    expires = _int(scene.get("expires_at_ms"))
    expired = bool(scene.get("expired")) or bool(expires and now_ms >= expires)
    recorded_policy = _text(scene.get("proactive_policy"), default="ALLOW", limit=80).upper()
    conflict_state = _text(scene.get("conflict_state"), default="NONE", limit=80).upper()
    return {
        "schema_version": _text(scene.get("schema_version"), limit=120),
        "status": "OBSERVED",
        "decision_id": decision_id,
        "scene_type": _text(scene.get("scene_type"), default="general", limit=120),
        "scene_tags": _refs(scene.get("scene_tags")),
        "observed_at_ms": _int(scene.get("observed_at_ms")),
        "expires_at_ms": expires,
        "ttl_remaining_ms": max(0, expires - now_ms) if expires else None,
        "expired": expired,
        "confidence": round(max(0.0, min(1.0, _number(scene.get("confidence")))), 4),
        "epistemic_type": _text(scene.get("epistemic_type"), default="derived", limit=80),
        "conflict_state": conflict_state,
        "conflicted": "CONFLICT" in conflict_state or conflict_state == "SOURCE_UNAVAILABLE",
        "source_refs": _refs(scene.get("source_refs")),
        "observation_source_ref": _text(scene.get("observation_source_ref"), limit=512),
        "observed_from_actual_producer": bool(scene.get("observed_from_actual_producer")),
        "recorded_proactive_policy": recorded_policy,
        "proactive_policy": "ALLOW" if expired else recorded_policy,
        "expression_mode": _text(scene.get("expression_mode"), default="natural", limit=120),
        "reason_codes": _refs(scene.get("reason_codes")),
        "user_goal_preserved": bool(scene.get("user_goal_preserved", True)),
        "permission_effect": "RESTRICT_ONLY",
        "read_only": True,
    }


def _arbiter_view(raw: Any) -> dict[str, Any]:
    decision = _map(raw)
    decision_id = _text(decision.get("decision_id"), limit=200)
    if not decision_id:
        return {
            "status": "UNAVAILABLE",
            "decision_id": "",
            "decision": "BLOCK",
            "execution_state": "NOT_OBSERVED",
            "reason_codes": ["ACTION_ARBITER_OBSERVATION_UNAVAILABLE"],
            "restrictions": [],
            "governor_decision": "",
            "committed_route_token": "",
            "authority_binding_ref": "",
            "scene_decision_ref": "",
            "authority_binding_valid": False,
            "user_goal_preserved": False,
            "observation_source_ref": "",
            "observed_from_actual_producer": False,
            "finality": "NO_COMMITTED_DECISION",
            "read_only": True,
        }
    return {
        "schema_version": _text(decision.get("schema_version"), limit=120),
        "status": "OBSERVED",
        "decision_id": decision_id,
        "decision": _text(decision.get("decision"), default="BLOCK", limit=80),
        "execution_state": _text(decision.get("execution_state"), default="BLOCKED", limit=80),
        "reason_codes": _refs(decision.get("reason_codes")),
        "restrictions": _refs(decision.get("restrictions")),
        "governor_decision": _text(decision.get("governor_decision"), limit=80),
        "committed_route_token": _text(decision.get("committed_route_token"), limit=120),
        "authority_binding_ref": _text(decision.get("authority_binding_ref"), limit=512),
        "scene_decision_ref": _text(decision.get("scene_decision_ref"), limit=512),
        "authority_binding_valid": bool(decision.get("authority_binding_valid")),
        "user_goal_preserved": bool(decision.get("user_goal_preserved")),
        "observation_source_ref": _text(decision.get("observation_source_ref"), limit=512),
        "observed_from_actual_producer": bool(decision.get("observed_from_actual_producer")),
        "decided_at_ms": _int(decision.get("decided_at_ms")),
        "finality": "COMMITTED_ACTION_ARBITER_DECISION",
        "read_only": True,
    }


def _preview_view(raw: Any, *, scene: Mapping[str, Any]) -> dict[str, Any]:
    preview = _map(raw)
    if not preview:
        return {
            "status": "NOT_AVAILABLE",
            "would_execute": False,
            "execution_performed": False,
            "reason_codes": ["GOVERNED_ACTION_CONTEXT_REQUIRED"],
            "why_now": [
                f"scene_policy={scene.get('proactive_policy') or 'ALLOW'}",
                "final ActionArbiter decision requires a current committed Governor authority binding",
            ],
            "finality": "AUTHORITY_CONTEXT_REQUIRED",
        }
    decision = _map(preview.get("arbiter_decision"))
    return {
        "schema_version": _text(preview.get("schema_version"), limit=120),
        "preview_id": _text(preview.get("preview_id"), limit=200),
        "status": _text(preview.get("status"), default="DRY_RUN", limit=80).upper(),
        "would_execute": bool(preview.get("would_execute")),
        "execution_performed": False,
        "external_side_effects": False,
        "decision": _text(decision.get("decision"), default="BLOCK", limit=80),
        "execution_state": _text(decision.get("execution_state"), default="BLOCKED", limit=80),
        "reason_codes": _refs(decision.get("reason_codes")),
        "restrictions": _refs(decision.get("restrictions")),
        "evidence_refs": _refs(preview.get("evidence_refs")),
        "governor_decision": _text(decision.get("governor_decision"), limit=80),
        "committed_route_token": _text(decision.get("committed_route_token"), limit=120),
        "authority_binding_ref": _text(decision.get("authority_binding_ref"), limit=512),
        "scene_decision_ref": _text(decision.get("scene_decision_ref"), limit=512),
        "user_goal_preserved": bool(decision.get("user_goal_preserved")),
        "why_now": _refs(decision.get("reason_codes")) + _refs(decision.get("restrictions")),
        "finality": "GOVERNED_DRY_RUN",
    }


def build_life_evolution_view(
    *,
    person_id: str,
    world_snapshot: Any,
    appraisal_records: Any = (),
    bond_projection: Any = None,
    scene_decision: Any = None,
    action_arbiter_decision: Any = None,
    action_preview: Any = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Build the single versioned contract consumed by the Life UI."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    world = _world_view(world_snapshot, now_ms=now)
    appraisals = _appraisals_view(appraisal_records)
    bond = _bond_view(bond_projection)
    scene = _scene_view(scene_decision, now_ms=now)
    arbiter = _arbiter_view(action_arbiter_decision)
    preview = _preview_view(action_preview, scene=scene)
    seed = json.dumps(
        {
            "person_id": _text(person_id, default="default", limit=200),
            "world": world.get("generated_at_ms"),
            "appraisals": [item["appraisal_id"] for item in appraisals["records"]],
            "bond": bond.get("projection_id"),
            "scene": scene.get("decision_id"),
            "arbiter": arbiter.get("decision_id"),
            "preview": preview.get("preview_id"),
            "ts_ms": now,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema_version": LIFE_EVOLUTION_VIEW_SCHEMA_VERSION,
        "view_id": f"life_evolution_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}",
        "person_id": _text(person_id, default="default", limit=200),
        "generated_at_ms": now,
        "read_only": True,
        "write_api_available": False,
        "world": world,
        "appraisals": appraisals,
        "bond": bond,
        "scene": scene,
        "action_arbiter": arbiter,
        "action_preview": preview,
        "legend": {
            "FACT": "directly observed source",
            "INFERENCE": "derived/adjudicated/degraded projection",
            "PROJECTION_ONLY": "compatibility display; never a permission authority",
            "EXPIRED": "visible but not current",
            "CONFLICT": "sources disagree or source is unavailable",
        },
    }


__all__ = [
    "LIFE_EVOLUTION_VIEW_SCHEMA_VERSION",
    "build_appraisal_evidence_view",
    "build_life_evolution_view",
]
