from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from host_brain.persona.surface_organ_influence import (
    SURFACE_ORGAN_IDS,
    SurfaceOrganInfluenceOverlay,
    build_surface_organ_influence_overlay,
    organ_surface_rule_table,
)


CONTRACT_FIELDS: tuple[str, ...] = (
    "task_delta",
    "emotion_delta",
    "relationship_delta",
    "drive_delta",
    "memory_candidates",
    "continuity_delta",
    "attention_focus",
    "inner_world_seed",
    "surface_constraints",
    "delivery_delta",
)
_RAW_CONTEXT_KEYS = frozenset(
    {
        "agent_advice",
        "assistant_history",
        "candidate",
        "candidates",
        "conversation_history",
        "dialogue_history",
        "history",
        "history_turns",
        "long_term_memory",
        "messages",
        "module_advice",
        "natural_language_advice",
        "organ_outputs",
        "rag_documents",
        "raw_history",
        "raw_long_term_memory",
        "raw_memory",
        "raw_organ_outputs",
        "retrieved_docs",
        "runtime_state",
        "state",
        "tool_candidate_list",
        "tool_candidates",
        "uncompressed_rag_documents",
    }
)
_SLOT_TO_CONTRACT_FIELDS: Mapping[str, tuple[str, ...]] = {
    "task": ("task_delta", "attention_focus"),
    "affect": ("emotion_delta",),
    "relationship": ("relationship_delta",),
    "drive": ("drive_delta",),
    "memory": ("memory_candidates", "continuity_delta", "attention_focus"),
    "continuity": ("continuity_delta", "inner_world_seed", "attention_focus"),
    "safety": ("surface_constraints",),
    "delivery": ("delivery_delta", "surface_constraints"),
}


@dataclass(frozen=True)
class OrganInfluenceContract:
    organ_id: str
    allowed_fields: tuple[str, ...]
    priority: int = 50
    source_slots: tuple[str, ...] = ()
    prompt_visibility: str = "contract_only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "organ_id": self.organ_id,
            "allowed_fields": list(self.allowed_fields),
            "priority": int(self.priority),
            "source_slots": list(self.source_slots),
            "prompt_visibility": self.prompt_visibility,
        }


@dataclass(frozen=True)
class OrganInfluencePacket:
    task_delta: Mapping[str, Any] = field(default_factory=dict)
    emotion_delta: Mapping[str, Any] = field(default_factory=dict)
    relationship_delta: Mapping[str, Any] = field(default_factory=dict)
    drive_delta: Mapping[str, Any] = field(default_factory=dict)
    memory_candidates: tuple[str, ...] = ()
    continuity_delta: Mapping[str, Any] = field(default_factory=dict)
    attention_focus: tuple[str, ...] = ()
    inner_world_seed: tuple[str, ...] = ()
    surface_constraints: tuple[str, ...] = ()
    delivery_delta: Mapping[str, Any] = field(default_factory=dict)
    source: str = "organ_influence_contract"
    organ_trace: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_delta": dict(self.task_delta),
            "emotion_delta": dict(self.emotion_delta),
            "relationship_delta": dict(self.relationship_delta),
            "drive_delta": dict(self.drive_delta),
            "memory_candidates": list(self.memory_candidates),
            "continuity_delta": dict(self.continuity_delta),
            "attention_focus": list(self.attention_focus),
            "inner_world_seed": list(self.inner_world_seed),
            "surface_constraints": list(self.surface_constraints),
            "delivery_delta": dict(self.delivery_delta),
            "source": self.source,
            "organ_trace": dict(self.organ_trace),
        }


def organ_influence_contract_table() -> tuple[dict[str, Any], ...]:
    return tuple(contract.as_dict() for contract in _organ_contracts())


def build_organ_influence_packet(organ_context: Mapping[str, Any] | None) -> OrganInfluencePacket:
    """Compress raw organ-family context into the only host-side influence contract.

    This is the boundary between the 23 organ family and downstream main-chain
    consumers. Raw organ output, raw memories, long history, tool candidates,
    and module-authored advice are counted for traceability but never copied.
    """

    overlay = build_surface_organ_influence_overlay(organ_context)
    context = _safe_mapping(organ_context)
    return organ_influence_packet_from_overlay(overlay, organ_context=context)


def organ_influence_packet_from_overlay(
    overlay: SurfaceOrganInfluenceOverlay,
    *,
    organ_context: Mapping[str, Any] | None = None,
) -> OrganInfluencePacket:
    context = _safe_mapping(organ_context)
    trace = dict(overlay.trace)
    trace["contract_version"] = 1
    trace["contract_fields"] = list(CONTRACT_FIELDS)
    trace["contract_allowed_fields_by_organ"] = {
        row["organ_id"]: list(row["allowed_fields"]) for row in organ_influence_contract_table()
    }
    trace["raw_context_dropped"] = int(trace.get("dropped_raw_field_count") or 0) > 0

    task_delta = _safe_mapping(overlay.task)
    continuity_delta = _safe_mapping(overlay.continuity)
    return OrganInfluencePacket(
        task_delta=task_delta,
        emotion_delta=_safe_mapping(overlay.affect),
        relationship_delta=_safe_mapping(overlay.relationship),
        drive_delta=_safe_mapping(overlay.drive),
        memory_candidates=tuple(_memory_candidates(context)),
        continuity_delta=continuity_delta,
        attention_focus=tuple(
            _clean_list(
                [
                    *_coerce_sequence(task_delta.get("style_anchor")),
                    *_coerce_sequence(continuity_delta.get("style_anchor")),
                ],
                limit=8,
            )
        ),
        inner_world_seed=tuple(_inner_world_seed(context, continuity_delta=continuity_delta)),
        surface_constraints=tuple(_clean_list(list(overlay.safety), limit=10)),
        delivery_delta=_safe_mapping(overlay.delivery),
        organ_trace=trace,
    )


def organ_influence_packet_from_mapping(raw: OrganInfluencePacket | Mapping[str, Any] | None) -> OrganInfluencePacket:
    if raw is None:
        return OrganInfluencePacket()
    if isinstance(raw, OrganInfluencePacket):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("organ influence packet must be a mapping")
    _reject_raw_context(raw)
    payload = dict(raw)
    return OrganInfluencePacket(
        task_delta=_safe_mapping(payload.get("task_delta")),
        emotion_delta=_safe_mapping(payload.get("emotion_delta")),
        relationship_delta=_safe_mapping(payload.get("relationship_delta")),
        drive_delta=_safe_mapping(payload.get("drive_delta")),
        memory_candidates=tuple(_clean_list(_coerce_sequence(payload.get("memory_candidates")), limit=8)),
        continuity_delta=_safe_mapping(payload.get("continuity_delta")),
        attention_focus=tuple(_clean_list(_coerce_sequence(payload.get("attention_focus")), limit=8)),
        inner_world_seed=tuple(_clean_list(_coerce_sequence(payload.get("inner_world_seed")), limit=6)),
        surface_constraints=tuple(_clean_list(_coerce_sequence(payload.get("surface_constraints")), limit=10)),
        delivery_delta=_safe_mapping(payload.get("delivery_delta")),
        source=_clean_text(payload.get("source")) or "organ_influence_contract",
        organ_trace=dict(payload.get("organ_trace") or {}) if isinstance(payload.get("organ_trace"), Mapping) else {},
    )


def _organ_contracts() -> tuple[OrganInfluenceContract, ...]:
    rows: list[OrganInfluenceContract] = []
    for row in organ_surface_rule_table():
        source_slots = tuple(str(slot) for slot in list(row.get("allowed_slots") or []))
        allowed_fields = _allowed_contract_fields(source_slots)
        rows.append(
            OrganInfluenceContract(
                organ_id=str(row.get("organ_id") or ""),
                allowed_fields=allowed_fields,
                priority=int(row.get("priority") or 50),
                source_slots=source_slots,
            )
        )
    return tuple(rows)


def _allowed_contract_fields(source_slots: Sequence[str]) -> tuple[str, ...]:
    fields: list[str] = []
    for slot in source_slots:
        for field_name in _SLOT_TO_CONTRACT_FIELDS.get(str(slot), ()):
            if field_name not in fields:
                fields.append(field_name)
    return tuple(fields)


def _memory_candidates(context: Mapping[str, Any]) -> list[str]:
    memory_layers = {
        **_safe_mapping(context.get("memory_layers")),
        **_safe_mapping(context.get("hippocampus_state")),
    }
    candidates = [
        "近期摘要可用于连续性。" if memory_layers.get("recent_summary_present") else "",
        "情景记忆层可用于事实召回。" if memory_layers.get("episodic_available") else "",
        "语义记忆层可用于稳定偏好。" if memory_layers.get("semantic_available") else "",
        "自传记忆层可用于自我连续性。" if memory_layers.get("autobiographical_available") else "",
        f"最近记忆数量：{int(_coerce_float(memory_layers.get('recent_memory_count'), default=0.0))}"
        if _coerce_float(memory_layers.get("recent_memory_count"), default=0.0) > 0
        else "",
    ]
    return _clean_list(candidates, limit=6)


def _inner_world_seed(context: Mapping[str, Any], *, continuity_delta: Mapping[str, Any]) -> list[str]:
    default_mode = _safe_mapping(context.get("default_mode_state"))
    inner = _safe_mapping(default_mode.get("inner_monologue"))
    directive = _safe_mapping(default_mode.get("self_directive"))
    seeds = [
        "内在连续性活跃。" if default_mode.get("thought_stream_active") else "",
        "自我模型活跃。" if default_mode.get("self_identity") or directive.get("self_identity") else "",
        *_coerce_sequence(default_mode.get("style_anchor")),
        *_coerce_sequence(inner.get("style_anchor")),
        *_coerce_sequence(directive.get("style_anchor")),
        *_coerce_sequence(continuity_delta.get("style_anchor")),
    ]
    return _clean_list(seeds, limit=6)


def _reject_raw_context(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key or "").strip().lower() in _RAW_CONTEXT_KEYS:
                raise ValueError("organ influence packet contains forbidden raw context")
            _reject_raw_context(child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_raw_context(item)


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _clean_text(value: Any, *, max_chars: int = 180) -> str:
    return str(value or "").replace("\u3000", " ").strip()[:max_chars]


def _clean_list(values: Sequence[Any], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if limit is not None and len(result) >= limit:
            break
    return result


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


__all__ = [
    "CONTRACT_FIELDS",
    "OrganInfluenceContract",
    "OrganInfluencePacket",
    "SURFACE_ORGAN_IDS",
    "build_organ_influence_packet",
    "organ_influence_contract_table",
    "organ_influence_packet_from_mapping",
    "organ_influence_packet_from_overlay",
]
