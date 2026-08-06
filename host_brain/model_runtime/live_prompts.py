"""Registered prompt bindings for live, already-compiled message lists.

Domain prompt compilers remain responsible for assembling their typed packets.
This module gives those compiled messages a versioned registry identity before
they cross the model-attempt boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.model_runtime.contracts import OutputContract
from host_brain.model_runtime.prompt_catalog import build_production_prompt_registry
from host_brain.model_runtime.prompt_registry import PromptRegistry, PromptSchemaError


SURFACE_MAIN_TEXT_PROMPT_ID = "surface.main_chain.live.text"
SURFACE_MAIN_JSON_PROMPT_ID = "surface.main_chain.live.json"
SURFACE_MEMORY_REPAIR_PROMPT_ID = "surface.memory_guard.repair.json"
VISION_ACTION_PLANNER_PROMPT_ID = "vision.action_planner.json"
PERSONA_INNER_ACTIVITY_PROMPT_ID = "persona.inner_activity.json"
AGENT_REACT_STEP_PROMPT_ID = "agent.react.step.json"
AGENT_SUBAGENT_GATEWAY_PROMPT_ID = "agent.subagent.gateway.text"
RESOURCE_MANAGER_ANALYSIS_PROMPT_ID = "resource.manager.analysis.json"
MEMORY_DISTILLATION_PROMPT_ID = "memory.distillation.json"
EMOTION_INFERENCE_PROMPT_ID = "emotion.inference.json"
MULTIMODAL_OBSERVER_PROMPT_ID = "multimodal.observer.json"
PLAN_SYNTHESIZE_PROMPT_ID = "plan.synthesize.text"
GRAPH_NODE_PROMPT_IDS = {
    "chat_direct_ja": "graph.chat_direct.json",
    "core_reasoner_primary": "graph.core_reasoner.primary.json",
    "core_reasoner_secondary": "graph.core_reasoner.secondary.json",
    "planner_constraints": "graph.planner_constraints.json",
    "tool_planner": "graph.tool_planner.json",
    "safety_verifier": "graph.safety_verifier.json",
    "consistency_verifier": "graph.consistency_verifier.json",
    "renderer_ja_c_style": "graph.renderer.ja_c_style.json",
    "workbench_translate": "graph.workbench.translate.json",
    "workbench_code_assist": "graph.workbench.code_assist.json",
}
TRIBE_PROMPT_IDS = {
    "cerebellum_correction_narrative": "tribe.cerebellum.correction_narrative.json",
    "cerebellum_failure_pattern": "tribe.cerebellum.failure_pattern.json",
    "circadian_mood_hint": "tribe.circadian.mood_hint.json",
    "endocrine_hormonal_hint": "tribe.endocrine.hormonal_hint.json",
    "glymphatic_consolidation_summary": "tribe.glymphatic.consolidation_summary.json",
    "immune_semantic_intent": "tribe.immune.semantic_intent.json",
    "interoception_narrative": "tribe.interoception.narrative.json",
    "language_broca_opening_hint": "tribe.language.broca_opening_hint.json",
    "language_wernicke_deep_parse": "tribe.language.wernicke_deep_parse.json",
    "motor_speak_style": "tribe.motor.speak_style.json",
    "pain_reward_hedonic_reflection": "tribe.pain_reward.hedonic_reflection.json",
    "peripheral_context_analysis": "tribe.peripheral.context_analysis.json",
    "proprioception_load_hint": "tribe.proprioception.load_hint.json",
    "somatic_narrative": "tribe.somatic.narrative.json",
    "vestibular_stability_assessment": "tribe.vestibular.stability_assessment.json",
}
CHAT_VISION_ANALYSIS_PROMPT_ID = "chat.vision.analysis.json"
LIFE_PROACTIVE_COMPOSE_PROMPT_ID = "life.proactive.compose.json"
INNER_WORLD_MONOLOGUE_PROMPT_ID = "inner_world.monologue.json"
BOND_NARRATIVE_REWRITE_PROMPT_ID = "bond.narrative.rewrite.json"
REFLECTION_EVALUATE_PROMPT_ID = "reasoning.reflection.evaluate.json"
SCHEDULER_NARRATIVE_PROMPT_ID = "scheduler.narrative.text"
PROACTIVE_TALK_PROMPT_ID = "proactive.talk.text"
SELF_REFLECTION_AWARENESS_PROMPT_ID = "self_reflection.awareness.text"
SELF_REFLECTION_MOOD_PROMPT_ID = "self_reflection.mood.text"
SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID = "self_reflection.growth_note.text"
SELF_REFLECTION_STYLE_TREND_PROMPT_ID = "self_reflection.style_trend.text"
EVOLUTION_DESIRE_PROMPT_ID = "evolution.desire.text"
EMERGENT_GROWTH_PATTERN_PROMPT_ID = "emergent.growth.pattern.json"
REPAIR_AGENT_PLAN_PROMPT_ID = "repair.agent.plan.json"
PREFRONTAL_SELF_REFLECTION_PROMPT_ID = "prefrontal.self_reflection.text"
PREFRONTAL_PLAN_PROMPT_ID = "prefrontal.plan.json"
MIRROR_EMPATHY_PROMPT_ID = "mirror.empathy_hint.json"
INITIATIVE_TOPIC_PROMPT_ID = "initiative.proactive_topic.json"
HIPPOCAMPUS_RECALL_PROMPT_ID = "hippocampus.recall_association.json"
EMOTION_REACTIVITY_PROMPT_ID = "persona.emotion_reactivity.json"
EMOTIONAL_SILENCE_PROMPT_ID = "persona.emotional_silence.json"
PENTEST_COMPAT_JSON_PROMPT_ID = "pentest.compat.legacy.json"
PENTEST_REACT_CHOOSE_PROMPT_ID = "pentest.react.choose.json"
PENTEST_REACT_PROPOSE_PROMPT_ID = "pentest.react.propose.json"
PENTEST_REACT_ANALYZE_PROMPT_ID = "pentest.react.analyze.json"
PENTEST_REACT_REFLECT_PROMPT_ID = "pentest.react.reflect.json"
PENTEST_FAILURE_CLASSIFY_PROMPT_ID = "pentest.failure.classify.json"
PENTEST_KNOWLEDGE_EXTRACT_PROMPT_ID = "pentest.knowledge.extract.json"
PENTEST_COGNITIVE_HYPOTHESES_PROMPT_ID = "pentest.cognitive.hypotheses.json"
PENTEST_PACK_PLAN_PROMPT_ID = "pentest.pack.plan.json"
STARTUP_MODEL_WARMUP_PROMPT_ID = "startup.model_warmup.text"
RESEARCH_EXECUTION_PROMPT_ID = "research.execution.citation.json"
RESEARCH_QUALITY_VERIFIER_PROMPT_ID = "research.quality.hidden.json"


def _compiled_messages(values: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = values.get("messages")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise PromptSchemaError("compiled live prompt messages must be a sequence")
    messages: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise PromptSchemaError("compiled live prompt message must be a mapping")
        messages.append(dict(item))
    if not messages:
        raise PromptSchemaError("compiled live prompt must contain at least one message")
    return tuple(messages)


def register_live_prompt_bindings(registry: PromptRegistry) -> PromptRegistry:
    """Install the three live prompt identities into ``registry``."""

    shared = {
        "version": "v1.0.0",
        "required_variables": ("messages",),
        "metadata": {
            "owner": "model_runtime",
            "source": "compiled_domain_prompt",
            "source_path": "host_brain/model_runtime/live_prompts.py",
        },
    }
    registry.register_renderer(
        SURFACE_MAIN_TEXT_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="text"),
        **shared,
    )
    registry.register_renderer(
        SURFACE_MAIN_JSON_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    registry.register_renderer(
        SURFACE_MEMORY_REPAIR_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    registry.register_renderer(
        VISION_ACTION_PLANNER_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    registry.register_renderer(
        PERSONA_INNER_ACTIVITY_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    registry.register_renderer(
        AGENT_REACT_STEP_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="json_object"),
        **shared,
    )
    registry.register_renderer(
        AGENT_SUBAGENT_GATEWAY_PROMPT_ID,
        _compiled_messages,
        output_contract=OutputContract(kind="text"),
        **shared,
    )
    for prompt_id, output_kind in (
        (RESOURCE_MANAGER_ANALYSIS_PROMPT_ID, "json_object"),
        (MEMORY_DISTILLATION_PROMPT_ID, "json_object"),
        (EMOTION_INFERENCE_PROMPT_ID, "json_object"),
        (MULTIMODAL_OBSERVER_PROMPT_ID, "json_object"),
        (PLAN_SYNTHESIZE_PROMPT_ID, "text"),
        *((prompt_id, "json_object") for prompt_id in GRAPH_NODE_PROMPT_IDS.values()),
        *((prompt_id, "json_object") for prompt_id in TRIBE_PROMPT_IDS.values()),
        (CHAT_VISION_ANALYSIS_PROMPT_ID, "json_object"),
        (LIFE_PROACTIVE_COMPOSE_PROMPT_ID, "json_object"),
        (INNER_WORLD_MONOLOGUE_PROMPT_ID, "json_object"),
        (BOND_NARRATIVE_REWRITE_PROMPT_ID, "json_object"),
        (REFLECTION_EVALUATE_PROMPT_ID, "json_object"),
        (SCHEDULER_NARRATIVE_PROMPT_ID, "text"),
        (PROACTIVE_TALK_PROMPT_ID, "text"),
        (SELF_REFLECTION_AWARENESS_PROMPT_ID, "text"),
        (SELF_REFLECTION_MOOD_PROMPT_ID, "text"),
        (SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID, "text"),
        (SELF_REFLECTION_STYLE_TREND_PROMPT_ID, "text"),
        (EVOLUTION_DESIRE_PROMPT_ID, "text"),
        (EMERGENT_GROWTH_PATTERN_PROMPT_ID, "json_object"),
        (REPAIR_AGENT_PLAN_PROMPT_ID, "json_object"),
        (PREFRONTAL_SELF_REFLECTION_PROMPT_ID, "text"),
        (PREFRONTAL_PLAN_PROMPT_ID, "json_object"),
        (MIRROR_EMPATHY_PROMPT_ID, "json_object"),
        (INITIATIVE_TOPIC_PROMPT_ID, "json_object"),
        (HIPPOCAMPUS_RECALL_PROMPT_ID, "json_object"),
        (EMOTION_REACTIVITY_PROMPT_ID, "json_object"),
        (EMOTIONAL_SILENCE_PROMPT_ID, "json_object"),
        (PENTEST_COMPAT_JSON_PROMPT_ID, "json_object"),
        (PENTEST_REACT_CHOOSE_PROMPT_ID, "json_object"),
        (PENTEST_REACT_PROPOSE_PROMPT_ID, "json_object"),
        (PENTEST_REACT_ANALYZE_PROMPT_ID, "json_object"),
        (PENTEST_REACT_REFLECT_PROMPT_ID, "json_object"),
        (PENTEST_FAILURE_CLASSIFY_PROMPT_ID, "json_object"),
        (PENTEST_KNOWLEDGE_EXTRACT_PROMPT_ID, "json_object"),
        (PENTEST_COGNITIVE_HYPOTHESES_PROMPT_ID, "json_object"),
        (PENTEST_PACK_PLAN_PROMPT_ID, "json_object"),
        (STARTUP_MODEL_WARMUP_PROMPT_ID, "text"),
        (RESEARCH_EXECUTION_PROMPT_ID, "json_object"),
        (RESEARCH_QUALITY_VERIFIER_PROMPT_ID, "json_object"),
    ):
        registry.register_renderer(
            prompt_id,
            _compiled_messages,
            output_contract=OutputContract(kind=output_kind),
            **shared,
        )
    return registry


def build_live_production_prompt_registry() -> PromptRegistry:
    """Build the code catalog plus the live compiled-message bindings."""

    return register_live_prompt_bindings(build_production_prompt_registry())


__all__ = [
    "AGENT_REACT_STEP_PROMPT_ID",
    "AGENT_SUBAGENT_GATEWAY_PROMPT_ID",
    "CHAT_VISION_ANALYSIS_PROMPT_ID",
    "BOND_NARRATIVE_REWRITE_PROMPT_ID",
    "EMOTION_INFERENCE_PROMPT_ID",
    "EMERGENT_GROWTH_PATTERN_PROMPT_ID",
    "EMOTIONAL_SILENCE_PROMPT_ID",
    "EVOLUTION_DESIRE_PROMPT_ID",
    "GRAPH_NODE_PROMPT_IDS",
    "TRIBE_PROMPT_IDS",
    "HIPPOCAMPUS_RECALL_PROMPT_ID",
    "INNER_WORLD_MONOLOGUE_PROMPT_ID",
    "LIFE_PROACTIVE_COMPOSE_PROMPT_ID",
    "MEMORY_DISTILLATION_PROMPT_ID",
    "MULTIMODAL_OBSERVER_PROMPT_ID",
    "INITIATIVE_TOPIC_PROMPT_ID",
    "MIRROR_EMPATHY_PROMPT_ID",
    "PLAN_SYNTHESIZE_PROMPT_ID",
    "PROACTIVE_TALK_PROMPT_ID",
    "PREFRONTAL_PLAN_PROMPT_ID",
    "PREFRONTAL_SELF_REFLECTION_PROMPT_ID",
    "RESOURCE_MANAGER_ANALYSIS_PROMPT_ID",
    "REFLECTION_EVALUATE_PROMPT_ID",
    "REPAIR_AGENT_PLAN_PROMPT_ID",
    "SCHEDULER_NARRATIVE_PROMPT_ID",
    "SELF_REFLECTION_AWARENESS_PROMPT_ID",
    "SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID",
    "SELF_REFLECTION_MOOD_PROMPT_ID",
    "SELF_REFLECTION_STYLE_TREND_PROMPT_ID",
    "STARTUP_MODEL_WARMUP_PROMPT_ID",
    "RESEARCH_EXECUTION_PROMPT_ID",
    "RESEARCH_QUALITY_VERIFIER_PROMPT_ID",
    "EMOTION_REACTIVITY_PROMPT_ID",
    "SURFACE_MAIN_JSON_PROMPT_ID",
    "SURFACE_MAIN_TEXT_PROMPT_ID",
    "SURFACE_MEMORY_REPAIR_PROMPT_ID",
    "PERSONA_INNER_ACTIVITY_PROMPT_ID",
    "PENTEST_COGNITIVE_HYPOTHESES_PROMPT_ID",
    "PENTEST_COMPAT_JSON_PROMPT_ID",
    "PENTEST_FAILURE_CLASSIFY_PROMPT_ID",
    "PENTEST_KNOWLEDGE_EXTRACT_PROMPT_ID",
    "PENTEST_PACK_PLAN_PROMPT_ID",
    "PENTEST_REACT_ANALYZE_PROMPT_ID",
    "PENTEST_REACT_CHOOSE_PROMPT_ID",
    "PENTEST_REACT_PROPOSE_PROMPT_ID",
    "PENTEST_REACT_REFLECT_PROMPT_ID",
    "VISION_ACTION_PLANNER_PROMPT_ID",
    "build_live_production_prompt_registry",
    "register_live_prompt_bindings",
]
