from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

from host_brain.persona.motive_runtime import (
    build_offline_state_from_inner_state,
    compute_runtime_motive_state,
)

_logger = logging.getLogger("Natsume.inner_activity_agent")

_ALLOWED_FOCUS = {"self", "state", "plan", "topic"}
_TEXT_WHITESPACE_RE = re.compile(r"\s+")
_TEXT_QUESTION_RE = re.compile(r"[?？]+")

_INNER_ACTIVITY_LLM_ENABLED = (
    str(os.environ.get("NATSUME_INNER_WORLD_LLM_ENABLED", "true")).strip().lower()
    not in {"0", "false", "no", "off"}
)
_INNER_ACTIVITY_LLM_TIMEOUT_MS = max(
    800,
    int(float(os.environ.get("NATSUME_INNER_WORLD_LLM_TIMEOUT_S", "15")) * 1000),
)
_INNER_ACTIVITY_LLM_CACHE_TTL_MS = max(
    5_000,
    int(float(os.environ.get("NATSUME_INNER_WORLD_LLM_CACHE_TTL_S", "120")) * 1000),
)

_INNER_ACTIVITY_LLM_CACHE_LOCK = threading.Lock()
_INNER_ACTIVITY_LLM_CACHE: dict[str, Any] = {
    "resolved_at_ms": 0,
    "runtime": None,
}
_INNER_ACTIVITY_SELF_REFLECTION_LOCK = threading.Lock()
_INNER_ACTIVITY_SELF_REFLECTION: Any | None = None
_INNER_ACTIVITY_SELF_MODEL_LOCK = threading.Lock()
_INNER_ACTIVITY_SELF_MODEL: Any | None = None


def generate_inner_activity_note(
    *,
    persona_user_id: str,
    absence_band: str,
    offline_hours: float,
    inner_state: Mapping[str, Any] | None,
    now_ms: int,
    topic_phrase: str = "",
) -> tuple[str, str, str, dict[str, Any]]:
    """Generate one inner activity note via standalone inner-activity agent.

    Returns:
        (note, focus, reason, meta)
    """
    if not _INNER_ACTIVITY_LLM_ENABLED:
        return "", "", "INNER_ACTIVITY_LLM_DISABLED", {}

    runtime = _resolve_inner_activity_llm_runtime(now_ms=now_ms)
    if not isinstance(runtime, Mapping):
        return "", "", "INNER_ACTIVITY_LLM_RUNTIME_UNAVAILABLE", {}
    if not bool(runtime.get("available")):
        return "", "", str(runtime.get("route_reason_code") or "INNER_ACTIVITY_LLM_UNAVAILABLE"), {}

    provider_name = str(runtime.get("provider_name") or "").strip()
    model_id = str(runtime.get("model_id") or "").strip()
    api_key = str(runtime.get("llm_api_key") or "").strip()
    base_url_override = str(runtime.get("base_url_override") or "").strip() or None
    timeout_ms = max(
        800,
        min(int(runtime.get("timeout_ms") or _INNER_ACTIVITY_LLM_TIMEOUT_MS), _INNER_ACTIVITY_LLM_TIMEOUT_MS),
    )
    if not provider_name or not model_id:
        return "", "", "INNER_ACTIVITY_LLM_RUNTIME_INVALID", {}
    if not api_key:
        return "", "", "INNER_ACTIVITY_LLM_KEY_MISSING", {}

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
        "emotion_timeline",
        "motive_state",
        "response_policy",
        "self_model_snapshot",
        "self_reflection_pulse",
        "recent_inner_events",
    ]
    context_digest = _build_context_digest(context)

    system_prompt = (
        "你是夏目蓝的内心活动 Agent。"
        "你只写内心独白，不对用户说话，不下指令，不提问。"
        "你必须基于给定的 soul-layer 状态生成一个新的内心片段。"
        "必须优先自我相关思考（self/state/plan），避免重复最近内心事件。"
        "输出必须是 JSON："
        '{"note":"...","focus":"self|state|plan|topic","meta":{"why":"..."}}。'
    )
    prompt_payload = {
        "goal": "generate_inner_activity_note",
        "absence_band": absence_band,
        "offline_hours": round(max(0.0, float(offline_hours)), 3),
        "topic_phrase": str(topic_phrase or ""),
        "context_scope": context_scope,
        "context": context,
        "constraints": {
            "max_chars": 72,
            "no_question": True,
            "no_user_direct_address": True,
            "prefer_self_related": True,
            "avoid_repeat_recent_inner_events": True,
        },
    }
    prompt_text = json.dumps(prompt_payload, ensure_ascii=False)

    try:
        raw_text = _run_inner_activity_llm(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            base_url_override=base_url_override,
            gen_params=dict(runtime.get("agent_gen_params") or {"temperature": 0.62, "top_p": 0.9}),
        )
    except Exception as exc:
        return "", "", f"INNER_ACTIVITY_LLM_ERROR:{str(exc)[:96]}", {"context_digest": context_digest}

    parsed = extract_llm_json_payload(raw_text)
    note = _finalize_inner_note_text(str(parsed.get("note") or ""))
    focus = str(parsed.get("focus") or "").strip().lower()
    if focus not in _ALLOWED_FOCUS:
        focus = _infer_focus_from_note(note)
    if not note:
        return "", "", "INNER_ACTIVITY_LLM_NOTE_EMPTY", {"context_digest": context_digest}

    recent_notes = {
        str(item.get("note") or "").strip()
        for item in list(context.get("recent_inner_events") or [])
        if isinstance(item, Mapping)
    }
    if note in recent_notes:
        return "", "", "INNER_ACTIVITY_LLM_DUPLICATE", {"context_digest": context_digest}

    meta = {
        "context_scope": context_scope,
        "context_digest": context_digest,
        "model_id": model_id,
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

    silence_state = _collect_silence_state(topic_phrase=topic_phrase)
    body_sense = _collect_body_sense()
    bond_summary, bond_state = _collect_bond_context(persona_user_id=persona_user_id)
    timeline_summary, timeline_diary, current_emotion = _collect_emotion_timeline()

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

    response_policy = _decide_response_policy(
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
        "emotion_timeline": {
            "summary": _project_mapping(timeline_summary, ["dominant_emotion", "trend", "total_events", "recent_events"]),
            "diary": _project_mapping(timeline_diary, ["avg_valence", "avg_arousal", "dominant_emotion", "entries"]),
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
        "response_policy": _project_mapping(
            response_policy,
            [
                "max_chars",
                "max_sentences",
                "allow_followup",
                "allow_topic_shift",
                "allow_proactive_after_reply",
                "warmth_floor",
                "warmth_ceiling",
                "intimacy_ceiling",
                "should_be_brief",
                "should_be_withdrawn",
                "policy_reason_tags",
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


def _collect_silence_state(*, topic_phrase: str) -> dict[str, Any]:
    try:
        from host_brain.persona.emotional_silence import get_silence_engine  # noqa: PLC0415

        return dict(get_silence_engine().get_silence_state(current_topic=str(topic_phrase or "")).as_dict())
    except Exception as exc:
        _logger.debug("inner_activity silence_state degraded: %s", exc)
        return {}


def _collect_body_sense() -> dict[str, Any]:
    try:
        from host_brain.persona.body_sense import get_body_sense_model  # noqa: PLC0415

        return dict(get_body_sense_model().get_state().as_dict())
    except Exception as exc:
        _logger.debug("inner_activity body_sense degraded: %s", exc)
        return {}


def _collect_bond_context(*, persona_user_id: str) -> tuple[dict[str, Any], Any | None]:
    try:
        from host_brain.app.dependencies import get_bond_stage_engine  # noqa: PLC0415

        engine = get_bond_stage_engine()
        if engine is None:
            return {}, None
        state = engine.get_state(str(persona_user_id or "user_1").strip() or "user_1")
        if state is None:
            return {}, None
        summary_raw = state.to_summary() if hasattr(state, "to_summary") else {}
        if isinstance(summary_raw, Mapping):
            return dict(summary_raw), state
        return {}, state
    except Exception as exc:
        _logger.debug("inner_activity bond_summary degraded: %s", exc)
        return {}, None


def _collect_emotion_timeline() -> tuple[dict[str, Any], dict[str, Any], str]:
    try:
        from host_brain.app.dependencies import get_emotion_timeline  # noqa: PLC0415

        timeline = get_emotion_timeline()
        if timeline is None:
            return {}, {}, "NEUTRAL"
        summary = timeline.rebuild_summary() if hasattr(timeline, "rebuild_summary") else {}
        diary = timeline.build_daily_diary() if hasattr(timeline, "build_daily_diary") else {}
        summary_dict = dict(summary) if isinstance(summary, Mapping) else {}
        diary_dict = dict(diary) if isinstance(diary, Mapping) else {}
        emotion = str(summary_dict.get("dominant_emotion") or diary_dict.get("dominant_emotion") or "NEUTRAL").upper()
        return summary_dict, diary_dict, emotion
    except Exception as exc:
        _logger.debug("inner_activity emotion_timeline degraded: %s", exc)
        return {}, {}, "NEUTRAL"


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


def _decide_response_policy(
    *,
    silence_state: dict[str, Any],
    body_sense: dict[str, Any],
    offline_state: dict[str, Any],
    bond_summary: dict[str, Any],
    motive_state: dict[str, Any],
) -> dict[str, Any]:
    try:
        from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy  # noqa: PLC0415

        decision = decide_response_policy(
            ResponsePolicyInput(
                silence_state=silence_state,
                body_sense=body_sense,
                offline_state=offline_state,
                bond_summary=bond_summary,
                self_reflection_pulse={},
                emotion_context={},
                motive_state=motive_state,
                memory_persona_signal={},
                user_message="",
                runtime_flags={"scene_type": "INNER_WORLD", "from_inner_activity_agent": True},
            )
        )
        return dict(decision.as_dict())
    except Exception as exc:
        _logger.debug("inner_activity response_policy degraded: %s", exc)
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
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    gen_params: dict[str, Any],
    base_url_override: str | None,
) -> str:
    chunks: list[str] = []
    for chunk in _stream_inner_activity_llm_chunks(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        prompt_text=prompt_text,
        system_prompt=system_prompt,
        gen_params=gen_params,
        base_url_override=base_url_override,
    ):
        if chunk:
            chunks.append(str(chunk))
        if len("".join(chunks)) > 2048:
            break
    return "".join(chunks).strip()


def _stream_inner_activity_llm_chunks(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    gen_params: dict[str, Any],
    base_url_override: str | None,
):
    from host_brain.integrations.llm_helpers import stream_llm_chunks  # noqa: PLC0415

    return stream_llm_chunks(
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        prompt_text=prompt_text,
        system_prompt=system_prompt,
        gen_params=gen_params,
        base_url_override=base_url_override,
    )


def _resolve_inner_activity_llm_runtime(*, now_ms: int) -> dict[str, Any] | None:
    cache_now = int(now_ms)
    with _INNER_ACTIVITY_LLM_CACHE_LOCK:
        resolved_at = int(_INNER_ACTIVITY_LLM_CACHE.get("resolved_at_ms") or 0)
        cached_runtime = _INNER_ACTIVITY_LLM_CACHE.get("runtime")
        if cached_runtime and (cache_now - resolved_at) <= _INNER_ACTIVITY_LLM_CACHE_TTL_MS:
            return dict(cached_runtime) if isinstance(cached_runtime, Mapping) else None

    runtime: dict[str, Any] | None = None
    try:
        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            resolve_chat_agent_llm_runtime,
            resolve_host_llm_runtime,
        )

        base_runtime = resolve_host_llm_runtime(
            rollout_key="inner_activity_agent",
            scene_type="CHAT",
        )
        runtime = resolve_chat_agent_llm_runtime(base_runtime=base_runtime)
        model_override = str(
            os.environ.get("HOST_INNER_ACTIVITY_AGENT_MODEL_ID")
            or os.environ.get("HOST_INNER_WORLD_LLM_MODEL_ID")
            or os.environ.get("HOST_DISPATCH_AGENT_MODEL_ID")
            or "gemini-2.5-flash"
        ).strip()
        runtime["model_id"] = model_override
        runtime["chat_model_id"] = model_override
        runtime["agent_gen_params"] = {"temperature": 0.62, "top_p": 0.9}
    except Exception as exc:
        _logger.debug("inner_activity llm runtime resolve failed: %s", exc)
        runtime = None

    with _INNER_ACTIVITY_LLM_CACHE_LOCK:
        _INNER_ACTIVITY_LLM_CACHE["resolved_at_ms"] = cache_now
        _INNER_ACTIVITY_LLM_CACHE["runtime"] = dict(runtime) if isinstance(runtime, Mapping) else None
    return dict(runtime) if isinstance(runtime, Mapping) else None


def _build_context_digest(context: Mapping[str, Any]) -> dict[str, Any]:
    offline_state = _coerce_mapping(context.get("offline_state"))
    motive_state = _coerce_mapping(context.get("motive_state"))
    response_policy = _coerce_mapping(context.get("response_policy"))
    return {
        "absence_band": str(offline_state.get("absence_band") or "recent"),
        "offline_hours": float(offline_state.get("offline_hours") or 0.0),
        "loneliness": float(offline_state.get("loneliness_level") or 0.0),
        "anticipation": float(offline_state.get("anticipation_level") or 0.0),
        "dominant_motive": str(motive_state.get("dominant_motive") or ""),
        "policy_withdrawn": bool(response_policy.get("should_be_withdrawn") or False),
        "recent_inner_events": len(list(context.get("recent_inner_events") or [])),
    }


def _project_mapping(source: Mapping[str, Any] | None, keys: list[str]) -> dict[str, Any]:
    src = _coerce_mapping(source)
    result: dict[str, Any] = {}
    for key in keys:
        if key in src:
            result[key] = src.get(key)
    return result


def _coerce_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, Mapping) else {}


def _finalize_inner_note_text(raw_text: str) -> str:
    text = _TEXT_WHITESPACE_RE.sub(" ", str(raw_text or "").strip())
    if not text:
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


def _infer_focus_from_note(note: str) -> str:
    text = str(note or "").strip()
    if not text:
        return "self"
    if any(token in text for token in ("下次", "计划", "收口", "节奏", "优先")):
        return "plan"
    if any(token in text for token in ("情绪", "状态", "稳定", "疲惫", "心绪")):
        return "state"
    if any(token in text for token in ("话题", "重连", "继续", "上次")):
        return "topic"
    return "self"


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "extract_llm_json_payload",
    "generate_inner_activity_note",
]
