from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.runtime_helpers import _coerce_float
from host_brain.persona.surface_organ_influence import (
    SURFACE_ORGAN_IDS,
    SurfaceOrganInfluenceOverlay,
    build_surface_organ_influence_overlay,
    organ_surface_rule_table,
)
from host_brain.surface_runtime.common import _safe_mapping


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
MAIN_CHAIN_EFFECTS: tuple[str, ...] = (
    "task",
    "emotion",
    "relationship",
    "memory",
    "inner_world",
    "initiative",
    "delivery",
    "safety",
    "surface_text",
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
ORGAN_CONTEXT_KEYS_BY_ORGAN: Mapping[str, tuple[str, ...]] = {
    "ans": ("vital_signs", "ans_state"),
    "cerebellum": ("cerebellum_state",),
    "circadian": ("circadian_state",),
    "cortex": ("cortex_state", "tribe_route"),
    "default_mode": ("default_mode_state",),
    "endocrine": ("vital_signs", "endocrine_gate"),
    "glymphatic": ("glymphatic_state",),
    "hippocampus": ("memory_layers", "hippocampus_state"),
    "immune": ("vital_signs", "immune_state", "immune_risk_level"),
    "initiative_governor": ("initiative_decision", "initiative_governor_state"),
    "interoception": ("interoception_state",),
    "language_system": ("language_dual_system", "language_style", "language_system_state"),
    "limbic": ("vital_signs", "emotion_context", "voice_emotion"),
    "mirror_neuron": ("mirror_neuron_state", "mirror_neuron_resonance"),
    "motor_cortex": ("motor_cortex_state",),
    "pain_reward": ("vital_signs", "hedonic_state", "pain_reward_state", "loneliness_level"),
    "peripheral": ("peripheral_state",),
    "prefrontal": ("prefrontal_state",),
    "proprioception": ("vital_signs", "proprioception_state"),
    "social_brain": ("bond_context", "bond_vector", "social_brain_state"),
    "somatic": ("somatic_state",),
    "thalamus": ("thalamus_state", "tribe_route"),
    "vestibular": ("vestibular_state",),
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
class OrganMainChainIntegration:
    organ_id: str
    context_keys: tuple[str, ...]
    contract_fields: tuple[str, ...]
    main_chain_effects: tuple[str, ...]
    role_summary: str
    prompt_visibility: str = "contract_only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "organ_id": self.organ_id,
            "context_keys": list(self.context_keys),
            "contract_fields": list(self.contract_fields),
            "main_chain_effects": list(self.main_chain_effects),
            "role_summary": self.role_summary,
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


def organ_main_chain_integration_table() -> tuple[dict[str, Any], ...]:
    contracts = {row.organ_id: row for row in _organ_contracts()}
    rows: list[OrganMainChainIntegration] = []
    for organ_id in SURFACE_ORGAN_IDS:
        contract = contracts[organ_id]
        rows.append(
            OrganMainChainIntegration(
                organ_id=organ_id,
                context_keys=ORGAN_CONTEXT_KEYS_BY_ORGAN[organ_id],
                contract_fields=contract.allowed_fields,
                main_chain_effects=_MAIN_CHAIN_EFFECTS_BY_ORGAN[organ_id],
                role_summary=_MAIN_CHAIN_ROLE_BY_ORGAN[organ_id],
            )
        )
    return tuple(row.as_dict() for row in rows)


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


_MAIN_CHAIN_EFFECTS_BY_ORGAN: Mapping[str, tuple[str, ...]] = {
    "ans": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
    "cerebellum": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
    "circadian": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
    "cortex": ("task", "safety", "surface_text"),
    "default_mode": ("inner_world", "initiative", "safety", "surface_text"),
    "endocrine": ("emotion", "inner_world", "initiative", "surface_text"),
    "glymphatic": ("task", "emotion", "inner_world", "initiative", "memory", "delivery", "safety", "surface_text"),
    "hippocampus": ("task", "memory", "inner_world", "initiative", "safety", "surface_text"),
    "immune": ("task", "safety", "surface_text"),
    "initiative_governor": ("task", "initiative", "inner_world", "surface_text"),
    "interoception": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
    "language_system": ("delivery", "safety", "surface_text"),
    "limbic": ("emotion", "initiative", "surface_text"),
    "mirror_neuron": ("emotion", "relationship", "initiative", "surface_text"),
    "motor_cortex": ("delivery", "safety", "surface_text"),
    "pain_reward": ("emotion", "initiative", "surface_text"),
    "peripheral": ("delivery", "safety", "surface_text"),
    "prefrontal": ("task", "inner_world", "safety", "surface_text"),
    "proprioception": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
    "social_brain": ("relationship", "initiative", "surface_text"),
    "somatic": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
    "thalamus": ("task", "inner_world", "safety", "surface_text"),
    "vestibular": ("task", "emotion", "inner_world", "initiative", "delivery", "safety", "surface_text"),
}
_MAIN_CHAIN_ROLE_BY_ORGAN: Mapping[str, str] = {
    "ans": "Controls recovery mode and proactive throttle so replies become steadier when the body budget is low.",
    "cerebellum": "Damps jitter and guards against unstable output or claimed actions.",
    "circadian": "Adds sleep-pressure and recovery-window pressure to brevity, warmth, and initiative.",
    "cortex": "Provides task route and current-goal guardrails for the surface main chain.",
    "default_mode": "Feeds private continuity, care drive, and self-model seeds without leaking inner monologue.",
    "endocrine": "Turns stress and homeostasis into emotional tone, withdrawal, and pacing pressure.",
    "glymphatic": "Signals recovery and consolidation pressure, reducing expansion and marking memory consolidation relevance.",
    "hippocampus": "Marks compressed memory availability and continuity relevance without passing raw memory rows.",
    "immune": "Raises safety and risk guardrails while keeping task completion conservative.",
    "initiative_governor": "Controls follow-up permission, question budget, and whether proactive branches should pause.",
    "interoception": "Converts internal fatigue and body budget into emotional containment and shorter continuity.",
    "language_system": "Controls output form and language-safety constraints without supplying candidate prose.",
    "limbic": "Converts emotion tags and intensity into affect tone and drive pressure.",
    "mirror_neuron": "Converts user resonance into co-regulated warmth, care, and relationship sensitivity.",
    "motor_cortex": "Exposes only delivery/action boundary constraints, never raw action plans.",
    "pain_reward": "Converts pain, reward, and loneliness into affect and motive pressure.",
    "peripheral": "Controls platform/output channel constraints and prevents false device-action claims.",
    "prefrontal": "Adds drift guardrails, planning stability, and self-control pressure.",
    "proprioception": "Converts session/cognitive load into task budget and continuity pacing.",
    "social_brain": "Converts bond state into relationship distance, teasing permission, and attachment drive.",
    "somatic": "Converts fatigue and body sensation into lower heat, shorter replies, and recovery pacing.",
    "thalamus": "Normalizes route confidence and handoff state into task/continuity guardrails.",
    "vestibular": "Converts route instability into single-thread pacing and conservative task focus.",
}


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


__all__ = [
    "CONTRACT_FIELDS",
    "MAIN_CHAIN_EFFECTS",
    "OrganInfluenceContract",
    "OrganInfluencePacket",
    "OrganMainChainIntegration",
    "ORGAN_CONTEXT_KEYS_BY_ORGAN",
    "SURFACE_ORGAN_IDS",
    "build_organ_influence_packet",
    "organ_influence_contract_table",
    "organ_influence_packet_from_mapping",
    "organ_influence_packet_from_overlay",
    "organ_main_chain_integration_table",
]
