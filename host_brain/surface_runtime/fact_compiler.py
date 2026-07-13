"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from host_brain.persona.inner_monologue import build_inner_weather_hint, InnerMonologueDigest
from collections.abc import AsyncGenerator, Mapping, Sequence
import os
import re

_AUDIO_INPUT_SOURCES = {"asr", "audio", "voice", "speech", "mic", "microphone", "voice_chat"}
_ACCOUNT_LOGIN_TASK_RE = re.compile(
    r"(登录|账号|账户|积分|轻币|购买|买|epub|电子书|lightnovel|轻小说)",
    re.IGNORECASE,
)
_ACCOUNT_CREDENTIAL_REQUEST_RE = re.compile(
    r"((账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password)"
    r".{0,16}(丢|发|给|贴|提供|传|告诉|send|paste|provide)"
    r"|(丢|发|给|贴|提供|传|告诉|send|paste|provide).{0,16}"
    r"(账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password))",
    re.IGNORECASE,
)
from host_brain.surface_runtime.life_context import (
    _surface_life_context_task_facts,
    _surface_memory_acceptance_must_not_do,
    _surface_memory_acceptance_task_facts,
    _surface_memory_write_task_facts,
)
from host_brain.surface_runtime.memory_context import (
    _surface_memory_context_orchestrator_facts,
    _surface_memory_context_orchestrator_must_not_do,
)
from host_brain.surface_runtime.quality_guards import (
    _is_technical_task,
    _surface_dream_context_task_facts,
    _surface_recent_conversation_window_facts,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_result_fact_lines,
)
from host_brain.surface_runtime.ports import SurfaceRuntimePorts


def _task_facts(
    *,
    reply_plan: Mapping[str, Any],
    agent_input: Any | None = None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    facts = [
        *_surface_input_boundary_facts(agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        *_surface_recent_conversation_window_facts(agent_input, ports=ports),
        *_surface_dialogue_repeat_task_facts(reply_plan),
        *_surface_memory_context_orchestrator_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_memory_write_task_facts(agent_input),
        *_surface_memory_acceptance_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_dream_context_task_facts(agent_input, ports=ports),
        *_surface_life_context_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        f"当前任务：{_first_nonempty(reply_plan.get('reply_contract'), reply_plan.get('response_goal'), '')}",
        f"当前意图：{_first_nonempty(reply_plan.get('task_mode'), reply_plan.get('primary_topic'), reply_plan.get('interaction_move'), '')}",
        *_technical_task_facts(reply_plan),
    ]
    return tuple(_clean_list(facts))


def _surface_reply_plan_repeat_count(reply_plan: Mapping[str, Any] | None) -> int:
    plan = _safe_mapping(reply_plan)
    raw = plan.get("repeat_question_count")
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    for item in list(plan.get("reason_tags") or []):
        text = str(item or "").strip()
        match = re.match(r"repeat_count:(\d+)$", text)
        if match:
            try:
                return max(0, int(match.group(1)))
            except ValueError:
                return 0
    return 0


def _surface_reply_plan_repeat_group(reply_plan: Mapping[str, Any] | None) -> str:
    plan = _safe_mapping(reply_plan)
    value = str(plan.get("repeat_surface_group") or plan.get("repeat_group") or "").strip()
    if value:
        return value
    for item in list(plan.get("reason_tags") or []):
        text = str(item or "").strip()
        if text.startswith("repeat_surface:"):
            return text.split(":", 1)[1].strip()
    return ""


def _surface_dialogue_repeat_task_facts(reply_plan: Mapping[str, Any] | None) -> tuple[str, ...]:
    repeat_count = _surface_reply_plan_repeat_count(reply_plan)
    if repeat_count <= 0:
        return ()
    repeat_group = _surface_reply_plan_repeat_group(reply_plan)
    group_text = f"；同类输入={repeat_group}" if repeat_group else ""
    return (
        f"重复输入：用户这轮是在重复刚才同类输入，之前已有 {repeat_count} 次{group_text}；本轮必须体现已经注意到重复，不要当第一次处理。",
    )


def _surface_dialogue_repeat_must_not_do(reply_plan: Mapping[str, Any] | None) -> tuple[str, ...]:
    if _surface_reply_plan_repeat_count(reply_plan) <= 0:
        return ()
    return (
        "重复输入时不要假装第一次看到；必须避免只换一种说法重复上一轮。",
        "重复时间错位问候时，不要猜用户睡迷糊、故意逗你、状态异常或现实行为。",
    )


def _necessary_facts(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    task_control: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    facts = [
        *_surface_sub_agent_result_fact_lines(agent_input),
        *_surface_input_boundary_facts(agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        *_surface_recent_conversation_window_facts(agent_input, ports=ports),
        *_surface_memory_context_orchestrator_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_memory_acceptance_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_surface_dream_context_task_facts(agent_input, ports=ports),
        *_surface_life_context_task_facts(
            agent_input=agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        ),
        *_technical_task_facts(task_control or {}),
        *_account_login_handoff_facts(agent_input, task_control=task_control),
    ]
    status = _first_nonempty(retrieval_summary.get("status"), "")
    if status:
        facts.append(f"召回状态：{status}")
    return tuple(_clean_list(facts))


def _surface_input_boundary_facts(
    *,
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if _input_has_audio_evidence(agent_input=agent_input, trace_stage_meta=trace_stage_meta):
        return (
            "当前输入渠道：语音/ASR。",
            "音频输入：有；可以确认收到了语音或语音转写。",
            "不要编造未提供的音色、语气、环境声音、摄像头画面、屏幕画面或现实传感器事实。",
        )
    return (
        "当前输入渠道：中文文字私聊。",
        "当前没有音频、摄像头、屏幕画面或现实传感器事实。",
        "只能回应用户当前文字和压缩事实，不能把文字输入写成听见声音或看见画面。",
    )


def _surface_boundary_must_not_do(
    *,
    agent_input: Any | None = None,
    task_control: Mapping[str, Any] | None = None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if _input_has_audio_evidence(agent_input=agent_input, trace_stage_meta=trace_stage_meta):
        sensory_boundary = "不要编造音色、语气、环境声音、摄像头画面、屏幕画面或现实传感器事实。"
    else:
        sensory_boundary = "不要说“你这声音”“我听出来”“我看到你”或把文字输入写成音频/视觉感知。"
    return tuple(
        _clean_list(
            [
                sensory_boundary,
                "不要编造用户现实动作、地点、屏幕内容、身体状态或睡眠事实，除非 facts 明确给出。",
                *_surface_memory_context_orchestrator_must_not_do(
                    agent_input=agent_input,
                    ports=ports,
                    trace_stage_meta=trace_stage_meta,
                ),
                *_surface_memory_acceptance_must_not_do(agent_input),
                *_technical_task_must_not_do(task_control or {}),
                *_account_login_must_not_do(agent_input, task_control=task_control),
            ]
        )
    )


def _input_has_audio_evidence(
    *,
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> bool:
    sources: list[Any] = []
    containers = (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)) if agent_input is not None else {},
        _safe_mapping(getattr(agent_input, "runtime_payload", None)) if agent_input is not None else {},
    )
    for container in containers:
        sources.extend(
            [
                container.get("input_source"),
                container.get("input_modality"),
                container.get("source_modality"),
                container.get("source"),
            ]
        )
    if agent_input is not None:
        sources.extend(
            [
                getattr(agent_input, "input_source", None),
                getattr(agent_input, "input_modality", None),
                getattr(agent_input, "source_modality", None),
                getattr(agent_input, "source", None),
            ]
        )
        observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
        if observations.get("audio") or observations.get("asr") or observations.get("speech_text"):
            return True
        for attachment in _coerce_sequence(getattr(agent_input, "attachments", None)):
            attachment_map = _safe_mapping(attachment)
            mime_type = str(attachment_map.get("mime_type") or attachment_map.get("content_type") or "").lower()
            media_type = str(attachment_map.get("type") or attachment_map.get("kind") or "").lower()
            if mime_type.startswith("audio/") or media_type in {"audio", "voice", "speech"}:
                return True
    for raw in sources:
        normalized = str(raw or "").strip().lower().replace("-", "_")
        if not normalized:
            continue
        if normalized in _AUDIO_INPUT_SOURCES or "asr" in normalized:
            return True
    return False


def _technical_task_facts(task_control: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not _is_technical_task(task_control or {}):
        return ()
    return (
        "技术场景：先给一条能执行的排查步骤，命令和符号要准确。",
        "技术场景：最终回复至少包含查因和下一步处理，不要只给一条裸命令。",
        "技术场景：当前宿主环境按 Windows/PowerShell 优先给命令，除非用户明确指定 Linux 或 macOS。",
        "技术场景：有用性优先，角色口吻只做轻量包裹。",
    )


def _technical_task_must_not_do(task_control: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not _is_technical_task(task_control or {}):
        return ()
    return (
        "技术回复不要用“干掉”“完事”等粗略说法替代清楚步骤。",
        "不要省略关键命令符号，例如端口筛选要保留冒号。",
        "不要默认给 lsof、grep 等 Linux/macOS 命令，除非用户明确指定该环境。",
        "不要只输出单条命令；命令后要说明下一步如何处理结果。",
    )


def _account_login_sensitive(agent_input: Any | None, *, task_control: Mapping[str, Any] | None = None) -> bool:
    if agent_input is None:
        return False
    sub_agent_ctx = getattr(agent_input, "sub_agent_context", None)
    sub_agent_id = str(getattr(sub_agent_ctx, "agent_id", "") or "").strip()
    if sub_agent_id == "agent_epub_builder":
        return True
    intent = _safe_mapping(getattr(agent_input, "intent_draft", None))
    target_id = str(intent.get("target_id") or intent.get("action") or "").strip()
    if target_id == "agent_epub_builder":
        return True
    text = " ".join(
        str(part or "")
        for part in (
            getattr(agent_input, "user_message", ""),
            _safe_mapping(task_control).get("reply_contract"),
            _safe_mapping(task_control).get("response_goal"),
            _safe_mapping(task_control).get("primary_topic"),
        )
    )
    return bool(_ACCOUNT_LOGIN_TASK_RE.search(text)) and bool(
        re.search(r"(登录|账号|账户|购买|积分|轻币|epub|lightnovel|轻小说)", text, re.IGNORECASE)
    )


def _account_login_handoff_facts(
    agent_input: Any | None,
    *,
    task_control: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if not _account_login_sensitive(agent_input, task_control=task_control):
        return ()
    return (
        "账号类任务：需要用户在本地浏览器完成登录或授权；系统只复用会话状态，不接收明文账号、密码、验证码或 Cookie。",
        "涉及积分/购买：未明确确认书名、链接和最高消耗前，只能生成购买计划，不执行购买或解锁。",
    )


def _account_login_must_not_do(
    agent_input: Any | None,
    *,
    task_control: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if not _account_login_sensitive(agent_input, task_control=task_control):
        return ()
    return (
        "不要要求用户把账号、密码、验证码或 Cookie 发到聊天里。",
        "不要说“账号信息丢过来”“把密码发给我”“贴 Cookie 给我”这类话。",
        "不要暗示可以代填密码、绕过登录、绕过付费限制或未确认就消耗积分。",
    )


def _sanitize_account_login_reply(text: str, *, agent_input: Any | None = None) -> tuple[str, bool]:
    narrative = str(text or "").strip()
    if not narrative:
        return narrative, False
    if not _account_login_sensitive(agent_input):
        return narrative, False
    if not _ACCOUNT_CREDENTIAL_REQUEST_RE.search(narrative):
        return narrative, False
    return (
        "需要登录没错，但别把账号、密码、验证码或 Cookie 发给我。"
        "你在本地浏览器完成登录/授权，我只复用会话状态；涉及积分购买时，"
        "还要你明确确认书名、链接和最高积分后才会继续。",
        True,
    )


def _has_multimodal_payload(agent_input: Any) -> bool:
    if getattr(agent_input, "attachments", None):
        return True
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    return bool(observations)


def _multimodal_surface_main_chain_supported(agent_input: Any) -> bool:
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    attachment_summary = str(getattr(agent_input, "attachment_summary", "") or "").strip()
    if attachment_summary or str(observations.get("core_observation") or "").strip():
        return True
    if list(observations.get("items") or []):
        return True
    attachments = list(getattr(agent_input, "attachments", None) or [])
    types = {str(_safe_mapping(item).get("type") or "").strip().lower() for item in attachments if item}
    if types & {"image", "emoji", "sticker"}:
        return True
    text = str(getattr(agent_input, "user_message", "") or "").strip()
    return text in {"[图片消息]", "[表情消息]", "[贴纸消息]", "[动画表情]"}


def _surface_user_visible_reply_context(agent_input: Any) -> bool:
    if str(getattr(agent_input, "user_message", "") or "").strip():
        return True
    if getattr(agent_input, "attachments", None):
        return True
    if str(getattr(agent_input, "attachment_summary", "") or "").strip():
        return True
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    return bool(observations)


def _surface_main_chain_default_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off", "legacy"}:
        return False
    return True


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _set_agent_input_mapping(agent_input: Any, name: str, value: Mapping[str, Any]) -> None:
    try:
        setattr(agent_input, name, dict(value))
    except Exception:
        return


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _build_digest_from_inner_monologue(inner_monologue: Mapping[str, Any]) -> InnerMonologueDigest:
    """Rehydrate an InnerMonologueDigest from the dict form of export_runtime_state."""
    return InnerMonologueDigest(
        thought_stream_active=bool(inner_monologue.get("thought_stream_active")),
        private_thought_count=int(inner_monologue.get("private_thought_count") or 0),
        dominant_focus=str(inner_monologue.get("dominant_focus") or "self"),
        emotion_tag=str(inner_monologue.get("emotion_tag") or "calm"),
        salience=float(inner_monologue.get("salience") or 0.0),
        care_drive=float(inner_monologue.get("care_drive") or 0.0),
        continuity_drive=float(inner_monologue.get("continuity_drive") or 0.0),
        repair_drive=float(inner_monologue.get("repair_drive") or 0.0),
        open_loop_count=int(inner_monologue.get("open_loop_count") or 0),
        should_distill_to_memory=bool(inner_monologue.get("should_distill_to_memory")),
        dominant_need=str(inner_monologue.get("dominant_need") or "agency"),
        dominant_tendency=str(inner_monologue.get("dominant_tendency") or "observe_more"),
        novelty_score=float(inner_monologue.get("novelty_score") or 0.0),
        dominant_thought_form=str(inner_monologue.get("dominant_thought_form") or "quiet_drift"),
        liveliness_score=float(inner_monologue.get("liveliness_score") or 0.5),
        quality_flags=tuple(str(item) for item in list(inner_monologue.get("quality_flags") or [])),
        memory_anchor_count=int(inner_monologue.get("memory_anchor_count") or 0),
        style_anchor=tuple(str(item) for item in list(inner_monologue.get("style_anchor") or [])),
        must_not_do=tuple(str(item) for item in list(inner_monologue.get("must_not_do") or [])),
    )


__all__ = [
    "_build_digest_from_inner_monologue",
    "build_surface_context_payload_from_adapter",
    "build_surface_main_chain_input_from_surface_reply_input",
    "run_surface_main_chain_for_surface_reply_input",
    "surface_main_chain_enabled",
    "surface_main_chain_required",
    "surface_main_chain_sse_generator",
]
