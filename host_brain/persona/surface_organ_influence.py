from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


SURFACE_ORGAN_IDS: tuple[str, ...] = (
    "ans",
    "cerebellum",
    "circadian",
    "cortex",
    "default_mode",
    "endocrine",
    "glymphatic",
    "hippocampus",
    "immune",
    "initiative_governor",
    "interoception",
    "language_system",
    "limbic",
    "mirror_neuron",
    "motor_cortex",
    "pain_reward",
    "peripheral",
    "prefrontal",
    "proprioception",
    "social_brain",
    "somatic",
    "thalamus",
    "vestibular",
)

_TEXT_ADVICE_KEYS = frozenset(
    {
        "advice",
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
        "source_refs",
        "tool_candidate_list",
        "tool_candidates",
        "uncompressed_rag_documents",
    }
)


@dataclass(frozen=True)
class SurfaceOrganRule:
    organ_id: str
    allowed_slots: tuple[str, ...]
    priority: int = 50
    prompt_visibility: str = "compressed_only"

    def as_dict(self) -> dict[str, Any]:
        return {
            "organ_id": self.organ_id,
            "allowed_slots": list(self.allowed_slots),
            "priority": int(self.priority),
            "prompt_visibility": self.prompt_visibility,
        }


@dataclass(frozen=True)
class SurfaceOrganInfluenceOverlay:
    task: Mapping[str, Any] = field(default_factory=dict)
    affect: Mapping[str, Any] = field(default_factory=dict)
    relationship: Mapping[str, Any] = field(default_factory=dict)
    drive: Mapping[str, Any] = field(default_factory=dict)
    continuity: Mapping[str, Any] = field(default_factory=dict)
    safety: tuple[str, ...] = ()
    delivery: Mapping[str, Any] = field(default_factory=dict)
    trace: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": dict(self.task),
            "affect": dict(self.affect),
            "relationship": dict(self.relationship),
            "drive": dict(self.drive),
            "continuity": dict(self.continuity),
            "safety": list(self.safety),
            "delivery": dict(self.delivery),
            "trace": dict(self.trace),
        }


ORGAN_SURFACE_RULES: tuple[SurfaceOrganRule, ...] = (
    SurfaceOrganRule("cortex", ("task", "safety"), 90),
    SurfaceOrganRule("thalamus", ("task", "continuity", "safety"), 88),
    SurfaceOrganRule("prefrontal", ("task", "safety"), 86),
    SurfaceOrganRule("limbic", ("affect", "drive"), 84),
    SurfaceOrganRule("social_brain", ("relationship", "drive"), 82),
    SurfaceOrganRule("hippocampus", ("memory", "continuity", "task", "drive", "safety"), 78),
    SurfaceOrganRule("language_system", ("delivery", "safety"), 76),
    SurfaceOrganRule("endocrine", ("affect", "drive", "continuity"), 70),
    SurfaceOrganRule("immune", ("safety", "task"), 70),
    SurfaceOrganRule("pain_reward", ("affect", "drive"), 68),
    SurfaceOrganRule("somatic", ("affect", "continuity", "task", "drive", "safety", "delivery"), 64),
    SurfaceOrganRule("interoception", ("affect", "continuity", "task", "drive", "safety", "delivery"), 64),
    SurfaceOrganRule("circadian", ("affect", "continuity", "task", "drive", "safety", "delivery"), 62),
    SurfaceOrganRule("glymphatic", ("continuity", "affect", "task", "safety", "delivery"), 60),
    SurfaceOrganRule("proprioception", ("task", "continuity", "affect", "safety", "delivery"), 58),
    SurfaceOrganRule("initiative_governor", ("task", "continuity"), 56),
    SurfaceOrganRule("default_mode", ("drive", "continuity", "safety"), 52),
    SurfaceOrganRule("mirror_neuron", ("affect", "drive"), 52),
    SurfaceOrganRule("motor_cortex", ("delivery", "safety"), 48),
    SurfaceOrganRule("cerebellum", ("delivery", "safety", "task", "affect", "continuity"), 48),
    SurfaceOrganRule("vestibular", ("task", "continuity", "affect", "safety", "delivery"), 46),
    SurfaceOrganRule("peripheral", ("delivery", "safety"), 44),
    SurfaceOrganRule("ans", ("affect", "continuity", "task", "safety", "delivery"), 42),
)

_RULE_BY_ORGAN = {rule.organ_id: rule for rule in ORGAN_SURFACE_RULES}
_NEGATIVE_EMOTION_TAGS = {"ANGRY", "ANGER", "HURT", "SAD", "SADNESS", "FEAR", "ANXIOUS", "LONELY", "EXHAUSTED"}
_WARM_EMOTION_TAGS = {"HAPPY", "JOY", "LOVE", "CALM", "CURIOUS", "GRATEFUL"}


def organ_surface_rule_table() -> tuple[dict[str, Any], ...]:
    return tuple(rule.as_dict() for rule in ORGAN_SURFACE_RULES)


def build_surface_organ_influence_overlay(organ_context: Mapping[str, Any] | None) -> SurfaceOrganInfluenceOverlay:
    context = _safe_mapping(organ_context)
    if not context:
        return SurfaceOrganInfluenceOverlay(trace={"mapped_organs": [], "dropped_raw_field_count": 0})

    vital = _safe_mapping(context.get("vital_signs"))
    ans = _safe_mapping(context.get("ans_state"))
    cortex = _safe_mapping(context.get("cortex_state"))
    thalamus = _safe_mapping(context.get("thalamus_state"))
    tribe_route = _safe_mapping(context.get("tribe_route"))
    prefrontal = _safe_mapping(context.get("prefrontal_state"))
    emotion = _safe_mapping(context.get("emotion_context")) or _voice_emotion_from_vitals(vital)
    bond = _safe_mapping(context.get("bond_context"))
    bond_vector = _safe_mapping(context.get("bond_vector")) or _safe_mapping(bond.get("bond_vector"))
    endocrine = _safe_mapping(context.get("endocrine_gate"))
    immune = _safe_mapping(context.get("immune_state"))
    hedonic = _safe_mapping(context.get("hedonic_state")) or _safe_mapping(context.get("pain_reward_state"))
    somatic = _safe_mapping(context.get("somatic_state"))
    interoception = _safe_mapping(context.get("interoception_state"))
    circadian = _safe_mapping(context.get("circadian_state"))
    glymphatic = _safe_mapping(context.get("glymphatic_state"))
    proprioception = _safe_mapping(context.get("proprioception_state"))
    initiative = _safe_mapping(context.get("initiative_decision")) or _safe_mapping(context.get("initiative_governor_state"))
    default_mode = _safe_mapping(context.get("default_mode_state"))
    mirror = {**_safe_mapping(context.get("mirror_neuron_resonance")), **_safe_mapping(context.get("mirror_neuron_state"))}
    memory_layers = {**_safe_mapping(context.get("memory_layers")), **_safe_mapping(context.get("hippocampus_state"))}
    language_state = _safe_mapping(context.get("language_system_state"))
    language = {
        **_safe_mapping(language_state.get("last_language_style")),
        **_safe_mapping(context.get("language_style")),
        **_safe_mapping(context.get("language_dual_system")),
    }
    motive = _safe_mapping(context.get("motive_state"))
    motor = _safe_mapping(context.get("motor_cortex_state"))
    cerebellum = _safe_mapping(context.get("cerebellum_state"))
    vestibular = _safe_mapping(context.get("vestibular_state"))
    peripheral = _safe_mapping(context.get("peripheral_state"))

    stress = _max_float(
        vital.get("stress_index"),
        endocrine.get("stress_index"),
        endocrine.get("cortisol_level"),
        endocrine.get("cortisol"),
        default=0.0,
    )
    cognitive_load = _max_float(
        vital.get("cognitive_load_index"),
        proprioception.get("cognitive_load_index"),
        interoception.get("cognitive_load_index"),
        default=0.0,
    )
    sleep_pressure = _max_float(
        vital.get("sleep_pressure"),
        circadian.get("sleep_pressure"),
        default=0.0,
    )
    fatigue = _max_float(
        vital.get("somatic_fatigue_level"),
        somatic.get("fatigue"),
        somatic.get("fatigue_level"),
        interoception.get("fatigue"),
        default=0.0,
    )
    loneliness = _max_float(
        vital.get("loneliness_level"),
        context.get("loneliness_level"),
        hedonic.get("loneliness_level"),
        default=0.0,
    )
    mirror_resonance = _max_float(
        vital.get("mirror_resonance_strength"),
        mirror.get("resonance_strength"),
        mirror.get("mirror_resonance_strength"),
        default=0.0,
    )
    pain_level = _max_float(vital.get("pain_level"), hedonic.get("pain_level"), default=0.0)
    reward_level = _max_float(vital.get("reward_level"), hedonic.get("reward_level"), default=0.0)
    silence_willingness = _coerce_float(
        vital.get("silence_willingness", context.get("silence_willingness", 1.0)),
        default=1.0,
    )
    ans_recovery_mode = _coerce_bool(ans.get("recovery_mode", vital.get("ans_recovery_mode", False)))
    ans_proactive_multiplier = _coerce_float(
        ans.get("proactive_multiplier", vital.get("ans_proactive_multiplier", 1.0)),
        default=1.0,
    )
    recovery_pressure = _max_float(
        glymphatic.get("recovery_pressure"),
        glymphatic.get("recovery_urgency"),
        glymphatic.get("cleanup_pressure"),
        default=0.0,
    )
    cerebellum_jitter = _max_float(
        cerebellum.get("jitter_level"),
        cerebellum.get("correction_load"),
        default=0.0,
    )
    vestibular_jitter = _max_float(
        vestibular.get("jitter_index"),
        vestibular.get("stability_index"),
        default=0.0,
    )
    prefrontal_drift_risk = _max_float(
        prefrontal.get("drift_risk"),
        prefrontal.get("persona_drift_risk"),
        prefrontal.get("goal_drift_risk"),
        default=0.0,
    )
    plan_stability = _coerce_float(prefrontal.get("plan_stability"), default=1.0)
    route_confidence = _coerce_float(tribe_route.get("confidence", thalamus.get("route_confidence")), default=0.0)
    route_bias = _first_nonempty(
        vestibular.get("route_bias"),
        tribe_route.get("vestibular_bias"),
        thalamus.get("route_bias"),
    ).lower()
    memory_active = bool(
        memory_layers.get("recent_summary_present")
        or memory_layers.get("episodic_available")
        or memory_layers.get("semantic_available")
        or memory_layers.get("autobiographical_available")
        or _coerce_float(memory_layers.get("memory_count"), default=0.0) > 0
        or _coerce_float(memory_layers.get("recent_memory_count"), default=0.0) > 0
    )

    risk_level = _highest_risk(
        vital.get("immune_risk_level"),
        immune.get("risk_level"),
        context.get("immune_risk_level"),
    )
    initiative_blocked = _looks_blocked(initiative) or _coerce_float(vital.get("initiative_block_rate"), default=0.0) >= 0.45
    route_should_throttle = route_bias in {"conservative", "throttle", "damped", "defer"}
    high_load = (
        cognitive_load >= 0.72
        or sleep_pressure >= 0.75
        or fatigue >= 0.70
        or recovery_pressure >= 0.68
        or cerebellum_jitter >= 0.68
        or vestibular_jitter >= 0.78
        or ans_recovery_mode
        or ans_proactive_multiplier < 0.55
    )

    task = _clean_mapping(
        {
            "information_budget": "very_short" if high_load else "short" if risk_level in {"medium", "high", "critical"} else "",
            "question_budget": 0 if initiative_blocked or high_load else None,
            "style_anchor": _clean_list(
                [
                    "路由面：按当前入口完成本轮，不临时换任务。" if cortex or thalamus or tribe_route else "",
                    "规划面：先稳住当前目标，避免话题漂移。" if prefrontal_drift_risk >= 0.25 or plan_stability < 0.65 else "",
                    "记忆面：有可用记忆线索时只抽取事实，不复述来源。" if memory_active else "",
                    "稳定性面：当前减少通道切换，保持单线程。" if route_should_throttle else "",
                    "当前适合短一点，先回应最核心的一句。" if high_load else "",
                    "先保守完成本轮目标，别扩写。" if risk_level in {"high", "critical"} else "",
                ],
                limit=5,
            ),
        }
    )
    affect = _clean_signal_mapping(
        {
            "emotional_tone": _coarse_emotional_tone(emotion, vital, stress=stress, pain_level=pain_level),
            "warmth_bias": _clamp(max(reward_level * 0.48, loneliness * 0.36, mirror_resonance * 0.34)),
            "withdraw_bias": _clamp(max((1.0 - silence_willingness) * 0.55, fatigue * 0.46, sleep_pressure * 0.42, cognitive_load * 0.28)),
            "irritation_bias": _clamp(max(stress * 0.38, pain_level * 0.32)),
            "style_anchor": _clean_list(
                [
                    "节律面：恢复态时更稳更短，别强行活跃。" if ans_recovery_mode or recovery_pressure >= 0.55 else "",
                    "稳定面：当前降低跳变感，句子完整一点。" if cerebellum_jitter >= 0.55 or vestibular_jitter >= 0.65 else "",
                    "情绪面：可以更柔一点，但不要解释内部情绪。" if loneliness >= 0.55 or mirror_resonance >= 0.55 else "",
                    "情绪面：低能量时句子更稳，不要故意造碎句。" if high_load else "",
                ],
                limit=4,
            ),
        }
    )
    if ans_recovery_mode or recovery_pressure >= 0.55 or cerebellum_jitter >= 0.55 or vestibular_jitter >= 0.65:
        affect["withdraw_bias"] = _clamp(
            max(
                _coerce_float(affect.get("withdraw_bias"), default=0.0),
                0.38 if ans_recovery_mode else 0.0,
                recovery_pressure * 0.50,
                cerebellum_jitter * 0.36,
                vestibular_jitter * 0.42,
            )
        )
    relationship = _relationship_overlay(
        bond=bond,
        bond_vector=bond_vector,
        vital=vital,
        social=_safe_mapping(context.get("social_brain_state")),
    )
    drive = _clean_signal_mapping(
        {
            "withdraw_drive": _clamp(
                max(
                    (1.0 - silence_willingness) * 0.50,
                    fatigue * 0.38,
                    sleep_pressure * 0.34,
                    _coerce_float(motive.get("withdraw_drive") or motive.get("withdraw"), default=0.0),
                )
            ),
            "continuity_drive": _clamp(
                max(
                    0.42 if memory_active else 0.0,
                    _coerce_float(default_mode.get("continuity_score"), default=0.0),
                    _coerce_float(default_mode.get("continuity_drive"), default=0.0),
                    _coerce_float(default_mode.get("self_continuity_score"), default=0.0),
                    _coerce_float(_safe_mapping(default_mode.get("inner_monologue")).get("continuity_drive"), default=0.0),
                    _coerce_float(_safe_mapping(default_mode.get("self_directive")).get("self_continuity_score"), default=0.0),
                    _coerce_float(vital.get("continuity_hint"), default=0.0),
                    _coerce_float(context.get("continuity_drive"), default=0.0),
                    _coerce_float(motive.get("continuity_drive") or motive.get("continuity"), default=0.0),
                )
            ),
            "repair_drive": _clamp(
                max(
                    _coerce_float(vital.get("bond_repair_needed"), default=0.0),
                    _coerce_float(relationship.get("conflict_rift"), default=0.0),
                    _coerce_float(default_mode.get("repair_drive"), default=0.0),
                    _coerce_float(_safe_mapping(default_mode.get("inner_monologue")).get("repair_drive"), default=0.0),
                )
            ),
            "care_drive": _clamp(
                max(
                    loneliness * 0.58,
                    mirror_resonance * 0.42,
                    _coerce_float(default_mode.get("care_drive"), default=0.0),
                    _coerce_float(_safe_mapping(default_mode.get("inner_monologue")).get("care_drive"), default=0.0),
                    _coerce_float(motive.get("care_drive") or motive.get("care"), default=0.0),
                )
            ),
            "playfulness_drive": _clamp(
                max(
                    _coerce_float(relationship.get("playfulness"), default=0.0) * 0.52,
                    reward_level * 0.22,
                    _coerce_float(motive.get("playfulness_drive") or motive.get("playfulness"), default=0.0),
                )
            ),
        }
    )
    continuity_style_anchor = _clean_list(
        [
            "连续性面：有近期记忆线索，续上意图而不是复述来源。" if memory_active else "",
            "连续性面：当前只续上必要信息，不主动开新分支。" if initiative_blocked or high_load else "",
            "恢复面：当前减少扩展，给自我节律留恢复空间。" if recovery_pressure >= 0.55 or ans_recovery_mode else "",
            "稳定性面：路由不稳时保持单线程，不跳话题。" if route_should_throttle or vestibular_jitter >= 0.65 else "",
            *_coerce_text_sequence(default_mode.get("style_anchor")),
            *_coerce_text_sequence(_safe_mapping(default_mode.get("inner_monologue")).get("style_anchor")),
            *_coerce_text_sequence(_safe_mapping(default_mode.get("self_directive")).get("style_anchor")),
        ],
        limit=6,
    )
    continuity = _clean_mapping(
        {
            "should_be_brief": True if high_load or initiative_blocked or route_should_throttle else None,
            "allow_followup": False
            if (
                initiative_blocked
                or route_should_throttle
                or cognitive_load >= 0.86
                or sleep_pressure >= 0.86
                or recovery_pressure >= 0.82
            )
            else None,
            "style_anchor": continuity_style_anchor,
        }
    )
    safety = tuple(
        _clean_list(
            [
                "不要偏离当前用户意图或临时扩写新目标。"
                if prefrontal_drift_risk >= 0.25 or plan_stability < 0.65
                else "",
                "不要复述记忆层、来源、摘要或检索细节。" if memory_active else "",
                "不要声称已经执行外部动作、表情、文件或设备操作。"
                if motor or peripheral or cerebellum
                else "",
                "高风险场景先保守，不承诺现实结果。" if risk_level in {"high", "critical"} else "",
                "不要为了角色感造不通顺短句。" if language or high_load else "",
                "不要输出内心独白、思维流、私有想法或它的来源。"
                if default_mode
                else "",
                *_coerce_text_sequence(default_mode.get("must_not_do")),
                *_coerce_text_sequence(_safe_mapping(default_mode.get("self_directive")).get("must_not_do")),
            ],
            limit=10,
        )
    )
    delivery = _clean_mapping(
        {
            "response_form": _normalize_response_form(
                _first_nonempty(
                    language.get("response_form"),
                    language.get("response_mode"),
                    peripheral.get("response_form"),
                )
            ),
            "output_channel": _first_nonempty(
                peripheral.get("output_channel"),
                peripheral.get("channel"),
                language.get("output_channel"),
            ),
            "vts_expression_id": _first_nonempty(
                language.get("vts_expression_id"),
                _safe_mapping(language.get("last_expression")).get("vts_expression_id"),
                context.get("vts_expression_id"),
            ),
            "style_anchor": _clean_list(
                [
                    "输出面：降低抖动感，保持句子完整。" if cerebellum_jitter >= 0.55 or vestibular_jitter >= 0.65 else "",
                    "输出面：文字优先通顺；语音化停顿不能牺牲语法。" if language or high_load else "",
                    "输出面：外设状态只影响输出形态，不写成现实动作。" if peripheral or motor else "",
                ],
                limit=4,
            ),
        }
    )

    mapped_organs = _mapped_organs(context)
    trace = {
        "mapped_organs": mapped_organs,
        "mapped_organ_count": len(mapped_organs),
        "dropped_raw_field_count": _count_forbidden_raw_fields(context),
        "slots": sorted(_slots_from_overlay(task, affect, relationship, drive, continuity, safety, delivery)),
        "high_load": bool(high_load),
        "risk_level": risk_level or "none",
        "inner_monologue_active": bool(
            default_mode.get("thought_stream_active")
            or _coerce_float(default_mode.get("private_thought_count"), default=0.0) > 0
            or _safe_mapping(default_mode.get("inner_monologue")).get("thought_stream_active")
        ),
        "self_model_active": bool(
            default_mode.get("self_identity")
            or default_mode.get("agency_score")
            or _safe_mapping(default_mode.get("self_directive")).get("self_identity")
        ),
        "route_confidence": round(float(route_confidence or 0.0), 4),
        "memory_active": bool(memory_active),
        "recovery_pressure": round(float(recovery_pressure or 0.0), 4),
    }
    return SurfaceOrganInfluenceOverlay(
        task=task,
        affect=affect,
        relationship=relationship,
        drive=drive,
        continuity=continuity,
        safety=safety,
        delivery=delivery,
        trace=trace,
    )


def _relationship_overlay(
    *,
    bond: Mapping[str, Any],
    bond_vector: Mapping[str, Any],
    vital: Mapping[str, Any],
    social: Mapping[str, Any],
) -> dict[str, Any]:
    stage = _first_nonempty(social.get("bond_stage"), bond.get("stage"), vital.get("bond_stage"))
    intimacy = _max_float(
        bond_vector.get("intimacy"),
        bond_vector.get("bond_vector_intimacy"),
        social.get("bond_vector_intimacy"),
        bond.get("score"),
        default=0.0,
    )
    trust = _max_float(
        bond_vector.get("trust"),
        bond_vector.get("bond_vector_trust"),
        social.get("bond_vector_trust"),
        default=0.0,
    )
    playfulness = _max_float(
        social.get("relationship_playfulness"),
        social.get("playfulness"),
        bond_vector.get("playfulness"),
        default=0.0,
    )
    conflict = _max_float(
        social.get("relationship_conflict_rift"),
        social.get("conflict_rift"),
        bond.get("rift_level"),
        default=0.0,
    )
    phase = _relationship_phase(stage)
    return _clean_signal_mapping(
        {
            "relationship_distance": _relationship_distance(stage=stage, intimacy=intimacy),
            "relationship_phase": phase,
            "relationship_substage": _first_nonempty(social.get("relationship_substage"), bond.get("relationship_substage")),
            "stage_label": _first_nonempty(social.get("stage_label"), bond.get("stage_label")),
            "playfulness": _clamp(playfulness),
            "conflict_rift": _clamp(conflict),
            "attachment_depth": _clamp(max(intimacy, trust) * 0.78),
        }
    )


def _mapped_organs(context: Mapping[str, Any]) -> list[str]:
    mapped: list[str] = []
    key_to_organs = {
        "vital_signs": ("ans", "limbic", "endocrine", "immune", "pain_reward", "proprioception"),
        "ans_state": ("ans",),
        "emotion_context": ("limbic",),
        "voice_emotion": ("limbic",),
        "bond_context": ("social_brain",),
        "bond_vector": ("social_brain",),
        "social_brain_state": ("social_brain",),
        "endocrine_gate": ("endocrine",),
        "immune_state": ("immune",),
        "hedonic_state": ("pain_reward",),
        "pain_reward_state": ("pain_reward",),
        "somatic_state": ("somatic",),
        "interoception_state": ("interoception",),
        "circadian_state": ("circadian",),
        "glymphatic_state": ("glymphatic",),
        "proprioception_state": ("proprioception",),
        "initiative_decision": ("initiative_governor",),
        "initiative_governor_state": ("initiative_governor",),
        "default_mode_state": ("default_mode",),
        "mirror_neuron_state": ("mirror_neuron",),
        "mirror_neuron_resonance": ("mirror_neuron",),
        "memory_layers": ("hippocampus",),
        "hippocampus_state": ("hippocampus",),
        "language_dual_system": ("language_system",),
        "language_style": ("language_system",),
        "language_system_state": ("language_system",),
        "motor_cortex_state": ("motor_cortex",),
        "cerebellum_state": ("cerebellum",),
        "vestibular_state": ("vestibular",),
        "peripheral_state": ("peripheral",),
        "prefrontal_state": ("prefrontal",),
        "cortex_state": ("cortex",),
        "thalamus_state": ("thalamus",),
        "tribe_route": ("cortex", "thalamus"),
    }
    for key, organs in key_to_organs.items():
        value = context.get(key)
        if not value:
            continue
        for organ in organs:
            if organ in _RULE_BY_ORGAN and organ not in mapped:
                mapped.append(organ)
    return sorted(mapped, key=lambda organ: _RULE_BY_ORGAN[organ].priority, reverse=True)


def _voice_emotion_from_vitals(vital: Mapping[str, Any]) -> dict[str, Any]:
    tag = _first_nonempty(vital.get("voice_emotion_tag"), vital.get("emotion_tag"))
    if not tag:
        return {}
    return {
        "emotion_tag": tag,
        "emotion_intensity": vital.get("voice_emotion_intensity") or vital.get("emotion_intensity"),
    }


def _coarse_emotional_tone(
    emotion: Mapping[str, Any],
    vital: Mapping[str, Any],
    *,
    stress: float,
    pain_level: float,
) -> str:
    tag = _first_nonempty(emotion.get("emotion_tag"), vital.get("emotion_tag"), vital.get("voice_emotion_tag")).upper()
    intensity = _max_float(emotion.get("emotion_intensity"), vital.get("emotion_intensity"), default=0.0)
    if stress >= 0.68 or pain_level >= 0.62 or tag in {"ANGRY", "ANGER", "HURT"}:
        return "soft_containment"
    if tag in _NEGATIVE_EMOTION_TAGS and intensity >= 0.45:
        return "low_heat_care"
    if tag in _WARM_EMOTION_TAGS:
        return "warm_playful"
    return ""


def _relationship_phase(stage: Any) -> str:
    normalized = str(stage or "").strip().upper()
    if normalized in {"CONFIDANT", "INTIMATE"}:
        return "lover"
    if normalized == "SOULMATE":
        return "soulmate"
    return "stepsibling" if normalized else ""


def _relationship_distance(*, stage: Any, intimacy: float) -> str:
    normalized = str(stage or "").strip().upper()
    if normalized in {"CONFIDANT", "INTIMATE", "SOULMATE"} or intimacy >= 0.78:
        return "close"
    if normalized in {"CLOSE", "CLOSE_FRIEND"} or intimacy >= 0.50:
        return "familiar"
    if normalized == "FRIEND":
        return "familiar"
    return ""


def _looks_blocked(payload: Mapping[str, Any]) -> bool:
    decision = _first_nonempty(payload.get("decision"), payload.get("action"), payload.get("status")).lower()
    return decision in {"block", "blocked", "defer", "deferred", "suppress", "suppressed"}


def _highest_risk(*values: Any) -> str:
    ranks = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    best = "none"
    for value in values:
        text = str(value or "").strip().lower()
        if ranks.get(text, 0) > ranks[best]:
            best = text
    return best


def _slots_from_overlay(*parts: Any) -> set[str]:
    slots: set[str] = set()
    names = ("task", "affect", "relationship", "drive", "continuity", "safety", "delivery")
    for name, part in zip(names, parts, strict=False):
        if isinstance(part, Mapping) and any(value not in (None, "", [], {}, ()) for value in part.values()):
            slots.add(name)
        elif isinstance(part, Sequence) and not isinstance(part, (str, bytes, bytearray)) and part:
            slots.add(name)
    return slots


def _count_forbidden_raw_fields(value: Any) -> int:
    if isinstance(value, Mapping):
        count = 0
        for key, child in value.items():
            count += 1 if str(key or "").strip().lower() in _TEXT_ADVICE_KEYS else 0
            count += _count_forbidden_raw_fields(child)
        return count
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_count_forbidden_raw_fields(item) for item in value)
    return 0


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _clean_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in dict(raw).items():
        if value in (None, "", [], {}, ()):
            continue
        cleaned[str(key)] = value
    return cleaned


def _clean_signal_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in dict(raw).items():
        if value in (None, "", [], {}, (), False):
            continue
        if _looks_numeric(value) and float(value) == 0.0:
            continue
        cleaned[str(key)] = value
    return cleaned


def _clean_text(value: Any, *, max_chars: int = 160) -> str:
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


def _coerce_text_sequence(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return _clean_list(list(value), limit=None)
    return []


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "active", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "inactive", "disabled"}:
        return False
    return bool(value)


def _max_float(*values: Any, default: float = 0.0) -> float:
    result = float(default)
    for value in values:
        if value in (None, ""):
            continue
        result = max(result, _coerce_float(value, default=default))
    return _clamp(result)


def _clamp(value: float, *, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _normalize_response_form(value: Any) -> str:
    text = _clean_text(value).lower()
    if text in {"", "chat_text", "text", "dm_text", "direct_answer"}:
        return "text" if text else ""
    if text in {"voice", "tts", "speech"}:
        return "voice"
    return text


__all__ = [
    "ORGAN_SURFACE_RULES",
    "SURFACE_ORGAN_IDS",
    "SurfaceOrganInfluenceOverlay",
    "SurfaceOrganRule",
    "build_surface_organ_influence_overlay",
    "organ_surface_rule_table",
]
