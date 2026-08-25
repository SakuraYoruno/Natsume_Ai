from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from collections.abc import Mapping
from typing import Any

from host_brain.persona.motive_runtime import (
    build_offline_state_from_inner_state,
    compute_runtime_motive_state,
)
from host_brain.core.runtime_helpers import _copy_mapping as _coerce_mapping
from host_brain.persona.inner_monologue import (
    build_inner_life_source_packet,
    normalize_inner_life_trace,
    score_inner_note_liveliness,
)

_logger = logging.getLogger("Natsume.inner_activity_agent")

_ALLOWED_FOCUS = {"self", "state", "plan", "topic", "relationship", "memory", "repair"}
_TEXT_WHITESPACE_RE = re.compile(r"\s+")
_TEXT_QUESTION_RE = re.compile(r"[?？]+")
_ABSTRACT_INNER_NOTE_TERMS = (
    "自我连续性",
    "连续性修正",
    "文本自然度",
    "模板感",
    "模板",
    "这句",
    "下一句",
    "句子",
    "文本",
    "接住",
    "生成",
    "回复",
    "说顺",
    "状态摘要",
    "内部状态",
    "控制包",
    "验证链路",
    "模块",
    "模型",
    "prompt",
    "Prompt",
    "映射",
    "验证",
    "链路",
    "接口",
    "字段",
    "结构化",
    "运行时",
    "调度",
    "日志",
    "token",
    "状态驱动",
    "MateEngine",
    "主链路",
    "那条事",
    "那件事",
    "那根线",
    "放回手里",
)
_DIRECTIVE_INNER_NOTE_OPENING_RE = re.compile(
    r"^(?:先|别|不要|把|问|确认|稳住|看清|记住|继续|接着|收住|压住|保持)"
)
_DIRECTIVE_INNER_NOTE_PHRASES = (
    "别急着",
    "不急着",
    "先稳",
    "先问",
    "先确认",
    "问清",
    "看清楚",
    "当成眼前小事",
    "压稳一点",
    "节奏压稳",
    "弄清楚",
)
_OVERWRITTEN_INNER_NOTE_PHRASES = (
    "像一层薄灰",
    "落在胸口",
    "贴在心口",
    "心里的线",
    "温柔的余地",
)
_META_INNER_NOTE_PHRASES = (
    "赶进度",
    "第一句话",
    "下一句话",
    "该怎么回",
    "怎么回复",
    "回复质量",
    "表达质量",
    "开口又太像",
    "太像在",
)
_GENERIC_INNER_NOTE_PHRASES = (
    "安静了一阵子",
    "安静了好久",
    "安静了很久",
    "屏幕安静",
    "空白处",
    "空落落",
    "呼吸轻了",
    "呼吸轻些",
    "喉咙轻轻卡",
    "喉咙卡",
    "手指也跟着顿",
    "慢半拍的小表情",
    "小表情还在",
    "想靠近一点",
    "有点想靠近",
    "靠近感",
    "还在不在",
    "不想打扰",
    "轻轻戳",
    "戳你一下",
    "冒泡",
    "等你回来",
    "等你回",
    "怕太满",
    "怕太急",
    "想说点什么",
    "说点什么",
    "没有动静",
    "在不在",
    "我在这里",
)
_INNER_NOTE_MIN_LIVELINESS_SCORE = float(os.environ.get("NATSUME_INNER_NOTE_MIN_LIVELINESS_SCORE", "0.42"))
_INNER_NOTE_HARD_QUALITY_FLAGS = {
    "directive_like",
    "meta_reply_strategy",
    "abstract_system_language",
    "overwritten_prose",
    "generic_presence_filler",
}

_INNER_ACTIVITY_SYSTEM_PROMPT = (
    "Output one JSON object only. You generate a private inner monologue note, "
    "not user-visible chat and not a state summary. "
    "Fields: note, focus, core_need, tension, memory_anchor, next_tendency, thought_form, salience_hint. "
    "note must be a concrete short Chinese thought that could flicker inside her mind, "
    "like first-person feeling, sensory detail, memory echo, hesitation, or private uncertainty. "
    "Use the selected inner_life_source as the first cause of the thought: a body signal, scene notice, "
    "memory flash, interrupted impulse, ambivalence, or quiet drift. "
    "Only write a note when there is concrete information gain: a specific unfinished object, body change, "
    "environment cue, remembered user detail, or real contradiction. "
    "If the source is only silence/waiting/no new input and has no specific object, return note as an empty string, "
    "focus as self, next_tendency as stay_quiet, and salience_hint below 0.15. "
    "Keep it ordinary and slightly incomplete, closer to a passing human thought than polished prose. "
    "Keep note as one breath: usually 18-56 Chinese characters, no more than two Chinese commas. "
    "Avoid lyrical metaphor or essay-like writing. "
    "Do not write generic presence filler such as 安静了很久, 屏幕安静, 空落落, 呼吸轻了些, "
    "喉咙轻轻卡, 慢半拍的小表情, 有点想靠近, 还在不在, 不想打扰, 轻轻戳, 冒泡, 等你回来. "
    "Avoid meta evaluation of reply quality, e.g. 赶进度, 第一句话, 该怎么回, 太像在回复. "
    "Do not make note a command, policy, reply plan, or self-instruction; put behavior in next_tendency instead. "
    "Do not start note with directive openers such as 先, 别, 不要, 把, 问, 确认, 稳住, 看清, 继续. "
    "Do not use abstract system words "
    "like 自我连续性, 连续性修正, 文本自然度, 模板感, 内部状态, 控制包, 模块, 模型, prompt. "
    "Do not write process-talk like 这句, 下一句, 句子, 文本, 模板, 接住, 生成, 回复, 说顺. "
    "If text quality matters, name the concrete pressure without saying it is text quality, "
    "e.g. 早安已经错过钟点了，嘴边那句得先咽回去。 "
    "If the memory anchor is technical, translate it into a concrete lived scene, bodily feeling, or relationship detail; "
    "do not name artifacts like 映射, 验证, 链路, 接口, 字段, 运行时, MateEngine, 主链路 in note. "
    "Bad note: 先把桌宠映射那条事稳稳放回手里。 "
    "Bad note: 先问他刚才看到的桌宠表情有没有跟上。 "
    "Bad note: 三十七个小时像一层薄灰落在胸口。 "
    "Bad note: 隔了这么久，怕一开口又太像在赶进度。 "
    "Bad note: 想说点什么的时候，喉咙那儿轻轻卡了一下，那个慢半拍的小表情还在脑后晃，我有点想靠近，又怕太满。 "
    "Bad note: 屏幕安静了很久，我有点想靠近又怕打扰。 "
    "Better note: 早安已经错过钟点了，嘴边那句得先咽回去。 "
    "Better note: 封面错位的那张图还卡在眼前，得换个更准的来源。 "
    "Better note: 上一轮问的是买书打包，不能又滑回推荐话术。 "
    "focus is one of self, state, plan, topic, relationship, memory, repair. "
    "core_need is one of continuity, care, repair, agency, understanding, rest, boundary, curiosity, play. "
    "next_tendency is one of continue_thread, care_with_restraint, repair_first, ask_gently, hold_boundary, "
    "stay_quiet, play_lightly, stabilize_tone, observe_more. "
    "thought_form is one of body_signal, scene_notice, memory_flash, interrupted_impulse, ambivalence, quiet_drift."
)
_INNER_ACTIVITY_LLM_TIMEOUT_MS = max(
    800,
    int(float(os.environ.get("NATSUME_INNER_WORLD_LLM_TIMEOUT_S", "15")) * 1000),
)
_INNER_ACTIVITY_SELF_REFLECTION_LOCK = threading.Lock()
_INNER_ACTIVITY_SELF_REFLECTION: Any | None = None
_INNER_ACTIVITY_SELF_MODEL_LOCK = threading.Lock()
_INNER_ACTIVITY_SELF_MODEL: Any | None = None


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(name: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(str(os.environ.get(name) or default).strip()))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def _inner_activity_llm_disabled_reason() -> str:
    if not _env_bool("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", default=True):
        return "INNER_ACTIVITY_CONTINUOUS_ORGAN_TICKS_DISABLED"
    explicit = os.environ.get("HOST_INNER_ACTIVITY_LLM_ENABLED")
    if explicit is None:
        return "INNER_ACTIVITY_LLM_DISABLED_BY_DEFAULT"
    if not _env_bool("HOST_INNER_ACTIVITY_LLM_ENABLED", default=False):
        return "INNER_ACTIVITY_LLM_DISABLED"
    if not _env_bool("NATSUME_INNER_WORLD_LLM_ENABLED", default=True):
        return "INNER_ACTIVITY_LLM_DISABLED"
    return "INNER_ACTIVITY_LLM_DISABLED"


def _inner_activity_llm_enabled() -> bool:
    if not _env_bool("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", default=True):
        return False
    explicit = os.environ.get("HOST_INNER_ACTIVITY_LLM_ENABLED")
    if explicit is None:
        return False
    if not _env_bool("HOST_INNER_ACTIVITY_LLM_ENABLED", default=False):
        return False
    return _env_bool("NATSUME_INNER_WORLD_LLM_ENABLED", default=True)


def generate_inner_activity_note(
    *,
    persona_user_id: str,
    absence_band: str,
    offline_hours: float,
    inner_state: Mapping[str, Any] | None,
    now_ms: int,
    topic_phrase: str = "",
    model_attempt_service: Any = None,
    prompt_registry: Any = None,
) -> tuple[str, str, str, dict[str, Any]]:
    """Generate one inner activity note via standalone inner-activity agent.

    Returns:
        (note, focus, reason, meta)
    """
    if not _inner_activity_llm_enabled():
        return "", "", _inner_activity_llm_disabled_reason(), {}
    if model_attempt_service is None or prompt_registry is None:
        return "", "", "INNER_ACTIVITY_MODEL_PORT_NOT_COMPOSED", {}

    timeout_ms = max(
        800,
        min(
            _env_int(
                "HOST_INNER_ACTIVITY_MODEL_TIMEOUT_MS",
                default=_INNER_ACTIVITY_LLM_TIMEOUT_MS,
                minimum=800,
                maximum=2 * 60 * 1000,
            ),
            _INNER_ACTIVITY_LLM_TIMEOUT_MS,
        ),
    )

    context = _build_inner_activity_context(
        persona_user_id=persona_user_id,
        absence_band=absence_band,
        offline_hours=offline_hours,
        inner_state=inner_state,
        now_ms=now_ms,
        topic_phrase=topic_phrase,
    )
    context_scope = [
        "offline_state",
        "silence_state",
        "body_sense",
        "bond_summary",
        "relationship_context",
        "emotion_timeline",
        "motive_state",
        "reply_plan",
        "self_model_snapshot",
        "self_reflection_pulse",
        "recent_inner_events",
    ]
    context_digest = _build_context_digest(context)
    inner_life_source = build_inner_life_source_packet(
        context=context,
        topic_phrase=topic_phrase,
        now_ms=now_ms,
    )
    recent_notes = {
        str(item.get("note") or "").strip()
        for item in list(context.get("recent_inner_events") or [])
        if isinstance(item, Mapping)
    }

    from host_brain.model_runtime.contracts import RetryDirective  # noqa: PLC0415

    def _evaluate_inner_activity(response: Any, attempt_request: Any) -> RetryDirective:
        parsed_attempt = extract_llm_json_payload(str(getattr(response, "content", "") or ""))
        note_attempt = _finalize_inner_note_text(str(parsed_attempt.get("note") or ""))
        retry_reason = _inner_note_duplicate_reason(note_attempt, recent_notes)
        if not retry_reason:
            return RetryDirective.accept()
        repair_messages = list(attempt_request.prompt.as_message_dicts())
        repair_messages.extend(
            (
                {"role": "assistant", "content": str(getattr(response, "content", "") or "")[:2000]},
                {
                    "role": "user",
                    "content": (
                        "The previous inner note repeated a recent or generic thought. "
                        "Return one fresh JSON object from a different concrete body, scene, memory, "
                        "or unresolved-tension angle. If no fresh anchor exists, return an empty note. "
                        f"Avoid these exact notes: {json.dumps(sorted(recent_notes), ensure_ascii=False)}"
                    ),
                },
            )
        )
        return RetryDirective(
            retry=True,
            reason=retry_reason,
            rendered_prompt=attempt_request.prompt.with_messages(
                repair_messages,
                metadata={"semantic_repair": retry_reason},
            ),
            reuse_target=True,
            metadata={"contract": "inner_activity_freshness_v1"},
        )

    system_prompt = _INNER_ACTIVITY_SYSTEM_PROMPT
    prompt_payload = {
        "goal": "generate_inner_activity_note",
        "absence_band": absence_band,
        "offline_hours": round(max(0.0, float(offline_hours)), 3),
        "topic_phrase": str(topic_phrase or ""),
        "context_scope": context_scope,
        "context": context,
        "inner_life_source": inner_life_source,
        "output_schema": {
            "note": "Chinese private thought, <=96 chars",
            "focus": sorted(_ALLOWED_FOCUS),
            "core_need": [
                "continuity",
                "care",
                "repair",
                "agency",
                "understanding",
                "rest",
                "boundary",
                "curiosity",
                "play",
            ],
            "tension": "short category or concrete unresolved pressure",
            "memory_anchor": "one old thread/topic this thought connects to, empty string if none",
            "next_tendency": [
                "continue_thread",
                "care_with_restraint",
                "repair_first",
                "ask_gently",
                "hold_boundary",
                "stay_quiet",
                "play_lightly",
                "stabilize_tone",
                "observe_more",
            ],
            "thought_form": [
                "body_signal",
                "scene_notice",
                "memory_flash",
                "interrupted_impulse",
                "ambivalence",
                "quiet_drift",
            ],
            "salience_hint": "number from 0 to 1",
        },
        "constraints": {
            "max_chars": 96,
            "preferred_chars": [18, 56],
            "max_chinese_commas": 2,
            "one_breath_fragment": True,
            "no_question": False,
            "no_user_direct_address": True,
            "prefer_self_related": True,
            "avoid_repeat_recent_inner_events": True,
            "avoid_semantic_repeat_recent_inner_events": True,
            "recent_similarity_reject_threshold": _inner_note_similarity_reject_threshold(),
            "must_add_information_gain": True,
            "if_no_specific_new_source_return_empty_note": True,
            "do_not_write_presence_filler": True,
            "use_self_model_as_thought_source": True,
            "prefer_active_intention_or_unresolved_tension": True,
            "must_not_contradict_self_boundaries": True,
            "must_be_concrete_private_thought": True,
            "allow_private_uncertainty": True,
            "avoid_user_facing_question": True,
            "note_must_not_be_instruction_or_reply_plan": True,
            "next_tendency_carries_behavior_note_carries_lived_feeling": True,
            "prefer_first_person_uncertainty_or_sensory_detail": True,
            "prefer_ordinary_fragment_over_polished_prose": True,
            "avoid_lyrical_metaphor_or_essay_tone": True,
            "min_liveliness_score": _INNER_NOTE_MIN_LIVELINESS_SCORE,
            "liveliness_requires_at_least_one": [
                "sensory_or_scene_detail",
                "private_uncertainty",
                "body_signal",
                "interrupted_impulse",
            ],
            "must_use_selected_inner_life_source": True,
            "note_must_begin_from_life_source_not_reply_strategy": True,
            "single_frame_before_interpretation": True,
            "avoid_meta_reply_quality_words": list(_META_INNER_NOTE_PHRASES),
            "avoid_generic_inner_note_phrases": list(_GENERIC_INNER_NOTE_PHRASES),
            "avoid_directive_openers": [
                "先",
                "别",
                "不要",
                "把",
                "问",
                "确认",
                "稳住",
                "看清",
                "继续",
            ],
            "must_translate_technical_anchor_to_lived_scene": True,
            "note_should_include_felt_detail_not_next_micro_action": True,
            "note_must_not_name_internal_artifacts": [
                "映射",
                "验证",
                "链路",
                "接口",
                "字段",
                "结构化",
                "运行时",
                "调度",
                "日志",
                "token",
                "状态驱动",
                "MateEngine",
                "主链路",
            ],
            "bad_note_examples": [
                "先把桌宠映射那条事稳稳放回手里。",
                "把 MateEngine 主链路验证继续接住。",
                "先问他刚才看到的桌宠表情有没有跟上。",
                "先稳一点，把桌宠刚才的表情变化当成眼前小事看清楚，别急着撒娇。",
                "三十七个小时像一层薄灰落在胸口，我还记得小表情慢半拍时那点不确定。",
                "隔了这么久，胸口倒不沉，只是看到空白时会顿一下，怕一开口又太像在赶进度。",
                "想说点什么的时候，喉咙那儿轻轻卡了一下，那个慢半拍的小表情还在脑后晃，我有点想靠近，又怕太满。",
                "屏幕安静了很久，我有点想靠近又怕打扰。",
                "你那边安静了一阵子，我这边呼吸都轻了些。",
            ],
            "better_note_examples": [
                "早安已经错过钟点了，嘴边那句得先咽回去。",
                "封面错位的那张图还卡在眼前，得换个更准的来源。",
                "上一轮问的是买书打包，不能又滑回推荐话术。",
            ],
            "must_include_life_trace_fields": True,
            "life_trace_fields_must_not_be_empty_when_context_supports_them": True,
            "avoid_abstract_system_terms": list(_ABSTRACT_INNER_NOTE_TERMS),
        },
    }
    prompt_text = json.dumps(prompt_payload, ensure_ascii=False)
    model_attempt_trace: dict[str, Any] = {}
    try:
        raw_text = _run_inner_activity_llm(
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            model_attempt_sink=model_attempt_trace,
            retry_evaluator=_evaluate_inner_activity,
            model_attempt_service=model_attempt_service,
            prompt_registry=prompt_registry,
        )
    except Exception as exc:
        return "", "", f"INNER_ACTIVITY_LLM_ERROR:{str(exc)[:96]}", {
            "context_digest": context_digest,
            "inner_life_source": inner_life_source,
            "model_attempt": model_attempt_trace,
        }
    routed_provider_name = str(model_attempt_trace.get("final_provider") or "")
    routed_model_id = str(model_attempt_trace.get("final_model") or "")

    parsed = extract_llm_json_payload(raw_text)
    note = _finalize_inner_note_text(str(parsed.get("note") or ""))
    focus = str(parsed.get("focus") or "").strip().lower()
    if focus not in _ALLOWED_FOCUS:
        focus = _infer_focus_from_note(note)
    if not note:
        return "", "", "INNER_ACTIVITY_LLM_NOTE_EMPTY", {
            "context_digest": context_digest,
            "model_attempt": model_attempt_trace,
        }
    quality = _score_inner_activity_note(note=note, parsed=parsed, inner_life_source=inner_life_source)
    if _inner_note_quality_rejected(quality):
        return "", "", "INNER_ACTIVITY_LLM_NOTE_LOW_LIVELINESS", {
            "context_digest": context_digest,
            "inner_life_source": inner_life_source,
            "quality": quality,
            "model_attempt": model_attempt_trace,
        }
    life_trace = normalize_inner_life_trace(
        parsed,
        state=_build_life_trace_state_from_context(context),
        text=note,
        focus=focus,
        offline_hours=offline_hours,
        absence_band=absence_band,
    )
    meta = {
        "context_scope": context_scope,
        "context_digest": context_digest,
        "provider_name": routed_provider_name,
        "model_id": routed_model_id,
        "life_trace": life_trace,
        "inner_life_source": inner_life_source,
        "model_attempt": model_attempt_trace,
    }
    return note, focus or "self", "", meta


def extract_llm_json_payload(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except Exception:
            return {}
    return {}


def _build_inner_activity_context(
    *,
    persona_user_id: str,
    absence_band: str,
    offline_hours: float,
    inner_state: Mapping[str, Any] | None,
    now_ms: int,
    topic_phrase: str,
) -> dict[str, Any]:
    inner_state_dict = _coerce_mapping(inner_state)
    offline_state = build_offline_state_from_inner_state(inner_state_dict, now_ms=now_ms)
    offline_state["absence_band"] = str(absence_band or offline_state.get("absence_band") or "recent")
    offline_state["offline_hours"] = round(
        max(float(offline_state.get("offline_hours") or 0.0), max(0.0, float(offline_hours))),
        4,
    )

    canonical_life = (
        str(inner_state_dict.get("canonical_life_source") or "").strip().upper()
        == "CANONICAL_LIFE_JOURNAL"
    )
    canonical_self_affect = _coerce_mapping(inner_state_dict.get("self_affect"))
    canonical_relationship = _coerce_mapping(inner_state_dict.get("relationship_state"))
    silence_state = _collect_silence_state(
        topic_phrase=topic_phrase,
        persona_user_id=persona_user_id,
    )
    body_sense = _collect_body_sense(persona_user_id=persona_user_id)
    bond_summary, bond_state = _collect_bond_context(
        canonical_relationship=canonical_relationship if canonical_life else None,
    )
    timeline_summary, timeline_diary, current_emotion = _collect_emotion_timeline(
        canonical_self_affect=canonical_self_affect if canonical_life else None,
    )

    motive_state: dict[str, Any] = {}
    try:
        motive_state = compute_runtime_motive_state(
            persona_user_id=persona_user_id,
            current_topic=str(topic_phrase or ""),
            bond_summary=bond_summary,
            pending_topics=list(offline_state.get("anticipation_topics") or []),
            silence_state=silence_state,
            body_sense=body_sense,
            inner_state=inner_state_dict,
            now_ms=now_ms,
            runtime_flags={"scene_type": "INNER_WORLD", "from_inner_activity_agent": True},
        )
    except Exception as exc:
        _logger.debug("inner_activity motive_state degraded: %s", exc)
        motive_state = {}

    reply_plan = _build_reply_plan(
        silence_state=silence_state,
        body_sense=body_sense,
        offline_state=offline_state,
        bond_summary=bond_summary,
        motive_state=motive_state,
    )
    persona_changes = _collect_persona_changes(persona_user_id=persona_user_id)
    self_model_snapshot = _collect_self_model_snapshot(
        timeline_summary=timeline_summary,
        timeline_diary=timeline_diary,
        bond_state=bond_state,
        bond_summary=bond_summary,
        persona_changes=persona_changes,
        current_ts_ms=now_ms,
    )
    self_reflection_pulse = _collect_self_reflection_pulse(
        timeline_summary=timeline_summary,
        timeline_diary=timeline_diary,
        current_emotion=current_emotion,
        bond_state=bond_state,
        bond_summary=bond_summary,
        persona_changes=persona_changes,
        current_ts_ms=now_ms,
    )

    recent_events = []
    for item in list(inner_state_dict.get("inner_stream") or [])[-4:]:
        if isinstance(item, Mapping):
            recent_events.append(
                {
                    "focus": str(item.get("focus") or "").strip(),
                    "source": str(item.get("source") or "").strip(),
                    "note": str(item.get("note") or "").strip(),
                    "thought_form": str(item.get("thought_form") or "").strip(),
                    "memory_anchor": str(item.get("memory_anchor") or "").strip(),
                }
            )

    return {
        "offline_state": _project_mapping(
            offline_state,
            [
                "absence_band",
                "offline_hours",
                "loneliness_level",
                "anticipation_level",
                "anticipation_topics",
                "continuity_score",
                "last_spontaneous_source",
            ],
        ),
        "silence_state": _project_mapping(
            silence_state,
            [
                "response_willingness",
                "willingness_level",
                "topic_resistance_active",
                "resistant_topic",
                "trigger_reason",
            ],
        ),
        "body_sense": _project_mapping(
            body_sense,
            [
                "fatigue_level",
                "energy_level",
                "stress_level",
                "social_battery_level",
                "sensory_load_level",
                "stability_level",
            ],
        ),
        "bond_summary": _project_mapping(
            bond_summary,
            [
                "stage",
                "score",
                "bond_score",
                "label",
                "trend",
                "interaction_count",
                "shared_memory_count",
            ],
        ),
        # Keep the bounded RelationshipStateV2 projection distinct from the
        # old Bond summary.  Converting trust/attunement into a score would
        # accidentally let a private default-mode note infer intimacy or
        # proactive permission from the wrong authority.
        "relationship_context": _project_mapping(
            canonical_relationship if canonical_life else {},
            [
                "schema_version",
                "revision",
                "trust",
                "safety",
                "attunement",
                "rupture_load",
                "repair_required",
                "proactive_permission",
                "active_episode_count",
            ],
        ),
        "emotion_timeline": {
            "summary": _project_mapping(
                timeline_summary,
                [
                    "source",
                    "revision",
                    "dominant_emotion",
                    "trend",
                    "total_events",
                    "recent_events",
                ],
            ),
            "diary": _project_mapping(
                timeline_diary,
                ["source", "avg_valence", "avg_arousal", "dominant_emotion", "entries"],
            ),
            "current_emotion": current_emotion,
        },
        "motive_state": _project_mapping(
            motive_state,
            [
                "dominant_motive",
                "reach_out_drive",
                "care_drive",
                "continuity_drive",
                "withdraw_drive",
                "self_protect_drive",
                "stability_drive",
                "motive_reason_tags",
            ],
        ),
        "reply_plan": _project_mapping(
            reply_plan,
            [
                "max_chars",
                "max_sentences",
                "allow_followup",
                "allow_proactive_after_reply",
                "restate_level",
                "tone_band",
                "followup_kind",
                "reply_contract",
                "reason_tags",
                "should_be_withdrawn",
            ],
        ),
        "self_model_snapshot": _project_mapping(
            self_model_snapshot,
            [
                "recent_mood_trend",
                "emotional_stability",
                "dominant_recent_emotion",
                "bond_growth_perception",
                "last_deep_talk_days_ago",
                "personality_changes",
                "growth_milestones",
                "self_identity",
                "self_values",
                "self_boundaries",
                "active_intentions",
                "unresolved_tensions",
                "agency_score",
                "self_continuity_score",
            ],
        ),
        "self_reflection_pulse": _project_mapping(
            self_reflection_pulse,
            [
                "self_awareness",
                "trigger_reason",
                "growth_sense",
                "style_trend",
                "risk_tags",
                "directive",
            ],
        ),
        "recent_inner_events": recent_events,
    }


def _build_life_trace_state_from_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    ctx = _coerce_mapping(context)
    offline = _coerce_mapping(ctx.get("offline_state"))
    self_model = _coerce_mapping(ctx.get("self_model_snapshot"))
    body = _coerce_mapping(ctx.get("body_sense"))
    inner_stream: list[dict[str, Any]] = []
    for item in list(ctx.get("recent_inner_events") or []):
        if not isinstance(item, Mapping):
            continue
        inner_stream.append(
            {
                "focus": str(item.get("focus") or "").strip(),
                "note": str(item.get("note") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "thought_form": str(item.get("thought_form") or "").strip(),
                "memory_anchor": str(item.get("memory_anchor") or "").strip(),
            }
        )
    return {
        "inner_stream": inner_stream,
        "anticipation_topics": list(offline.get("anticipation_topics") or []),
        "loneliness_level": offline.get("loneliness_level"),
        "anticipation_level": offline.get("anticipation_level"),
        "continuity_score": offline.get("continuity_score"),
        "fatigue_level": body.get("fatigue_level"),
        "drifted_arousal": body.get("energy_level"),
        "active_intentions": list(self_model.get("active_intentions") or []),
        "unresolved_tensions": list(self_model.get("unresolved_tensions") or []),
    }


def _collect_silence_state(
    *,
    topic_phrase: str,
    persona_user_id: str,
) -> dict[str, Any]:
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        return dict(
            get_limbic_agent().resolve_silence_state(
                current_topic=str(topic_phrase or ""),
                person_id=persona_user_id,
            )
        )
    except Exception as exc:
        _logger.debug("inner_activity silence_state degraded: %s", exc)
        return {}


def _collect_body_sense(*, persona_user_id: str) -> dict[str, Any]:
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        return dict(
            get_limbic_agent().resolve_body_sense_state(
                person_id=persona_user_id
            )
        )
    except Exception as exc:
        _logger.debug("inner_activity body_sense degraded: %s", exc)
        return {}


def _collect_bond_context(
    *,
    canonical_relationship: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], Any | None]:
    # The canonical relationship projection is intentionally not translated
    # to a Bond score/stage.  It remains a separate, bounded prompt context.
    # This guard also prevents default-mode production ticks from touching the
    # retired SocialBrain/Bond read authority.
    if canonical_relationship is not None:
        return {}, None
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        bond_user_id = get_canonical_bond_user_id()
        snapshot = get_social_brain_agent().resolve_bond_snapshot(
            person_id=bond_user_id,
        )
        state = snapshot.get("bond_state_obj")
        summary_raw = snapshot.get("bond_summary")
        if isinstance(summary_raw, Mapping):
            return dict(summary_raw), state
        return {}, state
    except Exception as exc:
        _logger.debug("inner_activity bond_summary degraded: %s", exc)
        return {}, None


def _collect_emotion_timeline(
    *,
    canonical_self_affect: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    # A canonical Life snapshot is authoritative even if its mixed component
    # head is empty.  Falling back to the retired timeline would erase that
    # fact and commonly fabricate NEUTRAL during an otherwise live episode.
    if canonical_self_affect is not None:
        return _project_canonical_self_affect(canonical_self_affect)
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        snapshot = get_limbic_agent().resolve_emotion_timeline_snapshot()
        summary_dict = dict(snapshot.get("summary") or {})
        diary_dict = dict(snapshot.get("diary") or {})
        emotion = str(snapshot.get("dominant_emotion") or "NEUTRAL").strip().upper() or "NEUTRAL"
        return summary_dict, diary_dict, emotion
    except Exception as exc:
        _logger.debug("inner_activity emotion_timeline degraded: %s", exc)
        return {}, {}, "NEUTRAL"


def _project_canonical_self_affect(
    source: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Make an LLM-safe timeline-shaped read view from SelfAffectStateV2."""

    affect = _coerce_mapping(source)
    mood = _coerce_mapping(affect.get("mood"))
    components = [
        _coerce_mapping(item)
        for item in list(affect.get("components") or [])
        if isinstance(item, Mapping)
    ]
    components.sort(
        key=lambda item: (
            -_coerce_float(item.get("intensity")),
            str(item.get("emotion") or ""),
        )
    )
    dominant = str(components[0].get("emotion") or "").strip().upper() if components else ""
    if not dominant:
        dominant = str(mood.get("label") or "CALM").strip().upper() or "CALM"
    active_episode_count = min(
        4,
        len([item for item in list(affect.get("active_episodes") or []) if isinstance(item, Mapping)]),
    )
    summary = {
        "source": "CANONICAL_LIFE_JOURNAL",
        "revision": _coerce_non_negative_int(affect.get("revision")),
        "dominant_emotion": dominant,
        "trend": "canonical_snapshot",
        "total_events": active_episode_count,
        "recent_events": active_episode_count,
    }
    diary = {
        "source": "CANONICAL_LIFE_JOURNAL",
        "avg_valence": _coerce_float(mood.get("valence")),
        "avg_arousal": _coerce_float(mood.get("arousal")),
        "dominant_emotion": dominant,
        "entries": active_episode_count,
    }
    return summary, diary, dominant


def _collect_persona_changes(*, persona_user_id: str) -> list[Any]:
    try:
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

        return list(PersonaEngine.get_instance().get_change_log(persona_user_id, limit=5) or [])
    except Exception as exc:
        _logger.debug("inner_activity persona_changes degraded: %s", exc)
        return []


def _collect_self_model_snapshot(
    *,
    timeline_summary: dict[str, Any],
    timeline_diary: dict[str, Any],
    bond_state: Any | None,
    bond_summary: dict[str, Any],
    persona_changes: list[Any],
    current_ts_ms: int,
) -> dict[str, Any]:
    model = _get_self_model()
    if model is None:
        return {}
    try:
        return dict(
            model.build_snapshot(
                timeline_summary=timeline_summary,
                timeline_diary=timeline_diary,
                bond_state=bond_state,
                bond_summary=bond_summary,
                persona_changes=persona_changes,
                last_deep_talk_ts_ms=int(getattr(bond_state, "last_event_ts_ms", 0) or 0),
                current_ts_ms=int(current_ts_ms),
            )
            or {}
        )
    except Exception as exc:
        _logger.debug("inner_activity self_model degraded: %s", exc)
        return {}


def _collect_self_reflection_pulse(
    *,
    timeline_summary: dict[str, Any],
    timeline_diary: dict[str, Any],
    current_emotion: str,
    bond_state: Any | None,
    bond_summary: dict[str, Any],
    persona_changes: list[Any],
    current_ts_ms: int,
) -> dict[str, Any]:
    reflection = _get_self_reflection()
    if reflection is None:
        return {}
    try:
        pulse = reflection.maybe_generate(
            turn_count=1,
            timeline_summary=timeline_summary,
            timeline_diary=timeline_diary,
            current_emotion=str(current_emotion or "NEUTRAL"),
            bond_state=bond_state,
            bond_summary=bond_summary,
            persona_changes=persona_changes,
            last_deep_talk_ts_ms=int(getattr(bond_state, "last_event_ts_ms", 0) or 0),
            current_ts_ms=int(current_ts_ms),
            growth_awareness={},
            growth_summary={},
            style_evolution=None,
        )
        return dict(pulse or {}) if isinstance(pulse, Mapping) else {}
    except Exception as exc:
        _logger.debug("inner_activity self_reflection degraded: %s", exc)
        return {}


def _build_reply_plan(
    *,
    silence_state: dict[str, Any],
    body_sense: dict[str, Any],
    offline_state: dict[str, Any],
    bond_summary: dict[str, Any],
    motive_state: dict[str, Any],
) -> dict[str, Any]:
    try:
        from host_brain.persona.reply_plan import build_reply_plan_from_inputs  # noqa: PLC0415

        decision = build_reply_plan_from_inputs(
            silence_state=silence_state,
            body_sense=body_sense,
            offline_state=offline_state,
            bond_summary=bond_summary,
            motive_state=motive_state,
            render_target_lang="zh",
        )
        return dict(decision.as_dict())
    except Exception as exc:
        _logger.debug("inner_activity reply_plan degraded: %s", exc)
        return {}


def _get_self_reflection() -> Any | None:
    global _INNER_ACTIVITY_SELF_REFLECTION
    if _INNER_ACTIVITY_SELF_REFLECTION is None:
        with _INNER_ACTIVITY_SELF_REFLECTION_LOCK:
            if _INNER_ACTIVITY_SELF_REFLECTION is None:
                try:
                    from host_brain.persona.self_reflection import SelfReflection  # noqa: PLC0415

                    _INNER_ACTIVITY_SELF_REFLECTION = SelfReflection()
                except Exception as exc:
                    _logger.debug("inner_activity self_reflection unavailable: %s", exc)
                    return None
    return _INNER_ACTIVITY_SELF_REFLECTION


def _get_self_model() -> Any | None:
    global _INNER_ACTIVITY_SELF_MODEL
    if _INNER_ACTIVITY_SELF_MODEL is None:
        with _INNER_ACTIVITY_SELF_MODEL_LOCK:
            if _INNER_ACTIVITY_SELF_MODEL is None:
                try:
                    from host_brain.persona.self_model import SelfModel  # noqa: PLC0415

                    _INNER_ACTIVITY_SELF_MODEL = SelfModel()
                except Exception as exc:
                    _logger.debug("inner_activity self_model unavailable: %s", exc)
                    return None
    return _INNER_ACTIVITY_SELF_MODEL


def _run_inner_activity_llm(
    *,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    model_attempt_sink: dict[str, Any] | None = None,
    retry_evaluator: Any | None = None,
    model_attempt_service: Any,
    prompt_registry: Any,
) -> str:
    from host_brain.model_runtime.contracts import (  # noqa: PLC0415
        ModelAttemptRequest,
        OutputContract,
        PromptRef,
    )
    from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
        PERSONA_INNER_ACTIVITY_PROMPT_ID,
    )
    from host_brain.model_runtime.trace import (  # noqa: PLC0415
        model_attempt_result_trace,
    )

    request_id = f"inner_activity::{uuid.uuid4().hex}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]
    prompt_ref = PromptRef(
        prompt_id=PERSONA_INNER_ACTIVITY_PROMPT_ID,
        version="v1.0.0",
        variables={"messages": tuple(messages)},
        output_contract=OutputContract(kind="json_object"),
    )
    prompt_registry.render(prompt_ref)
    result = model_attempt_service.complete_sync(
        ModelAttemptRequest(
            request_id=request_id,
            scene_type="INNER_ACTIVITY",
            cognitive_tier="B",
            prompt=prompt_ref,
            deadline_ms=max(1, int(timeout_ms)),
            foreground=False,
            retry_evaluator=retry_evaluator,
            metadata={"caller": "inner_activity_agent"},
        )
    )
    if model_attempt_sink is not None:
        model_attempt_sink.clear()
        model_attempt_sink.update(model_attempt_result_trace(result))
    if not result.success:
        raise RuntimeError(
            f"{result.error_code or 'INNER_ACTIVITY_MODEL_ATTEMPT_FAILED'}:"
            f"{result.error_message or 'model attempt failed'}"
        )
    return str(result.content or "").strip()


def _build_context_digest(context: Mapping[str, Any]) -> dict[str, Any]:
    offline_state = _coerce_mapping(context.get("offline_state"))
    motive_state = _coerce_mapping(context.get("motive_state"))
    reply_plan = _coerce_mapping(context.get("reply_plan"))
    return {
        "absence_band": str(offline_state.get("absence_band") or "recent"),
        "offline_hours": float(offline_state.get("offline_hours") or 0.0),
        "loneliness": float(offline_state.get("loneliness_level") or 0.0),
        "anticipation": float(offline_state.get("anticipation_level") or 0.0),
        "dominant_motive": str(motive_state.get("dominant_motive") or ""),
        "policy_withdrawn": bool(reply_plan.get("should_be_withdrawn") or False),
        "recent_inner_events": len(list(context.get("recent_inner_events") or [])),
    }


def _project_mapping(source: Mapping[str, Any] | None, keys: list[str]) -> dict[str, Any]:
    src = _coerce_mapping(source)
    result: dict[str, Any] = {}
    for key in keys:
        if key in src:
            result[key] = src.get(key)
    return result


def _score_inner_activity_note(
    *,
    note: str,
    parsed: Mapping[str, Any] | None,
    inner_life_source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    selected = _coerce_mapping(_coerce_mapping(inner_life_source).get("selected"))
    thought_form = str(_coerce_mapping(parsed).get("thought_form") or selected.get("thought_form") or "").strip()
    source_seed = str(selected.get("seed") or "").strip()
    return score_inner_note_liveliness(note, thought_form=thought_form, source_seed=source_seed)


def _inner_note_quality_rejected(quality: Mapping[str, Any] | None) -> bool:
    item = _coerce_mapping(quality)
    flags = {str(flag or "") for flag in list(item.get("flags") or [])}
    if flags & _INNER_NOTE_HARD_QUALITY_FLAGS:
        return True
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return score < _INNER_NOTE_MIN_LIVELINESS_SCORE


def _inner_note_similarity_reject_threshold() -> float:
    try:
        value = float(os.environ.get("HOST_INNER_ACTIVITY_SIMILARITY_REJECT_THRESHOLD", "0.72"))
    except (TypeError, ValueError):
        value = 0.72
    return max(0.45, min(0.95, value))


def _inner_note_duplicate_reason(note: str, recent_notes: set[str]) -> str:
    value = str(note or "").strip()
    if not value:
        return ""
    if value in recent_notes:
        return "INNER_ACTIVITY_LLM_DUPLICATE"
    if _looks_like_generic_inner_note(value):
        return "INNER_ACTIVITY_LLM_GENERIC_FILLER"
    value_key = _normalize_inner_note_key(value)
    if not value_key:
        return ""
    threshold = _inner_note_similarity_reject_threshold()
    for recent in recent_notes:
        recent_key = _normalize_inner_note_key(recent)
        if not recent_key:
            continue
        if _inner_note_similarity(value_key, recent_key) >= threshold:
            return "INNER_ACTIVITY_LLM_SEMANTIC_DUPLICATE"
    return ""


def _normalize_inner_note_key(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"[，,。.!！?？;；：:\s\"“”'‘’、…~～\-—_（）()【】\[\]{}<>《》]", "", value)
    for token in ("我", "有点", "一点", "一下", "这边", "那边", "还是", "只是", "已经", "现在"):
        value = value.replace(token, "")
    return value[:96]


def _inner_note_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_bigrams = {left[index : index + 2] for index in range(max(1, len(left) - 1))}
    right_bigrams = {right[index : index + 2] for index in range(max(1, len(right) - 1))}
    if not left_bigrams or not right_bigrams:
        return 0.0
    return len(left_bigrams & right_bigrams) / max(1, len(left_bigrams | right_bigrams))


def _coerce_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw if raw not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def _coerce_non_negative_int(raw: Any) -> int:
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def _finalize_inner_note_text(raw_text: str) -> str:
    text = _TEXT_WHITESPACE_RE.sub(" ", str(raw_text or "").strip())
    if not text:
        return ""
    if _looks_like_abstract_inner_note(text):
        return ""
    text = _TEXT_QUESTION_RE.sub("。", text)
    text = text.strip("，,;； ")
    if not text:
        return ""
    if text[-1] not in {"。", "！", "!", "."}:
        text = f"{text}。"
    if len(text) > 96:
        text = text[:96].rstrip("，,;；。.!！") + "。"
    return text


def _looks_like_abstract_inner_note(text: str) -> bool:
    value = str(text or "")
    return (
        any(term in value for term in _ABSTRACT_INNER_NOTE_TERMS)
        or _looks_like_directive_inner_note(value)
        or _looks_like_overwritten_inner_note(value)
        or _looks_like_meta_inner_note(value)
        or _looks_like_generic_inner_note(value)
    )


def _looks_like_directive_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if _DIRECTIVE_INNER_NOTE_OPENING_RE.match(value):
        return True
    return any(phrase in value for phrase in _DIRECTIVE_INNER_NOTE_PHRASES)


def _looks_like_overwritten_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    return any(phrase in value for phrase in _OVERWRITTEN_INNER_NOTE_PHRASES)


def _looks_like_meta_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    return any(phrase in value for phrase in _META_INNER_NOTE_PHRASES)


def _looks_like_generic_inner_note(text: str) -> bool:
    value = str(text or "").strip()
    return any(phrase in value for phrase in _GENERIC_INNER_NOTE_PHRASES)


def _infer_focus_from_note(note: str) -> str:
    text = str(note or "").strip()
    if not text:
        return "self"
    if any(token in text for token in ("抱歉", "不对", "误会", "修", "收住")):
        return "repair"
    if any(token in text for token in ("担心", "在意", "等你", "回来", "陪", "别硬撑")):
        return "relationship"
    if any(token in text for token in ("记得", "还记", "旧", "上次")):
        return "memory"
    if any(token in text for token in ("下次", "计划", "收口", "节奏", "优先")):
        return "plan"
    if any(token in text for token in ("情绪", "状态", "稳定", "疲惫", "心绪")):
        return "state"
    if any(token in text for token in ("话题", "重连", "继续", "上次")):
        return "topic"
    return "self"


__all__ = [
    "extract_llm_json_payload",
    "generate_inner_activity_note",
]
