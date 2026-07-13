"""Read-only compatibility projection over the existing Bond SSOT."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from host_brain.bond.neo4j_client import BOND_WRITER_ID


BOND_PROJECTION_SCHEMA_VERSION = "Natsume.bond.projection.v1"
BOND_PROJECTION_DISABLED = "HOST_LIFE_PROJECTION_DISABLED"
BOND_PROJECTION_STATE_UNAVAILABLE = "BOND_PROJECTION_STATE_UNAVAILABLE"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value: Any, limit: int = 320) -> str:
    return str(value or "").strip()[:limit]


def _as_map(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        resolved = as_dict()
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    if is_dataclass(value):
        return asdict(value)
    return {}


def _derive_social_bond_vector(
    *,
    bond_snapshot: Mapping[str, Any],
    repair_state: Mapping[str, Any],
) -> dict[str, Any]:
    # Keep Tribe imports lazy: host_brain.composition.process_services imports the Bond
    # package during startup, while Tribe modules themselves depend on that
    # dependency registry.
    from host_brain.tribe.social_brain_agent import derive_social_bond_vector  # noqa: PLC0415

    return derive_social_bond_vector(
        bond_snapshot=bond_snapshot,
        repair_state=repair_state,
    )


def _read_stage_state(stage_engine: Any, user_id: str) -> Any:
    """Read a detached state without triggering legacy INIT side effects."""
    if stage_engine is None:
        return None
    peek = getattr(stage_engine, "peek_state", None)
    if callable(peek):
        try:
            return peek(user_id)
        except Exception:
            return None

    # Compatibility fallback for older injected engines.  ``get_state`` is
    # called only after list_users proves the identity already exists.
    try:
        users = list(stage_engine.list_users()) if hasattr(stage_engine, "list_users") else []
    except Exception:
        users = []
    if user_id not in users:
        return None
    try:
        return stage_engine.get_state(user_id)
    except Exception:
        return None


def _rift_projection(state: Any) -> dict[str, Any]:
    rift = getattr(state, "rift", None) if state is not None else None
    if rift is None:
        return {
            "active": False,
            "rift_level": 0.0,
            "repair_progress": 0.0,
            "rift_cause": "",
            "repair_hint": "",
        }
    active = bool(rift.is_active()) if callable(getattr(rift, "is_active", None)) else False
    repair_hint = rift.repair_hint() if callable(getattr(rift, "repair_hint", None)) else ""
    return {
        "active": active,
        "rift_level": round(max(0.0, min(1.0, float(getattr(rift, "rift_level", 0.0) or 0.0))), 4),
        "repair_progress": round(
            max(0.0, min(1.0, float(getattr(rift, "repair_progress", 0.0) or 0.0))),
            4,
        ),
        "rift_cause": _text(getattr(rift, "rift_cause", ""), 320),
        "repair_hint": _text(repair_hint, 320),
    }


def _texture_projection(state: Any) -> dict[str, Any]:
    texture = getattr(state, "texture", None) if state is not None else None
    if texture is None:
        return {}
    return {
        "avg_response_gap_ms": round(float(getattr(texture, "avg_response_gap_ms", 0.0) or 0.0), 1),
        "recent_emotion_valence": round(
            max(-1.0, min(1.0, float(getattr(texture, "recent_emotion_valence", 0.0) or 0.0))),
            4,
        ),
        "intimacy_temperature": round(
            max(0.0, min(1.0, float(getattr(texture, "intimacy_temperature", 0.0) or 0.0))),
            4,
        ),
        "style_hint": _text(texture.style_hint() if callable(getattr(texture, "style_hint", None)) else ""),
    }


def _repair_projection(*, social_brain: Any, user_id: str, rift: Mapping[str, Any]) -> dict[str, Any]:
    social: dict[str, Any] = {}
    resolver = getattr(social_brain, "resolve_relationship_repair_state", None)
    if callable(resolver):
        try:
            social = dict(resolver(person_id=user_id) or {})
        except Exception:
            social = {}
    return {
        "rift_repair_progress": round(float(rift.get("repair_progress") or 0.0), 4),
        "social_repair_progress": round(
            max(0.0, min(1.0, float(social.get("repair_progress") or 0.0))),
            4,
        ),
        "rupture_load": round(
            max(0.0, min(1.0, float(social.get("rupture_load") or 0.0))),
            4,
        ),
        "last_rupture_ts_ms": int(social.get("last_rupture_ts_ms") or 0),
        "last_repair_ts_ms": int(social.get("last_repair_ts_ms") or 0),
        "repair_hint": _text(rift.get("repair_hint"), 320),
        "sources": ["bond_rift_state", "social_brain_relationship_repair"],
    }


def _boundary_projection(appraisal_records: Any) -> tuple[dict[str, Any], list[str]]:
    if appraisal_records is None:
        values: list[Any] = []
    elif isinstance(appraisal_records, Sequence) and not isinstance(
        appraisal_records,
        (str, bytes, bytearray),
    ):
        values = list(appraisal_records)
    else:
        values = [appraisal_records]

    candidates: list[dict[str, Any]] = []
    for value in values:
        record = _as_map(value)
        signal = record.get("bond_signal")
        signal_map = dict(signal) if isinstance(signal, Mapping) else {}
        direction = _text(signal_map.get("direction"), 100).lower()
        if direction != "respect_boundary":
            continue
        candidates.append(
            {
                "ts_ms": int(record.get("ts_ms") or 0),
                "appraisal_id": _text(record.get("appraisal_id"), 200),
                "source_event_ref": _text(record.get("source_event_ref"), 500),
                "direction": direction,
                "strength": round(
                    max(0.0, min(1.0, float(signal_map.get("strength") or 0.0))),
                    4,
                ),
                "reason": _text(signal_map.get("reason"), 160),
            }
        )
    if not candidates:
        return (
            {
                "status": "NONE",
                "explicit_user_boundary": False,
                "source_event_ref": "",
                "expression_constraint": "NONE",
                "permission_effect": "NONE",
                "requires_governed_command_for_bond_change": True,
            },
            [],
        )
    latest = max(candidates, key=lambda item: (item["ts_ms"], item["appraisal_id"]))
    return (
        {
            "status": "RESPECT_REQUIRED",
            "explicit_user_boundary": True,
            "source_event_ref": latest["source_event_ref"],
            "appraisal_id": latest["appraisal_id"],
            "direction": latest["direction"],
            "strength": latest["strength"],
            "reason": latest["reason"],
            "expression_constraint": "WITHDRAW_PROACTIVE_EXPRESSION",
            "permission_effect": "NONE",
            "requires_governed_command_for_bond_change": True,
        },
        [latest["source_event_ref"]] if latest["source_event_ref"] else [],
    )


def _disabled_projection(*, user_id: str, projected_at_ms: int) -> dict[str, Any]:
    return {
        "schema_version": BOND_PROJECTION_SCHEMA_VERSION,
        "projection_id": f"bond_projection_disabled_{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:16]}",
        "person_id": user_id,
        "projected_at_ms": projected_at_ms,
        "status": "DISABLED",
        "reason_codes": [BOND_PROJECTION_DISABLED],
        "read_only": True,
        "write_api_available": False,
        "write_methods": [],
        "write_effect": "NONE",
        "state_available": False,
        "authority": {
            "legal_writer": BOND_WRITER_ID,
            "single_writer_enforced": True,
            "evidence_required": True,
            "permission_effect": "NONE",
        },
        "compatibility": {"authority": "projection_only"},
        "relationship": {},
        "evidence_refs": [],
    }


def build_bond_projection(
    *,
    user_id: str,
    stage_engine: Any = None,
    social_brain: Any = None,
    appraisal_records: Any = None,
    now_ms: int | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Build score/stage compatibility plus rift/repair/boundary read data.

    The function never calls ``BondStageEngine.get_state`` for an unknown user
    and exposes no mutation method.  Appraisal boundary signals are projected
    as expression constraints only; they never update score, stage, or a
    relationship permission.
    """
    person_id = _text(user_id, 200) or "default"
    projected_at = int(now_ms if now_ms is not None else _now_ms())
    if not enabled:
        return _disabled_projection(user_id=person_id, projected_at_ms=projected_at)

    state = _read_stage_state(stage_engine, person_id)
    state_available = state is not None
    rift = _rift_projection(state)
    texture = _texture_projection(state)
    repair = _repair_projection(social_brain=social_brain, user_id=person_id, rift=rift)
    boundary, boundary_refs = _boundary_projection(appraisal_records)

    if state_available:
        summary_value = state.to_summary() if callable(getattr(state, "to_summary", None)) else {}
        summary = dict(summary_value) if isinstance(summary_value, Mapping) else {}
        trend = state.trend() if callable(getattr(state, "trend", None)) else ""
        trend_intensity = state.trend_intensity() if callable(getattr(state, "trend_intensity", None)) else ""
        capability_hint = state.capability_hint() if callable(getattr(state, "capability_hint", None)) else ""
        compatibility = {
            "authority": "projection_only",
            "authoritative": False,
            "epistemic_type": "compatibility_projection",
            "total_score": int(getattr(state, "total_score", 0) or 0),
            "current_stage": _text(getattr(state, "current_stage", "FRIEND"), 80) or "FRIEND",
            "event_count": int(getattr(state, "event_count", 0) or 0),
            "last_event_ts_ms": int(getattr(state, "last_event_ts_ms", 0) or 0),
            "trend": _text(trend, 80),
            "trend_intensity": _text(trend_intensity, 80),
            "capability_hint": _text(capability_hint, 320),
            "summary": summary,
            "permission_effect": "NONE",
        }
        state_ref = (
            f"bond_state://{person_id}"
            f"@events-{compatibility['event_count']}"
            f"/ts-{compatibility['last_event_ts_ms']}"
        )
    else:
        compatibility = {
            "authority": "projection_only",
            "authoritative": False,
            "epistemic_type": "unavailable",
            "total_score": None,
            "current_stage": None,
            "event_count": 0,
            "last_event_ts_ms": 0,
            "trend": "",
            "trend_intensity": "",
            "capability_hint": "",
            "summary": {},
            "permission_effect": "NONE",
        }
        state_ref = ""

    bond_snapshot = {
        "bond_stage": compatibility.get("current_stage") or "FRIEND",
        "bond_trend": compatibility.get("trend") or "",
        "bond_summary": compatibility.get("summary") or {},
        "intimacy_score": float(getattr(state, "intimacy_score", 0.0) or 0.0) if state is not None else 0.0,
    }
    social_repair = {
        "rupture_load": repair["rupture_load"],
        "repair_progress": repair["social_repair_progress"],
    }
    vector_resolver = getattr(social_brain, "resolve_bond_vector_from_snapshot", None)
    if callable(vector_resolver):
        try:
            vector = dict(
                vector_resolver(
                    bond_snapshot=bond_snapshot,
                    repair_state=social_repair,
                )
                or {}
            )
        except Exception:
            vector = _derive_social_bond_vector(
                bond_snapshot=bond_snapshot,
                repair_state=social_repair,
            )
    else:
        vector = _derive_social_bond_vector(
            bond_snapshot=bond_snapshot,
            repair_state=social_repair,
        )

    evidence_refs = list(dict.fromkeys(([state_ref] if state_ref else []) + boundary_refs))
    identity_seed = json.dumps(
        {
            "schema_version": BOND_PROJECTION_SCHEMA_VERSION,
            "person_id": person_id,
            "compatibility": compatibility,
            "vector": vector,
            "texture": texture,
            "rift": rift,
            "repair": repair,
            "boundary": boundary,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    reason_codes = [] if state_available else [BOND_PROJECTION_STATE_UNAVAILABLE]
    return {
        "schema_version": BOND_PROJECTION_SCHEMA_VERSION,
        "projection_id": f"bond_projection_{hashlib.sha256(identity_seed).hexdigest()[:24]}",
        "person_id": person_id,
        "projected_at_ms": projected_at,
        "status": "AVAILABLE" if state_available else "DEGRADED",
        "reason_codes": reason_codes,
        "read_only": True,
        "write_api_available": False,
        "write_methods": [],
        "write_effect": "NONE",
        "state_available": state_available,
        "authority": {
            "legal_writer": BOND_WRITER_ID,
            "command_path": "BondDynamicsWriter.apply_update",
            "single_writer_enforced": True,
            "evidence_required": True,
            "score_stage_authority": "BondDynamics command result",
            "permission_effect": "NONE",
        },
        "compatibility": compatibility,
        "relationship": {
            "vector": vector,
            "vector_source": "SocialBrainAgent compatibility projection",
            "texture": texture,
            "rift": rift,
            "repair": repair,
            "boundary": boundary,
            "relationship_permission": {
                "changed": False,
                "effect": "NONE",
                "reason": "read_only_projection_cannot_change_permission",
            },
        },
        "evidence_refs": evidence_refs,
    }


class BondProjectionReadModel:
    """Read facade with no write/apply/update/promote/save capability."""

    def __init__(self, *, stage_engine: Any = None, social_brain: Any = None, enabled: bool = True) -> None:
        self._stage_engine = stage_engine
        self._social_brain = social_brain
        self._enabled = bool(enabled)

    def snapshot(
        self,
        *,
        user_id: str,
        appraisal_records: Any = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        return build_bond_projection(
            user_id=user_id,
            stage_engine=self._stage_engine,
            social_brain=self._social_brain,
            appraisal_records=appraisal_records,
            now_ms=now_ms,
            enabled=self._enabled,
        )


__all__ = [
    "BOND_PROJECTION_DISABLED",
    "BOND_PROJECTION_SCHEMA_VERSION",
    "BOND_PROJECTION_STATE_UNAVAILABLE",
    "BondProjectionReadModel",
    "build_bond_projection",
]
