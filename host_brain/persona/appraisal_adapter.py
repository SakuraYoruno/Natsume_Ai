"""Pure adapters from existing emotional events to replayable appraisal records.

The adapter deliberately owns no emotional state.  It links the existing
``EmotionalProactiveEpisode`` / aftereffect events to derived appraisal,
affect/drive, and expression views so the same source event can be replayed
without creating a second VAD, inner-weather, or relationship writer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


APPRAISAL_SCHEMA_VERSION = "Natsume.appraisal.record.v1"
APPRAISAL_EPISODE_KIND = "emotional_proactive_episode"
APPRAISAL_AFTEREFFECT_KIND = "emotional_proactive_aftereffect"


@dataclass(frozen=True, slots=True)
class AppraisalRecord(Mapping[str, Any]):
    """Immutable, content-minimized read model for one emotional event."""

    schema_version: str
    appraisal_id: str
    source_event_kind: str
    source_event_ref: str
    source_event_digest: str
    parent_event_refs: tuple[str, ...]
    ts_ms: int
    appraisal: dict[str, Any]
    affect_drive: dict[str, Any]
    episode_expression: dict[str, Any]
    bond_signal: dict[str, Any]
    evidence_refs: tuple[str, ...]
    reason_tags: tuple[str, ...]
    replay_chain: tuple[dict[str, Any], ...]
    read_only: bool = True
    write_effect: str = "NONE"
    relationship_permission_effect: str = "NONE"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self):
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())


def _event_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    as_dict = getattr(event, "as_dict", None)
    if callable(as_dict):
        value = as_dict()
        if isinstance(value, Mapping):
            return dict(value)
    raise TypeError("appraisal source must be a mapping or expose as_dict()")


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _clamp(value: Any, *, low: float, high: float, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(low, min(high, resolved)), 4)


def _text(value: Any, limit: int = 320) -> str:
    return str(value or "").strip()[:limit]


def _refs(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, Sequence) or isinstance(values, (bytes, bytearray)):
        return []
    result: list[str] = []
    for value in values:
        ref = _text(value, 500)
        if ref and ref not in result:
            result.append(ref)
    return result


def _evidence_refs(data: Mapping[str, Any], *, source_ref: str, parent_refs: Sequence[str] = ()) -> tuple[str, ...]:
    refs = [source_ref, *_refs(parent_refs), *_refs(data.get("evidence_refs"))]
    evidence = data.get("evidence")
    if isinstance(evidence, Mapping):
        refs.extend(_refs(evidence.get("evidence_refs")))
    return tuple(dict.fromkeys(ref for ref in refs if ref))


def _appraisal_id(*, source_ref: str, source_digest: str) -> str:
    seed = f"{APPRAISAL_SCHEMA_VERSION}|{source_ref}|{source_digest}".encode("utf-8")
    return f"appraisal_{hashlib.sha256(seed).hexdigest()[:24]}"


def _replay_chain(
    *,
    appraisal_id: str,
    source_ref: str,
    evidence_refs: Sequence[str],
    include_bond_signal: bool,
) -> tuple[dict[str, Any], ...]:
    appraisal_ref = f"appraisal://{appraisal_id}#appraisal"
    affect_ref = f"appraisal://{appraisal_id}#affect-drive"
    expression_ref = f"appraisal://{appraisal_id}#episode-expression"
    chain: list[dict[str, Any]] = [
        {
            "stage": "event",
            "ref": source_ref,
            "input_refs": [ref for ref in evidence_refs if ref != source_ref],
            "operation": "read_source_event",
        },
        {
            "stage": "appraisal",
            "ref": appraisal_ref,
            "input_refs": [source_ref],
            "operation": "derive_appraisal",
        },
        {
            "stage": "affect_drive",
            "ref": affect_ref,
            "input_refs": [appraisal_ref, source_ref],
            "operation": "adapt_existing_affect_and_drives",
        },
        {
            "stage": "episode_expression",
            "ref": expression_ref,
            "input_refs": [affect_ref, source_ref],
            "operation": "adapt_existing_expression_policy",
        },
    ]
    if include_bond_signal:
        chain.append(
            {
                "stage": "bond_signal",
                "ref": f"appraisal://{appraisal_id}#bond-signal",
                "input_refs": [expression_ref, source_ref],
                "operation": "project_signal_without_write",
            }
        )
    return tuple(chain)


def build_appraisal_from_emotional_episode(event: Any) -> AppraisalRecord:
    """Replay an emotional proactive episode into an immutable appraisal view."""

    data = _event_dict(event)
    episode_id = _text(data.get("episode_id"), 200)
    if not episode_id:
        raise ValueError("emotional proactive episode requires episode_id")
    ts_ms = int(data.get("ts_ms") or 0)
    if ts_ms <= 0:
        raise ValueError("emotional proactive episode requires positive ts_ms")

    source_ref = f"emotional_proactive_episode://{episode_id}"
    source_digest = _canonical_digest(data)
    appraisal_id = _appraisal_id(source_ref=source_ref, source_digest=source_digest)
    evidence_refs = _evidence_refs(data, source_ref=source_ref)
    emotional = dict(data.get("emotional_state") or {})

    # Reuse the existing drive adapter rather than introducing another drive
    # model.  The embedded source episode is intentionally removed from this
    # minimized read model; source_ref + digest retain the replay linkage.
    from host_brain.persona.emotional_proactive import episode_to_motive_state  # noqa: PLC0415

    motive_state = dict(episode_to_motive_state(data))
    motive_state.pop("proactive_episode", None)
    appraisal = {
        "kind": APPRAISAL_EPISODE_KIND,
        "motive": _text(data.get("motive"), 160),
        "felt_reason": _text(data.get("felt_reason"), 500),
        "valence": _clamp(emotional.get("drifted_valence"), low=-1.0, high=1.0),
        "arousal": _clamp(
            emotional.get("drifted_arousal", data.get("emotional_drive")),
            low=0.0,
            high=1.0,
        ),
        "relevance": _clamp(data.get("emotional_drive"), low=0.0, high=1.0),
        "disturbance_risk": _clamp(data.get("disturbance_risk"), low=0.0, high=1.0),
        "confidence": 0.82 if _text(data.get("felt_reason")) else 0.68,
        "epistemic_type": "inferred",
    }
    affect_drive = {
        "affect": {
            "valence": appraisal["valence"],
            "arousal": appraisal["arousal"],
            "source": "existing_emotional_state",
        },
        "drives": motive_state,
        "relationship_permission_input": _clamp(
            data.get("relationship_permission"),
            low=0.0,
            high=1.0,
        ),
        "relationship_permission_effect": "NONE",
    }
    expression = {
        "episode_ref": source_ref,
        "chosen_action": _text(data.get("chosen_action"), 120),
        "send_allowed": bool(data.get("send_allowed")),
        "opening_style": _text(data.get("opening_style"), 120),
        "opening_message": _text(data.get("opening_message"), 500),
        "no_reply_policy": _text(data.get("no_reply_policy"), 200),
        "expected_reply_modes": _refs(data.get("expected_reply_modes")),
        "aftereffect_plan": dict(data.get("aftereffect_plan") or {}),
    }
    bond_signal = {
        "direction": "none",
        "strength": 0.0,
        "reason": "episode_does_not_write_bond",
        "permission_effect": "NONE",
        "requires_governed_command": True,
    }
    chain = _replay_chain(
        appraisal_id=appraisal_id,
        source_ref=source_ref,
        evidence_refs=evidence_refs,
        include_bond_signal=False,
    )
    return AppraisalRecord(
        schema_version=APPRAISAL_SCHEMA_VERSION,
        appraisal_id=appraisal_id,
        source_event_kind=APPRAISAL_EPISODE_KIND,
        source_event_ref=source_ref,
        source_event_digest=source_digest,
        parent_event_refs=(),
        ts_ms=ts_ms,
        appraisal=appraisal,
        affect_drive=affect_drive,
        episode_expression=expression,
        bond_signal=bond_signal,
        evidence_refs=evidence_refs,
        reason_tags=tuple(_refs(data.get("reason_tags"))),
        replay_chain=chain,
    )


def build_appraisal_from_emotional_aftereffect(event: Any) -> AppraisalRecord:
    """Replay a proactive aftereffect, preserving boundary and repair meaning."""

    data = _event_dict(event)
    aftereffect_id = _text(data.get("aftereffect_id"), 200)
    episode_id = _text(data.get("episode_id"), 200)
    if not aftereffect_id or not episode_id:
        raise ValueError("emotional proactive aftereffect requires aftereffect_id and episode_id")
    ts_ms = int(data.get("ts_ms") or 0)
    if ts_ms <= 0:
        raise ValueError("emotional proactive aftereffect requires positive ts_ms")

    source_ref = f"emotional_proactive_aftereffect://{aftereffect_id}"
    parent_ref = f"emotional_proactive_episode://{episode_id}"
    source_digest = _canonical_digest(data)
    appraisal_id = _appraisal_id(source_ref=source_ref, source_digest=source_digest)
    evidence_refs = _evidence_refs(data, source_ref=source_ref, parent_refs=[parent_ref])
    outcome = _text(data.get("outcome"), 80)
    reply_mode = _text(data.get("reply_mode"), 80)
    warmth = _clamp(data.get("reply_warmth"), low=0.0, high=1.0)
    valence = _clamp((warmth * 2.0) - 1.0, low=-1.0, high=1.0) if outcome == "replied" else 0.0
    loneliness_delta = _clamp(data.get("loneliness_delta"), low=-1.0, high=1.0)
    anticipation_delta = _clamp(data.get("anticipation_delta"), low=-1.0, high=1.0)
    appraisal = {
        "kind": APPRAISAL_AFTEREFFECT_KIND,
        "outcome": outcome,
        "reply_mode": reply_mode,
        "reply_warmth": warmth,
        "emotional_resolution": _text(data.get("emotional_resolution"), 240),
        "valence": valence,
        "arousal": round(max(abs(loneliness_delta), abs(anticipation_delta)), 4),
        "confidence": 0.9 if outcome == "replied" else 0.76,
        "epistemic_type": "observed_feedback",
    }
    affect_drive = {
        "affect": {
            "valence": valence,
            "reply_warmth": warmth,
            "source": "existing_emotional_aftereffect",
        },
        "drives": {
            "loneliness_delta": loneliness_delta,
            "anticipation_delta": anticipation_delta,
            "next_impulse_modulation": dict(data.get("next_impulse_modulation") or {}),
            "state_update_hint": dict(data.get("state_update_hint") or {}),
        },
        "relationship_permission_effect": "NONE",
    }
    expression = {
        "episode_ref": parent_ref,
        "aftereffect_ref": source_ref,
        "outcome": outcome,
        "reply_mode": reply_mode,
        "emotional_resolution": appraisal["emotional_resolution"],
        "next_expression_policy": dict(data.get("next_impulse_modulation") or {}),
    }
    source_bond_signal = dict(data.get("bond_signal") or {})
    bond_signal = {
        "direction": _text(source_bond_signal.get("direction"), 100) or "neutral",
        "strength": _clamp(source_bond_signal.get("strength"), low=0.0, high=1.0),
        "reason": _text(source_bond_signal.get("reason"), 160),
        "permission_effect": "NONE",
        "requires_governed_command": True,
    }
    chain = _replay_chain(
        appraisal_id=appraisal_id,
        source_ref=source_ref,
        evidence_refs=evidence_refs,
        include_bond_signal=True,
    )
    return AppraisalRecord(
        schema_version=APPRAISAL_SCHEMA_VERSION,
        appraisal_id=appraisal_id,
        source_event_kind=APPRAISAL_AFTEREFFECT_KIND,
        source_event_ref=source_ref,
        source_event_digest=source_digest,
        parent_event_refs=(parent_ref,),
        ts_ms=ts_ms,
        appraisal=appraisal,
        affect_drive=affect_drive,
        episode_expression=expression,
        bond_signal=bond_signal,
        evidence_refs=evidence_refs,
        reason_tags=tuple(_refs(data.get("reason_tags"))),
        replay_chain=chain,
    )


def replay_appraisal(event: Any) -> AppraisalRecord:
    """Route an existing event through the deterministic appraisal adapter."""

    data = _event_dict(event)
    if _text(data.get("aftereffect_id")):
        return build_appraisal_from_emotional_aftereffect(data)
    if _text(data.get("episode_id")):
        return build_appraisal_from_emotional_episode(data)
    raise ValueError("unsupported appraisal source event")


def replay_appraisal_chain(record: AppraisalRecord | Mapping[str, Any]) -> dict[str, Any]:
    """Validate and expose the complete reference chain without side effects."""

    data = record.as_dict() if isinstance(record, AppraisalRecord) else dict(record)
    if data.get("schema_version") != APPRAISAL_SCHEMA_VERSION:
        raise ValueError("unsupported appraisal schema_version")
    source_ref = _text(data.get("source_event_ref"), 500)
    evidence_refs = _refs(data.get("evidence_refs"))
    chain = list(data.get("replay_chain") or [])
    if not source_ref or source_ref not in evidence_refs or len(chain) < 4:
        raise ValueError("appraisal replay chain is incomplete")

    known_refs = set(evidence_refs)
    expected_prefix = ["event", "appraisal", "affect_drive", "episode_expression"]
    for index, node in enumerate(chain):
        if not isinstance(node, Mapping):
            raise ValueError("appraisal replay node must be a mapping")
        stage = _text(node.get("stage"), 80)
        ref = _text(node.get("ref"), 500)
        inputs = _refs(node.get("input_refs"))
        if index < len(expected_prefix) and stage != expected_prefix[index]:
            raise ValueError("appraisal replay stage order is invalid")
        if not ref or any(item not in known_refs for item in inputs):
            raise ValueError("appraisal replay node contains an unresolved reference")
        known_refs.add(ref)

    return {
        "schema_version": APPRAISAL_SCHEMA_VERSION,
        "appraisal_id": _text(data.get("appraisal_id"), 200),
        "source_event_ref": source_ref,
        "source_event_digest": _text(data.get("source_event_digest"), 128),
        "stages": chain,
        "terminal_ref": _text(chain[-1].get("ref"), 500),
        "evidence_refs": evidence_refs,
        "appraisal": dict(data.get("appraisal") or {}),
        "affect_drive": dict(data.get("affect_drive") or {}),
        "episode_expression": dict(data.get("episode_expression") or {}),
        "bond_signal": dict(data.get("bond_signal") or {}),
        "read_only": True,
        "write_effect": "NONE",
    }


__all__ = [
    "APPRAISAL_AFTEREFFECT_KIND",
    "APPRAISAL_EPISODE_KIND",
    "APPRAISAL_SCHEMA_VERSION",
    "AppraisalRecord",
    "build_appraisal_from_emotional_aftereffect",
    "build_appraisal_from_emotional_episode",
    "replay_appraisal",
    "replay_appraisal_chain",
]
