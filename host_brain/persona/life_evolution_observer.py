"""Non-authoritative observation cache for actual Life evolution producers.

This module never writes Persona, Bond, Memory, World or Life Core.  It keeps a
bounded, content-minimized projection of decisions/events already produced by
the runtime so the Monitor UI does not synthesize a fake current Scene.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from host_brain.persona.life_evolution_view import build_appraisal_evidence_view


_LOCK = threading.RLock()
_MAX_APPRAISALS = 50
_OBSERVATIONS: dict[str, dict[str, Any]] = {}


def _enabled() -> bool:
    raw = str(os.environ.get("HOST_NLE_LIFE_VIEWS_ENABLED") or "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _map(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        try:
            resolved = as_dict()
        except TypeError:
            resolved = as_dict(now_ms=None)
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    if is_dataclass(value):
        resolved = asdict(value)
        return dict(resolved) if isinstance(resolved, Mapping) else {}
    return {}


def _text(value: Any, limit: int = 512) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _person(value: Any) -> str:
    return _text(value, 200) or "default"


def _scene_projection(value: Any, *, source_kind: str, source_ref: str) -> dict[str, Any]:
    scene = _map(value)
    return {
        "schema_version": _text(scene.get("schema_version"), 120),
        "decision_id": _text(scene.get("decision_id"), 200),
        "scene_type": _text(scene.get("scene_type"), 120),
        "scene_tags": [item[:120] for item in list(scene.get("scene_tags") or ())[:24] if isinstance(item, str)],
        "observed_at_ms": int(scene.get("observed_at_ms") or 0),
        "expires_at_ms": int(scene.get("expires_at_ms") or 0),
        "expired": bool(scene.get("expired")),
        "confidence": scene.get("confidence"),
        "epistemic_type": _text(scene.get("epistemic_type"), 80),
        "conflict_state": _text(scene.get("conflict_state"), 80) or "NONE",
        "source_refs": [item[:512] for item in list(scene.get("source_refs") or ())[:32] if isinstance(item, str)],
        "proactive_policy": _text(scene.get("proactive_policy"), 80) or "ALLOW",
        "expression_mode": _text(scene.get("expression_mode"), 120) or "natural",
        "reason_codes": [item[:160] for item in list(scene.get("reason_codes") or ())[:32] if isinstance(item, str)],
        "user_goal_preserved": bool(scene.get("user_goal_preserved", True)),
        "permission_effect": "RESTRICT_ONLY",
        "observation_source_kind": _text(source_kind, 80) or "runtime",
        "observation_source_ref": _text(source_ref, 512),
        "observed_from_actual_producer": True,
    }


def _arbiter_projection(value: Any, *, source_kind: str, source_ref: str) -> dict[str, Any]:
    decision = _map(value)
    return {
        "schema_version": _text(decision.get("schema_version"), 120),
        "decision_id": _text(decision.get("decision_id"), 200),
        "decision": _text(decision.get("decision"), 80) or "BLOCK",
        "execution_state": _text(decision.get("execution_state"), 80) or "BLOCKED",
        "reason_codes": [item[:160] for item in list(decision.get("reason_codes") or ())[:32] if isinstance(item, str)],
        "restrictions": [item[:160] for item in list(decision.get("restrictions") or ())[:32] if isinstance(item, str)],
        "governor_decision": _text(decision.get("governor_decision"), 80),
        "committed_route_token": _text(decision.get("committed_route_token"), 120),
        "authority_binding_ref": _text(decision.get("authority_binding_ref"), 512),
        "scene_decision_ref": _text(decision.get("scene_decision_ref"), 512),
        "authority_binding_valid": bool(decision.get("authority_binding_valid")),
        "user_goal_preserved": bool(decision.get("user_goal_preserved")),
        "side_effects_executed": False,
        "decided_at_ms": int(decision.get("decided_at_ms") or 0),
        "observation_source_kind": _text(source_kind, 80) or "runtime",
        "observation_source_ref": _text(source_ref, 512),
        "observed_from_actual_producer": True,
    }


def observe_scene_decision(
    decision: Any,
    *,
    person_id: str = "default",
    source_kind: str = "runtime",
    source_ref: str = "",
) -> dict[str, Any]:
    if not _enabled():
        return {}
    item = _scene_projection(decision, source_kind=source_kind, source_ref=source_ref)
    if not item["decision_id"]:
        return {}
    with _LOCK:
        state = _OBSERVATIONS.setdefault(_person(person_id), {"appraisals": []})
        state["scene_decision"] = item
    return copy.deepcopy(item)


def observe_action_arbiter_decision(
    decision: Any,
    *,
    person_id: str = "default",
    source_kind: str = "runtime",
    source_ref: str = "",
) -> dict[str, Any]:
    if not _enabled():
        return {}
    item = _arbiter_projection(decision, source_kind=source_kind, source_ref=source_ref)
    if not item["decision_id"]:
        return {}
    with _LOCK:
        state = _OBSERVATIONS.setdefault(_person(person_id), {"appraisals": []})
        state["action_arbiter_decision"] = item
    return copy.deepcopy(item)


def observe_appraisal_event(event: Any, *, person_id: str = "default") -> dict[str, Any]:
    if not _enabled():
        return {}
    from host_brain.persona.appraisal_adapter import replay_appraisal

    try:
        record = replay_appraisal(event)
        item = build_appraisal_evidence_view(record) or {}
    except (TypeError, ValueError):
        return {}
    if not item.get("appraisal_id"):
        return {}
    with _LOCK:
        state = _OBSERVATIONS.setdefault(_person(person_id), {"appraisals": []})
        values = [entry for entry in list(state.get("appraisals") or []) if entry.get("appraisal_id") != item["appraisal_id"]]
        values.append(item)
        values.sort(key=lambda entry: (int(entry.get("ts_ms") or 0), str(entry.get("appraisal_id") or "")))
        state["appraisals"] = values[-_MAX_APPRAISALS:]
    return copy.deepcopy(item)


def read_life_evolution_observations(*, person_id: str = "default") -> dict[str, Any]:
    with _LOCK:
        state = copy.deepcopy(_OBSERVATIONS.get(_person(person_id)) or {})
    return {
        "scene_decision": state.get("scene_decision") or {},
        "action_arbiter_decision": state.get("action_arbiter_decision") or {},
        "appraisals": list(state.get("appraisals") or []),
        "read_only": True,
        "authoritative": False,
    }


def read_supervisor_appraisal_events(out_dir: str | Path | None, *, limit: int = 20) -> list[dict[str, Any]]:
    """Read actual supervisor JSONL producers without returning message bodies."""
    if not out_dir:
        return []
    root = Path(out_dir).expanduser()
    events: list[dict[str, Any]] = []
    for filename, nested_key in (
        ("proactive_episodes.jsonl", "episode"),
        ("proactive_aftereffects.jsonl", "aftereffect"),
    ):
        for record in _recent_jsonl_records(root / filename, limit=limit):
            event = record.get(nested_key)
            if isinstance(event, Mapping):
                try:
                    from host_brain.persona.appraisal_adapter import replay_appraisal

                    item = build_appraisal_evidence_view(replay_appraisal(event))
                except (TypeError, ValueError):
                    item = None
                if item:
                    events.append(item)
    events.sort(key=lambda item: (int(item.get("ts_ms") or 0), str(item.get("appraisal_id") or "")), reverse=True)
    return events[: max(1, min(int(limit or 20), 50))]


def _recent_jsonl_records(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 2_000_000))
            chunk = stream.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    lines = chunk.splitlines()
    if size > 2_000_000 and lines:
        lines = lines[1:]
    values: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(values) >= max(1, min(int(limit or 20), 100)):
            break
        try:
            item = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            values.append(item)
    return values


def reset_life_evolution_observations() -> None:
    """Test-only reset; production callers should never clear observations."""
    with _LOCK:
        _OBSERVATIONS.clear()


__all__ = [
    "observe_action_arbiter_decision",
    "observe_appraisal_event",
    "observe_scene_decision",
    "read_life_evolution_observations",
    "read_supervisor_appraisal_events",
]
