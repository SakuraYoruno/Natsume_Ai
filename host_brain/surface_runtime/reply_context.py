"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence
from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    SurfaceMainChainResult,
    compile_surface_main_chain_input,
)
from host_brain.persona.response_form_contract import normalize_response_form
import re
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
from host_brain.digital_life.relationship_guidance import (
    build_relationship_reply_guidance,
)

_MEMORY_RECALL_SURFACE_REASONS = {
    "anaphoric_selection_recall",
    "collection_memory_recall",
    "event_memory_recall",
    "failure_debug_recall",
    "evidence_review_recall",
    "explicit_memory_recall",
    "correction_recall",
}
_TRACE_RUNTIME_PAYLOAD_KEYS = (
    "conversation_history",
    "conversation_interpretation",
    "interaction_thread",
    "interaction_state",
    "topic_ledger",
    "tactic_ledger",
    "affect_state",
    "relationship_state",
    "drive_state",
    "reply_plan",
    "surface_affect_packet",
    "organ_influence_packet",
    "surface_influence_packet",
    "surface_organ_context_meta",
    "surface_organ_influence",
    "surface_memory_recall_packet",
)
from host_brain.surface_runtime.common import (
    _clean_list,
    _coerce_int,
    _coerce_sequence,
    _first_nonempty,
    _safe_mapping,
    _set_agent_input_mapping,
)
from host_brain.surface_runtime.fact_compiler import (
    _surface_dialogue_repeat_must_not_do,
    _surface_reply_plan_repeat_count,
    _surface_reply_plan_repeat_group,
    _task_facts,
)
from host_brain.surface_runtime.policies import (
    morning_greeting_mismatch_candidate as _surface_morning_greeting_mismatch_candidate,
)
from host_brain.surface_runtime.life_context import (
    _surface_life_context_must_not_do,
    _surface_life_context_task_facts,
    _surface_life_director_pack,
    _surface_memory_acceptance_must_include,
    _surface_memory_acceptance_must_not_do,
    _surface_memory_plan_reason,
    _surface_user_preference_fact_lines,
    _surface_user_preference_must_not_do,
    _surface_user_preference_query_owned,
)
from host_brain.surface_runtime.memory_context import (
    _surface_apply_autonomous_self_query_reply_plan_override,
    _surface_apply_memory_context_reply_plan_override,
    _surface_memory_context_orchestrator_must_include,
)
from host_brain.surface_runtime.memory_grounding import (
    _surface_memory_card_fact_lines,
    _surface_memory_missing_detail_selection,
    _surface_memory_packet_for_output_guard,
    _surface_memory_readiness,
)
from host_brain.surface_runtime.quality_guards import (
    _surface_dream_context_must_not_do,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_pragmatic_guidance_action,
    _surface_pragmatic_guidance_must_include,
    _surface_pragmatic_guidance_must_not_do,
    _surface_pragmatic_intent_fact_lines,
    _surface_sub_agent_result_fact_lines,
    _surface_sub_agent_result_must_include,
    _surface_sub_agent_result_must_not_do,
)
def _surface_emotion_context(agent_input: Any, *, result: SurfaceMainChainResult | None = None) -> dict[str, Any]:
    emotion = _safe_mapping(getattr(agent_input, "emotion_context", None)) or _safe_mapping(
        getattr(agent_input, "emotion_snapshot", None)
    )
    if "emotion_tag" not in emotion:
        emotion["emotion_tag"] = "NEUTRAL"
    if result is not None:
        natsume_tag = str(result.natsume_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        user_tag = str(result.user_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        emotion["emotion_tag"] = natsume_tag
        emotion["emotion_source"] = "surface_generation_model"
        emotion["emotion_inference_source"] = "surface_generation_model"
        emotion["emotion_inference_confidence"] = float(result.runtime.emotion_confidence or 0.0)
        emotion["surface_user_emotion_tag"] = user_tag
        emotion["surface_natsume_emotion_tag"] = natsume_tag
        emotion["surface_vts_expression_id"] = str(result.vts_expression_id or "")
    return emotion


def _response_form(agent_input: Any) -> str:
    render_policy = _reply_plan(agent_input)
    runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
    return normalize_response_form(
        str(
            getattr(agent_input, "response_form", "")
            or runtime.get("response_form")
            or render_policy.get("response_form")
            or "text"
        )
    )


def _resolve_surface_reply_plan(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    direct = _reply_plan(agent_input, trace_stage_meta)
    if direct:
        return direct
    payload = _safe_mapping(runtime_payload)
    if not payload:
        payload = _build_surface_runtime_payload(
            agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        )
    reply_plan = _safe_mapping(payload.get("reply_plan"))
    if reply_plan:
        _set_agent_input_mapping(agent_input, "render_policy", reply_plan)
        return reply_plan
    return {}


def _selected_reply_plan(agent_input: Any, trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = _safe_mapping(trace_stage_meta).get("surface_selected_reply_plan")
    if isinstance(selected, Mapping):
        return dict(selected)
    return _reply_plan(agent_input, trace_stage_meta)


def _reply_plan(agent_input: Any, trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    for raw in (
        getattr(agent_input, "surface_task_control", None),
        trace.get("surface_task_control"),
        trace.get("reply_plan"),
        getattr(agent_input, "reply_plan", None),
        getattr(agent_input, "render_policy", None),
    ):
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
    return {}


def _build_surface_runtime_payload(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
    merge_trace: bool = True,
) -> dict[str, Any]:
    try:
        direct_reply_plan = _reply_plan(agent_input, trace_stage_meta)

        runtime_payload = ports.build_surface_context_packet(
            user_message=str(getattr(agent_input, "user_message", "") or ""),
            render_target_lang=str(getattr(agent_input, "render_target_lang", "") or "zh"),
            emotion_context=_safe_mapping(getattr(agent_input, "emotion_context", None)),
            emotion_snapshot=_safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
            retrieval_summary=_safe_mapping(getattr(agent_input, "retrieval_summary", None)) or {"status": "SKIPPED"},
            retrieval_evidence_items=list(getattr(agent_input, "retrieval_evidence_items", None) or []),
            person_id=str(getattr(agent_input, "person_id", "") or ""),
            session_id=str(getattr(agent_input, "session_id", "") or ""),
            request_id=str(getattr(agent_input, "request_id", "") or ""),
            thread_id=getattr(agent_input, "thread_id", None),
            bond_summary=_safe_mapping(getattr(agent_input, "bond_summary", None)),
            intent_draft=_safe_mapping(getattr(agent_input, "intent_draft", None)),
            organ_context=_surface_organ_context(agent_input, trace_stage_meta=trace_stage_meta),
            surface_task_control=direct_reply_plan,
            eval_history_turns=list(getattr(agent_input, "eval_history_turns", None) or []),
        )
        if merge_trace:
            _merge_surface_trace(trace_stage_meta, runtime_payload)
        return runtime_payload
    except Exception as exc:
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["surface_context_prepare_failed"] = type(exc).__name__
    return {}


def build_surface_context_payload_from_adapter(
    *,
    ports: SurfaceRuntimePorts,
    user_message: str,
    request_id: str = "",
    session_id: str = "",
    person_id: str = "",
    render_target_lang: str = "zh",
    emotion_context: Mapping[str, Any] | None = None,
    emotion_snapshot: Mapping[str, Any] | None = None,
    retrieval_summary: Mapping[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    intent_draft: Mapping[str, Any] | None = None,
    organ_context: Mapping[str, Any] | None = None,
    surface_task_control: Mapping[str, Any] | None = None,
    eval_history_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return ports.build_surface_context_packet(
        user_message=user_message,
        render_target_lang=render_target_lang,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        retrieval_summary=retrieval_summary or {"status": "SKIPPED"},
        retrieval_evidence_items=list(retrieval_evidence_items or []),
        person_id=person_id,
        session_id=session_id,
        request_id=request_id,
        bond_summary=bond_summary,
        intent_draft=intent_draft,
        organ_context=organ_context,
        surface_task_control=surface_task_control,
        eval_history_turns=list(eval_history_turns or []),
    )


def _surface_organ_context(agent_input: Any, *, trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    agent_trace = _safe_mapping(getattr(agent_input, "trace_stage_meta", None))
    for container in (trace, agent_trace):
        for key in ("surface_organ_context", "organ_context", "tribe_context"):
            raw = container.get(key)
            if isinstance(raw, Mapping):
                return dict(raw)

    organ_route_meta = _safe_mapping(trace.get("organ_route_meta") or agent_trace.get("organ_route_meta"))
    motive_state = _safe_mapping(trace.get("motive_state") or agent_trace.get("motive_state"))
    if organ_route_meta and not motive_state:
        motive_state = {
            "withdraw_drive": organ_route_meta.get("motive_withdraw"),
            "care_drive": organ_route_meta.get("motive_care"),
            "continuity_drive": organ_route_meta.get("motive_continuity"),
        }

    vital_signs = _safe_mapping(
        trace.get("tribe_vital_signs")
        or agent_trace.get("tribe_vital_signs")
        or trace.get("vital_signs")
        or agent_trace.get("vital_signs")
    )
    immune_state = _safe_mapping(trace.get("immune_state") or agent_trace.get("immune_state"))
    proprioception_state = _safe_mapping(trace.get("proprioception_state") or agent_trace.get("proprioception_state"))
    if organ_route_meta:
        if not immune_state:
            immune_state = {
                "risk_level": organ_route_meta.get("immune_risk_level"),
                "last_decision": organ_route_meta.get("immune_last_decision"),
            }
        if not proprioception_state:
            proprioception_state = {
                "cognitive_load_index": organ_route_meta.get("cognitive_load_index"),
                "interrupt_frequency": organ_route_meta.get("interrupt_frequency"),
            }

    context = {
        "vital_signs": vital_signs,
        "voice_emotion": _safe_mapping(trace.get("voice_emotion") or agent_trace.get("voice_emotion")),
        "motive_state": motive_state,
        "immune_state": immune_state,
        "proprioception_state": proprioception_state,
        "social_brain_state": _safe_mapping(trace.get("social_brain_state") or agent_trace.get("social_brain_state")),
        "endocrine_gate": _safe_mapping(trace.get("endocrine_gate") or agent_trace.get("endocrine_gate")),
        "hedonic_state": _safe_mapping(trace.get("hedonic_state") or agent_trace.get("hedonic_state")),
        "somatic_state": _safe_mapping(trace.get("somatic_state") or agent_trace.get("somatic_state")),
        "interoception_state": _safe_mapping(trace.get("interoception_state") or agent_trace.get("interoception_state")),
        "circadian_state": _safe_mapping(trace.get("circadian_state") or agent_trace.get("circadian_state")),
        "default_mode_state": _safe_mapping(trace.get("default_mode_state") or agent_trace.get("default_mode_state")),
        "mirror_neuron_state": _safe_mapping(trace.get("mirror_neuron_state") or agent_trace.get("mirror_neuron_state")),
        "language_dual_system": _safe_mapping(trace.get("language_dual_system") or agent_trace.get("language_dual_system")),
        "motor_cortex_state": _safe_mapping(trace.get("motor_cortex_state") or agent_trace.get("motor_cortex_state")),
        "cerebellum_state": _safe_mapping(trace.get("cerebellum_state") or agent_trace.get("cerebellum_state")),
        "vestibular_state": _safe_mapping(trace.get("vestibular_state") or agent_trace.get("vestibular_state")),
        "peripheral_state": _safe_mapping(trace.get("peripheral_state") or agent_trace.get("peripheral_state")),
        "initiative_decision": _safe_mapping(trace.get("initiative_decision") or agent_trace.get("initiative_decision")),
    }
    return {
        key: value
        for key, value in context.items()
        if isinstance(value, Mapping) and value
    }


def _prepared_surface_runtime_payload(trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    if not bool(trace.get("surface_context_packet_prepared")):
        return {}
    return {
        key: dict(value)
        for key in _TRACE_RUNTIME_PAYLOAD_KEYS
        if isinstance((value := trace.get(key)), Mapping) and value
    }


def _merge_surface_trace(trace_stage_meta: dict[str, Any] | None, runtime_payload: Mapping[str, Any]) -> None:
    if not isinstance(trace_stage_meta, dict):
        return
    if runtime_payload:
        trace_stage_meta["surface_context_packet_prepared"] = True
    for key in _TRACE_RUNTIME_PAYLOAD_KEYS:
        value = runtime_payload.get(key)
        existing = trace_stage_meta.get(key)
        if isinstance(value, Mapping) and value and not (isinstance(existing, Mapping) and existing):
            trace_stage_meta[key] = dict(value)


def _surface_task_control_from_reply_plan(
    *,
    agent_input: Any,
    reply_plan: Mapping[str, Any],
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reply_plan = _surface_apply_memory_context_reply_plan_override(
        reply_plan,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    reply_plan = _surface_apply_autonomous_self_query_reply_plan_override(
        reply_plan,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    preference_query_owned = _surface_user_preference_query_owned(agent_input, ports=ports)
    memory_plan_reason = _surface_memory_plan_reason(trace_stage_meta)
    memory_packet = _surface_memory_packet_for_output_guard(agent_input=agent_input, trace_stage_meta=trace_stage_meta)
    memory_readiness = _surface_memory_readiness(memory_packet)
    missing_detail_selection = _surface_memory_missing_detail_selection(memory_readiness)
    task_mode = _first_nonempty(
        reply_plan.get("task_mode"),
        reply_plan.get("primary_topic"),
        reply_plan.get("response_form_reason"),
        reply_plan.get("response_goal"),
        _safe_mapping(getattr(agent_input, "intent_draft", None)).get("route"),
        "daily_chat",
    )
    response_move = _first_nonempty(
        reply_plan.get("response_move"),
        reply_plan.get("interaction_move"),
        reply_plan.get("response_goal"),
        reply_plan.get("tactic_variant"),
        task_mode,
    )
    repeat_count = _surface_reply_plan_repeat_count(reply_plan)
    repeat_group = _surface_reply_plan_repeat_group(reply_plan)
    user_message = str(getattr(agent_input, "user_message", "") or "").strip()
    compact_user_message = re.sub(r"\s+", "", user_message)
    morning_greeting_mismatch = False
    if _surface_morning_greeting_mismatch_candidate(compact_user_message):
        try:
            from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415

            environment = _safe_mapping(build_environment_snapshot())
            day_phase = str(environment.get("day_phase") or "").strip()
            morning_greeting_mismatch = bool(day_phase and day_phase not in {"dawn", "morning"})
        except Exception:
            morning_greeting_mismatch = False
    if morning_greeting_mismatch:
        task_mode = "temporal_mismatch_greeting"
        response_move = "temporal_mismatch_greeting"
        reply_plan = dict(reply_plan)
        reply_plan["task_mode"] = task_mode
        reply_plan["response_move"] = response_move
        reply_plan["response_goal"] = "correct_temporal_greeting_mismatch"
        if repeat_count > 0:
            group_text = f"；重复类别={repeat_group}" if repeat_group else ""
            reply_plan["reply_contract"] = (
                f"用户重复发送早安类问候，之前已有 {repeat_count} 次同类输入{group_text}；"
                "当前也不是早上。必须同时承接“又说了一遍”和“时间错位”两个事实，"
                "不要按普通早安回复，也不要猜用户动机或状态。"
            )
        else:
            reply_plan["reply_contract"] = "用户说早安类问候但当前不是早上；自然指出时间错位，不要按普通早安回复。"
        must_include = list(_coerce_sequence(reply_plan.get("must_include")))
        must_include.append("明确承认当前不是早上，这句早安/早上好和当前时间阶段不匹配。")
        must_include.append("自然落到当前时间阶段，至少说清现在不是早上，而是下午、傍晚或晚上中的一种。")
        reply_plan["must_include"] = _clean_list(must_include)
    elif task_mode == "greeting":
        task_mode = "daily_chat"
        response_move = "reply_current_turn"
        reply_plan = dict(reply_plan)
        reply_plan["task_mode"] = task_mode
        reply_plan["response_move"] = response_move
        reply_plan["response_goal"] = "contextual_chat_reply"
        reply_plan["reply_contract"] = "低信息输入也走统一 surface 主链；结合当前上下文、环境、生命状态、Soul 和最近对话自然回应。"
    if preference_query_owned:
        task_mode = "user_preference_lookup"
        response_move = "answer_user_preference"
    elif memory_plan_reason in _MEMORY_RECALL_SURFACE_REASONS:
        task_mode = "memory_followup"
        response_move = "memory_followup"
    pragmatic_guidance_action = _surface_pragmatic_guidance_action(agent_input, ports=ports)
    if pragmatic_guidance_action == "clarify_reference":
        task_mode = "reference_clarification"
        response_move = "clarify_reference"
    life_director_pack = _surface_life_director_pack(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
        task_mode=task_mode,
        response_move=response_move,
    )
    if isinstance(trace_stage_meta, dict) and life_director_pack:
        trace_stage_meta["life_director"] = dict(life_director_pack)
    relationship_guidance = build_relationship_reply_guidance(
        _safe_mapping(getattr(agent_input, "emotion_context", None))
    )
    if isinstance(trace_stage_meta, dict) and relationship_guidance.available:
        trace_stage_meta["relationship_life_guidance"] = (
            relationship_guidance.trace()
        )
    task_facts = _task_facts(
        reply_plan=reply_plan,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    life_fact_lines = _surface_life_context_task_facts(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    if life_fact_lines:
        task_facts = _clean_list(
            [
                *life_fact_lines,
                *task_facts,
            ]
        )
    if relationship_guidance.facts:
        task_facts = _clean_list(
            [
                *relationship_guidance.facts,
                *task_facts,
            ]
        )
    pragmatic_fact_lines = _surface_pragmatic_intent_fact_lines(agent_input, ports=ports)
    if pragmatic_fact_lines:
        task_facts = _clean_list(
            [
                *pragmatic_fact_lines,
                *task_facts,
            ]
        )
    preference_fact_lines = _surface_user_preference_fact_lines(agent_input, ports=ports)
    if preference_fact_lines:
        task_facts = _clean_list(
            [
                *preference_fact_lines,
                *task_facts,
            ]
        )
    sub_agent_fact_lines = _surface_sub_agent_result_fact_lines(agent_input)
    if sub_agent_fact_lines:
        task_mode = "sub_agent_result"
        response_move = "report_sub_agent_result"
        reply_plan = dict(reply_plan)
        reply_plan["task_mode"] = task_mode
        reply_plan["response_move"] = response_move
        reply_plan["reply_contract"] = _first_nonempty(
            reply_plan.get("reply_contract"),
            "子代理已经返回执行结果；直接向用户说明结论、关键证据和必要下一步。",
        )
        task_facts = _clean_list(
            [
                *sub_agent_fact_lines,
                *task_facts,
            ]
        )
    if morning_greeting_mismatch:
        try:
            temporal_environment = _safe_mapping(environment)
        except Exception:
            temporal_environment = {}
        temporal_hour = temporal_environment.get("hour")
        temporal_minute = temporal_environment.get("minute")
        temporal_phase = str(temporal_environment.get("day_phase") or "").strip()
        temporal_routine = str(temporal_environment.get("routine_phase") or "").strip()
        temporal_time_text = ""
        if isinstance(temporal_hour, int) and isinstance(temporal_minute, int):
            temporal_time_text = f"{temporal_hour:02d}:{temporal_minute:02d}"
        task_facts = _clean_list(
            [
                (
                    f"时间错位主事实：当前本地时间是 {temporal_time_text or '未知'}，阶段是 {temporal_phase or '未知'}"
                    f"{'，生活窗口是 ' + temporal_routine if temporal_routine else ''}；用户是在指出早安/早上好不合时宜。"
                ),
                "时间错位回答策略：必须落到真实当前时间阶段；不要把非早晨说成天亮、早晨、上午、刚醒或睡迷糊。",
                *task_facts,
            ]
        )
    task_must_not_do = _clean_list(
        [
            *_coerce_sequence(reply_plan.get("must_not_do")),
            *_surface_sub_agent_result_must_not_do(agent_input),
            *_surface_dialogue_repeat_must_not_do(reply_plan),
            *_surface_life_context_must_not_do(
                agent_input,
                ports=ports,
                trace_stage_meta=trace_stage_meta,
            ),
            *_surface_user_preference_must_not_do(agent_input, ports=ports),
            *_surface_pragmatic_guidance_must_not_do(agent_input, ports=ports),
            *(() if preference_query_owned else _surface_memory_acceptance_must_not_do(agent_input)),
            *_surface_dream_context_must_not_do(agent_input),
            *relationship_guidance.must_not_do,
        ]
    )
    if morning_greeting_mismatch:
        task_must_not_do = _clean_list(
            [
                *task_must_not_do,
                "时间错位问候不得使用早晨/天亮/上午/刚醒/睡迷糊等与当前阶段冲突的说法。",
                "时间错位问候不要否认用户是在纠正时间；要承认现在不是早晨，再用角色口吻轻轻回应。",
            ]
        )
    if missing_detail_selection:
        card_lines = _surface_memory_card_fact_lines(memory_packet, limit=4)
        task_facts = _clean_list(
            [
                "缺正文状态：还没读正文/详情；只能按召回卡片给临时口味，不要当读后感。",
                f"召回卡片：{'；'.join(card_lines)}" if card_lines else "",
                *task_facts,
            ]
        )
        task_facts = [item[:260] for item in task_facts]
        task_must_not_do = _clean_list(
            [
                *task_must_not_do,
                "不要按书名自由联想题材、氛围、隐喻、文笔、节奏或读者适配；只能使用召回卡片里的看点/简介。",
                "不要说“值得试读”“大概率好看”“前二三十页”这类像书评模板的话。",
                "不要照搬或改写成“奇幻身份钩子、资料完整、标题辨识度高、现实感交叠、正文撑不撑得住”这类字段腔。",
            ]
        )
        task_must_not_do = [item[:180] for item in task_must_not_do]
    return {
        "response_move": response_move,
        "task_mode": task_mode,
        "reply_contract": _first_nonempty(reply_plan.get("reply_contract"), reply_plan.get("response_goal"), ""),
        "information_budget": _first_nonempty(reply_plan.get("information_budget"), "short"),
        "question_budget": max(
            1 if pragmatic_guidance_action == "clarify_reference" else 0,
            _coerce_int(
                _first_nonempty(reply_plan.get("question_budget"), reply_plan.get("max_questions")),
                default=0,
            ),
        ),
        "must_include": _clean_list(
            [
                *_coerce_sequence(reply_plan.get("must_include")),
                *_surface_sub_agent_result_must_include(agent_input),
                *_surface_memory_context_orchestrator_must_include(
                    agent_input=agent_input,
                    ports=ports,
                    trace_stage_meta=trace_stage_meta,
                ),
                *_surface_pragmatic_guidance_must_include(agent_input, ports=ports),
                *(
                    ()
                    if preference_query_owned
                    else _surface_memory_acceptance_must_include(agent_input, ports=ports)
                ),
                *relationship_guidance.must_include,
            ]
        ),
        "must_not_do": task_must_not_do,
        "facts": task_facts,
        "style_anchor": _clean_list(
            [
                "app_surface_main_chain",
                "召回回答要使用已召回的经历对象、证据或长期事实，不要只按当前这句话泛聊。" if memory_plan_reason in _MEMORY_RECALL_SURFACE_REASONS else "",
                "指代追问要承接已召回的最近经历对象。" if memory_plan_reason == "anaphoric_selection_recall" else "",
                "选择评价追问只说基于召回和补读证据的判断，不要臆测用户是否看过、忘了、终于想起来或终于来问。" if memory_plan_reason == "anaphoric_selection_recall" else "",
                "缺正文/详情时交给主链自然表达：不要写系统免责声明；可以说没读正文、只按简介或卡片第一眼先挑，且读完可能改口。" if missing_detail_selection else "",
                "缺正文/详情时不要照搬字段名，例如“奇幻身份钩子、资料完整、标题辨识度高”；要改成像人在书摊前挑书的口吻。" if missing_detail_selection else "",
                "用户偏好画像只提供证据，不负责最终话术；偏好问答必须由模型自然生成，明确区分确定偏好和低置信推断。" if preference_fact_lines else "",
                "日期/近期集合追问要保留用户指定时间范围，并点出集合里的具体条目。" if memory_plan_reason == "collection_memory_recall" else "",
                "失败追问只能解释召回事实里的失败结果和证据，不要猜新的根因。" if memory_plan_reason == "failure_debug_recall" else "",
                "证据审查追问必须回答结论和依据；不要改写成日期集合列表，也不要夹带不相关的推荐条目。" if memory_plan_reason == "evidence_review_recall" else "",
                *relationship_guidance.style_anchor,
                _first_nonempty(reply_plan.get("style_anchor"), ""),
            ]
        ),
    }
