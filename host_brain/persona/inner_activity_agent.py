from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from host_brain.persona.motive_runtime import (
    build_offline_state_from_inner_state,
    compute_runtime_motive_state,
)

_logger = logging.getLogger("Natsume.inner_activity_agent")

_ALLOWED_FOCUS = {"self", "state", "plan", "topic"}
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
)

_INNER_ACTIVITY_SYSTEM_PROMPT = (
    "Output one JSON object only. You generate a private inner monologue note, "
    "not user-visible chat and not a state summary. Fields: note, focus. "
    "note must be a concrete short Chinese thought that could occur inside her mind, "
    "with a small intention or concern for the next response. Do not use abstract system words "
    "like 自我连续性, 连续性修正, 文本自然度, 模板感, 内部状态, 控制包, 模块, 模型, prompt. "
    "Do not write process-talk like 这句, 下一句, 句子, 文本, 模板, 接住, 生成, 回复, 说顺. "
    "If text quality matters, think from her self and relationship, e.g. 别急着装可爱，先把他在意的地方弄清楚。 "
    "focus is one of self, state, plan, topic."
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
_INNER_ACTIVITY_CODEX_CLI_PROVIDER = "codex_cli"
_INNER_ACTIVITY_RESULT_CACHE_LOCK = threading.Lock()
_INNER_ACTIVITY_RESULT_CACHE: dict[str, tuple[int, str]] = {}
_INNER_ACTIVITY_DIRECT_CALL_LOCK = threading.Lock()
_INNER_ACTIVITY_LAST_DIRECT_CALL_MS = 0
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
    if explicit is not None and not _env_bool("HOST_INNER_ACTIVITY_LLM_ENABLED", default=False):
        return "INNER_ACTIVITY_LLM_DISABLED"
    if not _env_bool("NATSUME_INNER_WORLD_LLM_ENABLED", default=True):
        return "INNER_ACTIVITY_LLM_DISABLED"
    return "INNER_ACTIVITY_LLM_DISABLED"


def _inner_activity_llm_enabled() -> bool:
    if not _env_bool("HOST_CONTINUOUS_ORGAN_TICKS_ENABLED", default=True):
        return False
    explicit = os.environ.get("HOST_INNER_ACTIVITY_LLM_ENABLED")
    if explicit is not None:
        return _env_bool("HOST_INNER_ACTIVITY_LLM_ENABLED", default=False)
    return _env_bool("NATSUME_INNER_WORLD_LLM_ENABLED", default=True)


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
    if not _inner_activity_llm_enabled():
        return "", "", _inner_activity_llm_disabled_reason(), {}

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
    if not api_key and not _inner_activity_provider_uses_codex_cli(provider_name):
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
        "reply_plan",
        "self_model_snapshot",
        "self_reflection_pulse",
        "recent_inner_events",
    ]
    context_digest = _build_context_digest(context)
    recent_notes = {
        str(item.get("note") or "").strip()
        for item in list(context.get("recent_inner_events") or [])
        if isinstance(item, Mapping)
    }

    system_prompt = _INNER_ACTIVITY_SYSTEM_PROMPT
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
            "use_self_model_as_thought_source": True,
            "prefer_active_intention_or_unresolved_tension": True,
            "must_not_contradict_self_boundaries": True,
            "must_be_concrete_private_thought": True,
            "prefer_concrete_next_intention": True,
            "avoid_abstract_system_terms": list(_ABSTRACT_INNER_NOTE_TERMS),
        },
    }
    prompt_text = json.dumps(prompt_payload, ensure_ascii=False)
    gen_params = dict(runtime.get("agent_gen_params") or {"temperature": 0.62, "top_p": 0.9})

    try:
        raw_text = _run_inner_activity_llm(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            base_url_override=base_url_override,
            gen_params=gen_params,
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
    if note in recent_notes:
        retry_result = _retry_inner_activity_llm_after_duplicate(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            base_url_override=base_url_override,
            system_prompt=system_prompt,
            prompt_payload=prompt_payload,
            recent_notes=recent_notes,
            gen_params=gen_params,
        )
        retry_note = str(retry_result.get("note") or "").strip()
        if retry_note:
            meta = {
                "context_scope": context_scope,
                "context_digest": context_digest,
                "provider_name": provider_name,
                "model_id": model_id,
                "pool_bypass": bool(runtime.get("pool_bypass")),
                "retry_count": 1,
                "retry_reason": "duplicate_recent_inner_event",
            }
            return retry_note, str(retry_result.get("focus") or "self"), "", meta
        return "", "", str(retry_result.get("reason") or "INNER_ACTIVITY_LLM_DUPLICATE"), {"context_digest": context_digest}

    meta = {
        "context_scope": context_scope,
        "context_digest": context_digest,
        "provider_name": provider_name,
        "model_id": model_id,
        "pool_bypass": bool(runtime.get("pool_bypass")),
    }
    return note, focus or "self", "", meta


def _retry_inner_activity_llm_after_duplicate(
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    base_url_override: str | None,
    system_prompt: str,
    prompt_payload: Mapping[str, Any],
    recent_notes: set[str],
    gen_params: Mapping[str, Any],
) -> dict[str, str]:
    retry_payload = dict(prompt_payload)
    retry_payload["retry"] = {
        "reason": "previous_output_repeated_recent_inner_event",
        "must_avoid_exact_notes": sorted(recent_notes),
        "must_choose_fresh_angle": True,
        "fresh_angle_examples": [
            "换成对下一次回应的具体担心",
            "换成对未完话题的短促在意",
            "换成对自己别逞强的提醒",
        ],
    }
    retry_params = dict(gen_params or {})
    try:
        retry_params["temperature"] = min(0.86, float(retry_params.get("temperature") or 0.62) + 0.12)
    except Exception:
        retry_params["temperature"] = 0.74
    try:
        raw_retry = _run_inner_activity_llm(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=json.dumps(retry_payload, ensure_ascii=False),
            system_prompt=system_prompt,
            base_url_override=base_url_override,
            gen_params=retry_params,
            allow_burst=True,
        )
    except Exception as exc:
        return {"reason": f"INNER_ACTIVITY_LLM_DUPLICATE_RETRY_FAILED:{str(exc)[:80]}"}

    parsed = extract_llm_json_payload(raw_retry)
    note = _finalize_inner_note_text(str(parsed.get("note") or ""))
    focus = str(parsed.get("focus") or "").strip().lower()
    if focus not in _ALLOWED_FOCUS:
        focus = _infer_focus_from_note(note)
    if not note:
        return {"reason": "INNER_ACTIVITY_LLM_DUPLICATE_RETRY_EMPTY"}
    if note in recent_notes:
        return {"reason": "INNER_ACTIVITY_LLM_DUPLICATE"}
    return {"note": note, "focus": focus or "self"}


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


def _collect_silence_state(*, topic_phrase: str) -> dict[str, Any]:
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        return dict(get_limbic_agent().resolve_silence_state(current_topic=str(topic_phrase or "")))
    except Exception as exc:
        _logger.debug("inner_activity silence_state degraded: %s", exc)
        return {}


def _collect_body_sense() -> dict[str, Any]:
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        return dict(get_limbic_agent().resolve_body_sense_state())
    except Exception as exc:
        _logger.debug("inner_activity body_sense degraded: %s", exc)
        return {}


def _collect_bond_context(*, persona_user_id: str) -> tuple[dict[str, Any], Any | None]:
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


def _collect_emotion_timeline() -> tuple[dict[str, Any], dict[str, Any], str]:
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
    provider_name: str,
    model_id: str,
    api_key: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    gen_params: dict[str, Any],
    base_url_override: str | None,
    allow_burst: bool = False,
) -> str:
    if _inner_activity_provider_uses_codex_cli(provider_name):
        return _run_inner_activity_codex_cli(
            provider_name=provider_name,
            model_id=model_id,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=gen_params,
            allow_burst=allow_burst,
        )

    from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
        _pop_last_provider_usage,
        _set_last_provider_usage,
        invoke_llm_text,
    )

    request_id = f"inner_activity_codex::{int(time.time() * 1000)}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]
    call_params = dict(gen_params or {})
    call_params.setdefault("response_format", "json_object")
    cache_key = _inner_activity_result_cache_key(
        provider_name=provider_name,
        model_id=model_id,
        base_url_override=base_url_override,
        messages=messages,
        gen_params=call_params,
    )
    cached_text = _get_cached_inner_activity_result(cache_key)
    if cached_text is not None:
        return cached_text

    if not allow_burst:
        remaining_ms = _reserve_inner_activity_direct_call_slot()
        if remaining_ms > 0:
            raise RuntimeError(f"INNER_ACTIVITY_CODEX_RATE_LIMITED:{remaining_ms}")

    _set_last_provider_usage(None)
    try:
        raw_text = invoke_llm_text(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=messages,
            gen_params=call_params,
            base_url_override=base_url_override,
        )
        provider_usage = _pop_last_provider_usage()
    except Exception as exc:
        provider_usage = _pop_last_provider_usage()
        _log_inner_activity_direct_usage(
            request_id=request_id,
            provider_name=provider_name,
            model_id=model_id,
            messages=messages,
            content="",
            status="FAILED",
            provider_usage=provider_usage,
        )
        raise exc

    content = str(raw_text or "").strip()
    _put_cached_inner_activity_result(cache_key, content)
    _log_inner_activity_direct_usage(
        request_id=request_id,
        provider_name=provider_name,
        model_id=model_id,
        messages=messages,
        content=content,
        status="OK",
        provider_usage=provider_usage,
    )
    return content


def _inner_activity_provider_uses_codex_cli(provider_name: str) -> bool:
    normalized = str(provider_name or "").strip().lower()
    return normalized in {_INNER_ACTIVITY_CODEX_CLI_PROVIDER, "codex", "codex-cli", "cli"}


def _run_inner_activity_codex_cli(
    *,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    prompt_text: str,
    system_prompt: str,
    gen_params: Mapping[str, Any],
    allow_burst: bool,
) -> str:
    request_id = f"inner_activity_codex_cli::{int(time.time() * 1000)}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt_text},
    ]
    cli_prompt = _build_inner_activity_codex_cli_prompt(
        system_prompt=system_prompt,
        prompt_text=prompt_text,
        gen_params=gen_params,
    )
    cache_key = _inner_activity_result_cache_key(
        provider_name=provider_name,
        model_id=model_id,
        base_url_override="codex-cli",
        messages=messages,
        gen_params={"transport": "codex_cli", **dict(gen_params or {})},
    )
    cached_text = _get_cached_inner_activity_result(cache_key)
    if cached_text is not None:
        return cached_text

    if not allow_burst:
        remaining_ms = _reserve_inner_activity_direct_call_slot()
        if remaining_ms > 0:
            raise RuntimeError(f"INNER_ACTIVITY_CODEX_CLI_RATE_LIMITED:{remaining_ms}")

    status = "FAILED"
    content = ""
    try:
        from host_brain.integrations.codex_cli_provider import invoke_codex_cli_text  # noqa: PLC0415

        content = invoke_codex_cli_text(
            model_id=model_id,
            messages=[{"role": "user", "content": cli_prompt}],
            timeout_ms=timeout_ms,
            response_format={"type": "json_object"},
            feature_key="inner_activity",
        ).strip()
        if not content:
            raise RuntimeError("INNER_ACTIVITY_CODEX_CLI_EMPTY")
        status = "OK"
        _put_cached_inner_activity_result(cache_key, content)
        return content
    finally:
        _log_inner_activity_direct_usage(
            request_id=request_id,
            provider_name=_INNER_ACTIVITY_CODEX_CLI_PROVIDER,
            model_id=model_id,
            messages=messages,
            content=content,
            status=status,
            provider_usage=None,
        )


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
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ],
        gen_params=gen_params,
        base_url_override=base_url_override,
    )


def _resolve_inner_activity_codex_cli_command() -> str | None:
    raw = str(os.environ.get("HOST_INNER_ACTIVITY_CODEX_CLI_PATH") or os.environ.get("HOST_CODEX_CLI_PATH") or "codex").strip()
    if not raw:
        return None
    if raw.lower() == "codex" and not os.environ.get("HOST_INNER_ACTIVITY_CODEX_CLI_PATH") and not os.environ.get("HOST_CODEX_CLI_PATH"):
        try:
            from host_brain.integrations.codex_cli_provider import codex_cli_command  # noqa: PLC0415

            resolved_provider_cmd = codex_cli_command()
            if resolved_provider_cmd:
                return str(resolved_provider_cmd)
        except Exception:
            pass
    if Path(raw).exists():
        return raw
    if raw.lower() == "codex":
        cmd = shutil.which("codex.cmd") or shutil.which("codex")
        if cmd:
            return str(cmd)
    resolved = shutil.which(raw)
    return str(resolved or "").strip() or None


def _resolve_inner_activity_codex_cli_workspace() -> Path:
    raw = str(os.environ.get("HOST_INNER_ACTIVITY_CODEX_CLI_WORKSPACE") or os.environ.get("NATSUME_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def _build_inner_activity_codex_cli_command(
    *,
    codex_cmd: str,
    model_id: str,
    workspace: Path,
    last_message_path: Path,
) -> list[str]:
    command = [str(codex_cmd), "exec"]
    if _env_bool("HOST_INNER_ACTIVITY_CODEX_CLI_DISABLE_PLUGINS", default=True):
        command.extend(["--disable", "plugins"])
    if _env_bool("HOST_INNER_ACTIVITY_CODEX_CLI_DISABLE_SHELL_SNAPSHOT", default=True):
        command.extend(["--disable", "shell_snapshot"])
    if _env_bool("HOST_INNER_ACTIVITY_CODEX_CLI_FAST_MODE", default=True):
        command.extend(["--enable", "fast_mode"])
    model = str(model_id or "").strip()
    if model:
        command.extend(["-m", model])
    reasoning_effort = str(
        os.environ.get("HOST_INNER_ACTIVITY_CODEX_CLI_REASONING_EFFORT")
        or os.environ.get("HOST_CODEX_RUNTIME_REASONING_EFFORT")
        or "minimal"
    ).strip()
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    command.extend(
        [
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "-C",
            str(workspace),
            "-o",
            str(last_message_path),
            "-",
        ]
    )
    if sys.platform == "win32" and str(codex_cmd).strip().lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/d", "/s", "/c", *command]
    return command


def _build_inner_activity_codex_cli_env() -> dict[str, str]:
    env = dict(os.environ)
    if _env_bool("HOST_INNER_ACTIVITY_CODEX_CLI_STRIP_API_KEYS", default=True):
        for key in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
            "MINIMAX_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            env.pop(key, None)
    return env


def _run_inner_activity_codex_cli_process(
    command: list[str],
    *,
    timeout: float,
    input_text: str,
    workspace: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(workspace),
        env=env,
        creationflags=_subprocess_creationflags(),
    )
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=max(1.0, float(timeout)))
    except subprocess.TimeoutExpired as exc:
        _terminate_inner_activity_process_tree(proc, command=command)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        detail = _compact_process_detail(stdout, stderr)
        raise TimeoutError(f"INNER_ACTIVITY_CODEX_CLI_TIMEOUT:{int(timeout * 1000)}:{detail}") from exc
    return subprocess.CompletedProcess(command, int(proc.returncode or 0), stdout=stdout, stderr=stderr)


def _terminate_inner_activity_process_tree(proc: subprocess.Popen[str], *, command: list[str] | None = None) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=_subprocess_creationflags(),
            )
            marker = _codex_cli_command_marker(command or [])
            if marker:
                marker = marker.replace("'", "''")
                ps = (
                    "$marker = '"
                    + marker
                    + "'; "
                    + "Get-CimInstance Win32_Process | "
                    + "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($marker) -and ($_.Name -eq 'node.exe' -or $_.Name -like 'codex*.exe') } | "
                    + "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                    creationflags=_subprocess_creationflags(),
                )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _codex_cli_command_marker(command: list[str]) -> str:
    try:
        if "-o" in command:
            return str(Path(command[command.index("-o") + 1]).parent)
    except Exception:
        return ""
    return ""


def _build_inner_activity_codex_cli_prompt(
    *,
    system_prompt: str,
    prompt_text: str,
    gen_params: Mapping[str, Any],
) -> str:
    max_tokens = int(dict(gen_params or {}).get("max_output_tokens") or dict(gen_params or {}).get("max_tokens") or 180)
    return "\n".join(
        [
            "You are a local Codex CLI subagent for Natsume's private inner activity.",
            "Do not inspect files, do not run shell commands, do not edit anything, and do not explain your process.",
            "Return exactly one compact JSON object and no Markdown.",
            "Schema: {\"note\":\"short concrete Chinese private thought\",\"focus\":\"self|state|plan|topic\"}.",
            f"Keep note under {max(32, min(220, max_tokens))} Chinese characters.",
            "",
            "System constraints:",
            str(system_prompt or ""),
            "",
            "Input payload:",
            str(prompt_text or ""),
        ]
    )


def _read_codex_cli_last_message(path: Path, *, stdout: str) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return _extract_codex_cli_message_from_stdout(stdout)


def _extract_codex_cli_message_from_stdout(stdout: str) -> str:
    messages: list[str] = []
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if not isinstance(payload, Mapping):
            continue
        for key in ("message", "content", "text", "last_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                messages.append(value.strip())
    return messages[-1] if messages else ""


def _compact_process_detail(*values: Any) -> str:
    text = "\n".join(str(value or "").strip() for value in values if str(value or "").strip())
    text = _TEXT_WHITESPACE_RE.sub(" ", text).strip()
    return text[:480]


def _subprocess_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _inner_activity_result_cache_ttl_ms() -> int:
    return _env_int(
        "HOST_INNER_ACTIVITY_RESULT_CACHE_TTL_MS",
        default=600_000,
        minimum=0,
        maximum=30 * 60 * 1000,
    )


def _inner_activity_direct_min_interval_ms() -> int:
    return _env_int(
        "HOST_INNER_ACTIVITY_CODEX_MIN_INTERVAL_MS",
        default=600_000,
        minimum=0,
        maximum=30 * 60 * 1000,
    )


def _inner_activity_result_cache_key(
    *,
    provider_name: str,
    model_id: str,
    base_url_override: str | None,
    messages: list[Mapping[str, Any]],
    gen_params: Mapping[str, Any],
) -> str:
    payload = {
        "provider_name": str(provider_name or "").strip().lower(),
        "model_id": str(model_id or "").strip(),
        "base_url_override": str(base_url_override or "").strip(),
        "messages": [dict(item) for item in list(messages or []) if isinstance(item, Mapping)],
        "gen_params": dict(gen_params or {}),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()


def _get_cached_inner_activity_result(cache_key: str) -> str | None:
    ttl_ms = _inner_activity_result_cache_ttl_ms()
    if ttl_ms <= 0:
        return None
    now_value = _now_ms()
    with _INNER_ACTIVITY_RESULT_CACHE_LOCK:
        item = _INNER_ACTIVITY_RESULT_CACHE.get(str(cache_key or ""))
        if not item:
            return None
        expires_at_ms, content = item
        if int(expires_at_ms) <= now_value:
            _INNER_ACTIVITY_RESULT_CACHE.pop(str(cache_key or ""), None)
            return None
        return str(content or "")


def _put_cached_inner_activity_result(cache_key: str, content: str) -> None:
    ttl_ms = _inner_activity_result_cache_ttl_ms()
    if ttl_ms <= 0:
        return
    with _INNER_ACTIVITY_RESULT_CACHE_LOCK:
        _INNER_ACTIVITY_RESULT_CACHE[str(cache_key or "")] = (_now_ms() + ttl_ms, str(content or ""))


def _reserve_inner_activity_direct_call_slot() -> int:
    global _INNER_ACTIVITY_LAST_DIRECT_CALL_MS
    min_interval_ms = _inner_activity_direct_min_interval_ms()
    if min_interval_ms <= 0:
        return 0
    now_value = _now_ms()
    with _INNER_ACTIVITY_DIRECT_CALL_LOCK:
        remaining_ms = int(_INNER_ACTIVITY_LAST_DIRECT_CALL_MS + min_interval_ms - now_value)
        if remaining_ms > 0:
            return remaining_ms
        _INNER_ACTIVITY_LAST_DIRECT_CALL_MS = now_value
        return 0


def _log_inner_activity_direct_usage(
    *,
    request_id: str,
    provider_name: str,
    model_id: str,
    messages: list[Mapping[str, Any]],
    content: str,
    status: str,
    provider_usage: Mapping[str, Any] | None,
) -> None:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.observability.token_usage import build_llm_token_usage_row  # noqa: PLC0415

        ledger = get_ledger()
        if not hasattr(ledger, "log_llm_token_usage"):
            return
        ledger.log_llm_token_usage(
            build_llm_token_usage_row(
                request_id=request_id,
                scene_type="INNER_ACTIVITY",
                cognitive_tier="B",
                provider=provider_name,
                model_id=model_id,
                messages=messages,
                content=content,
                attempts=[{"status": status, "provider": provider_name, "model": model_id}],
                cached=False,
                pool_exhausted=False,
                ts_ms=_now_ms(),
                provider_usage=provider_usage,
            )
        )
    except Exception as exc:
        _logger.debug("inner_activity direct token usage log skipped: %s", exc)


def _resolve_inner_activity_llm_runtime(*, now_ms: int) -> dict[str, Any] | None:
    cache_now = int(now_ms)
    with _INNER_ACTIVITY_LLM_CACHE_LOCK:
        resolved_at = int(_INNER_ACTIVITY_LLM_CACHE.get("resolved_at_ms") or 0)
        cached_runtime = _INNER_ACTIVITY_LLM_CACHE.get("runtime")
        if cached_runtime and (cache_now - resolved_at) <= _INNER_ACTIVITY_LLM_CACHE_TTL_MS:
            return dict(cached_runtime) if isinstance(cached_runtime, Mapping) else None

    runtime = _resolve_inner_activity_direct_runtime()
    with _INNER_ACTIVITY_LLM_CACHE_LOCK:
        _INNER_ACTIVITY_LLM_CACHE["resolved_at_ms"] = cache_now
        _INNER_ACTIVITY_LLM_CACHE["runtime"] = dict(runtime) if isinstance(runtime, Mapping) else None
    return dict(runtime) if isinstance(runtime, Mapping) else None


def _resolve_inner_activity_direct_runtime() -> dict[str, Any] | None:
    try:
        provider_name = str(
            os.environ.get("HOST_INNER_ACTIVITY_AGENT_PROVIDER")
            or os.environ.get("HOST_INNER_ACTIVITY_LLM_PROVIDER")
            or _INNER_ACTIVITY_CODEX_CLI_PROVIDER
        ).strip().lower()
        model_id = str(
            os.environ.get("HOST_INNER_ACTIVITY_AGENT_MODEL_ID")
            or os.environ.get("HOST_INNER_ACTIVITY_CODEX_CLI_MODEL")
            or os.environ.get("HOST_INNER_ACTIVITY_CODEX_MODEL_ID")
            or (os.environ.get("HOST_CODEX_RUNTIME_MODEL") if _inner_activity_provider_uses_codex_cli(provider_name) else "")
            or (os.environ.get("HOST_CODEX_MODEL_ID") if _inner_activity_provider_uses_codex_cli(provider_name) else "")
            or os.environ.get("HOST_INNER_WORLD_LLM_MODEL_ID")
            or ("gpt-5.4-mini" if _inner_activity_provider_uses_codex_cli(provider_name) else "")
        ).strip()
        timeout_ms = _env_int(
            "HOST_INNER_ACTIVITY_CODEX_CLI_TIMEOUT_MS",
            default=25_000,
            minimum=5_000,
            maximum=2 * 60 * 1000,
        )
        codex_cmd = _resolve_inner_activity_codex_cli_command() if _inner_activity_provider_uses_codex_cli(provider_name) else None
        available = bool(provider_name and model_id and codex_cmd)
        return {
            "available": available,
            "provider_name": provider_name,
            "model_id": model_id,
            "chat_model_id": model_id,
            "llm_api_key": "",
            "base_url_override": None,
            "timeout_ms": timeout_ms,
            "agent_gen_params": {"temperature": 0.62, "top_p": 0.9, "max_output_tokens": 180},
            "route_reason_code": "INNER_ACTIVITY_CODEX_CLI" if available else "INNER_ACTIVITY_CODEX_CLI_UNAVAILABLE",
            "pool_bypass": True,
            "codex_cli_command": codex_cmd,
        }
    except Exception as exc:
        _logger.debug("inner_activity direct llm runtime resolve failed: %s", exc)
        return None


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


def _coerce_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw) if isinstance(raw, Mapping) else {}


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
    return any(term in value for term in _ABSTRACT_INNER_NOTE_TERMS)


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
