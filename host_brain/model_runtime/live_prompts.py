"""已编译实时消息的提示注册边界。

领域提示编译器仍负责组装各自的类型化消息包；本模块在它们进入模型尝试边界前
赋予固定的注册表身份、版本与输出契约。这样运行时消息不能绕过审计、缓存和
结构化输出校验，也无需让每个领域重复实现同一套接入规则。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from host_brain.model_runtime.contracts import OutputContract
from host_brain.model_runtime.prompt_catalog import build_production_prompt_registry
from host_brain.model_runtime.prompt_registry import PromptRegistry, PromptSchemaError


SURFACE_MAIN_TEXT_PROMPT_ID = "surface.main_chain.live.text"
SURFACE_MAIN_JSON_PROMPT_ID = "surface.main_chain.live.json"
PERSONA_INNER_ACTIVITY_PROMPT_ID = "persona.inner_activity.json"
AGENT_REACT_STEP_PROMPT_ID = "agent.react.step.json"
AGENT_SUBAGENT_GATEWAY_PROMPT_ID = "agent.subagent.gateway.text"
RESOURCE_MANAGER_ANALYSIS_PROMPT_ID = "resource.manager.analysis.json"
MEMORY_DISTILLATION_PROMPT_ID = "memory.distillation.json"
MULTIMODAL_OBSERVER_PROMPT_ID = "multimodal.observer.json"
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
BOND_NARRATIVE_REWRITE_PROMPT_ID = "bond.narrative.rewrite.json"
SCHEDULER_NARRATIVE_PROMPT_ID = "scheduler.narrative.text"
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
EMOTION_REACTIVITY_PROMPT_ID = "persona.emotion_reactivity.json"
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
CODE_BENEFIT_EXECUTION_PROMPT_ID = "code.benefit.execution.json"
CODE_BENEFIT_GENERATION_PARAMETERS = MappingProxyType(
    {
        "temperature": 0.0,
        "top_p": 0.8,
        "max_tokens": 600,
        "thinking_mode": "disabled",
    }
)


def _compiled_messages(values: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """验证领域编译消息并复制为不可变序列，防止后续调用共享可变输入。"""
    # 领域编译器只能交付消息序列；这里复制每条 Mapping 并拒绝空结果，保证其内容随后
    # 仍由 PromptRegistry 绑定固定 ID、版本、输出契约和缓存策略。
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
    """将实时编译消息注册为带输出类型的稳定 Prompt 身份。

    ``registry`` 由调用者传入并原地扩展，以便静态目录与动态消息共用同一个
    版本、缓存和契约治理平面。
    """

    # 所有 live binding 共享同一来源标识和只允许 ``messages`` 的输入面：领域模块负责
    # 编译语义包，但无权在每次调用时改变注册表身份或模型输出类型。
    shared = {
        "version": "v1.0.0",
        "required_variables": ("messages",),
        "metadata": {
            "owner": "model_runtime",
            "source": "compiled_domain_prompt",
            "source_path": "host_brain/model_runtime/live_prompts.py",
        },
    }
    # Surface 文本与 JSON 即使使用同一个编译器，也登记为不同 prompt identity；这让
    # 路由、缓存与输出验证不会把自然语言回复和结构化控制包混为一类。
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
    # 批量绑定将跨域运行主链统一接入同一 renderer，但每个 ID 的输出种类仍是明确、
    # 可审计的契约；新增 live prompt 必须在此处声明，不能临时绕过 registry。
    for prompt_id, output_kind in (
        (RESOURCE_MANAGER_ANALYSIS_PROMPT_ID, "json_object"),
        (MEMORY_DISTILLATION_PROMPT_ID, "json_object"),
        (MULTIMODAL_OBSERVER_PROMPT_ID, "json_object"),
        *((prompt_id, "json_object") for prompt_id in GRAPH_NODE_PROMPT_IDS.values()),
        *((prompt_id, "json_object") for prompt_id in TRIBE_PROMPT_IDS.values()),
        (BOND_NARRATIVE_REWRITE_PROMPT_ID, "json_object"),
        (SCHEDULER_NARRATIVE_PROMPT_ID, "text"),
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
        (EMOTION_REACTIVITY_PROMPT_ID, "json_object"),
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
        (CODE_BENEFIT_EXECUTION_PROMPT_ID, "json_object"),
    ):
        registry.register_renderer(
            prompt_id,
            _compiled_messages,
            output_contract=OutputContract(kind=output_kind),
            **shared,
        )
    return registry


def build_live_production_prompt_registry() -> PromptRegistry:
    """构建生产提示目录，并在其上附加实时编译消息的受治理入口。"""

    # 先加载代码侧生产规范，再附加运行时编译消息入口；该顺序确保 live binding 复用
    # 同一严格 registry，而非创建脱离静态 PromptSpec 的第二套模型平面。
    return register_live_prompt_bindings(build_production_prompt_registry())


__all__ = [
    "AGENT_REACT_STEP_PROMPT_ID",
    "AGENT_SUBAGENT_GATEWAY_PROMPT_ID",
    "BOND_NARRATIVE_REWRITE_PROMPT_ID",
    "EMERGENT_GROWTH_PATTERN_PROMPT_ID",
    "EVOLUTION_DESIRE_PROMPT_ID",
    "GRAPH_NODE_PROMPT_IDS",
    "TRIBE_PROMPT_IDS",
    "MEMORY_DISTILLATION_PROMPT_ID",
    "MULTIMODAL_OBSERVER_PROMPT_ID",
    "INITIATIVE_TOPIC_PROMPT_ID",
    "MIRROR_EMPATHY_PROMPT_ID",
    "PREFRONTAL_PLAN_PROMPT_ID",
    "PREFRONTAL_SELF_REFLECTION_PROMPT_ID",
    "RESOURCE_MANAGER_ANALYSIS_PROMPT_ID",
    "REPAIR_AGENT_PLAN_PROMPT_ID",
    "SCHEDULER_NARRATIVE_PROMPT_ID",
    "SELF_REFLECTION_AWARENESS_PROMPT_ID",
    "SELF_REFLECTION_GROWTH_NOTE_PROMPT_ID",
    "SELF_REFLECTION_MOOD_PROMPT_ID",
    "SELF_REFLECTION_STYLE_TREND_PROMPT_ID",
    "STARTUP_MODEL_WARMUP_PROMPT_ID",
    "RESEARCH_EXECUTION_PROMPT_ID",
    "RESEARCH_QUALITY_VERIFIER_PROMPT_ID",
    "CODE_BENEFIT_EXECUTION_PROMPT_ID",
    "CODE_BENEFIT_GENERATION_PARAMETERS",
    "EMOTION_REACTIVITY_PROMPT_ID",
    "SURFACE_MAIN_JSON_PROMPT_ID",
    "SURFACE_MAIN_TEXT_PROMPT_ID",
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
    "build_live_production_prompt_registry",
    "register_live_prompt_bindings",
]
