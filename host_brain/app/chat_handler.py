"""Chat 核心业务逻辑模块。

拆分后的主入口，仅保留：
  - handle_chat：POST /v1/chat 统一入口
  - _unified_chat_sse_generator：统一 SSE 生成器（Governor 鉴权 + 场景分类 + 路由分发）

Phase XV 收口架构：
  cortex_orchestrator 统一承接主入口，本模块负责 /v1/chat 的鉴权与流式转发。
意图收口由 thalamus_agent.classify_and_compile_intent_draft 完成（Phase XV+），再按 canonical intent 路由分发到对应 handler。

子模块职责：
  - chat_handler_complex.py — 复杂场景 LangGraph 编排（_complex_scene_sse_generator）
  - chat_handler_vision.py  — 视觉界面场景（_vision_ui_sse_generator）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict

from fastapi import Request
from fastapi.responses import StreamingResponse
from host_brain.composition.process_services import get_intent_engine
from host_brain.bond.user_identity import resolve_person_id
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import (
    ALLOWED_EXPLICIT_SCENE_TYPES,
    SCENE_CHAT,
)
from host_brain.governor.route_token import (
    RouteTokenDisposition,
    validate_governor_route_token,
)
from host_brain.tribe.thalamus_agent import (
    THALAMUS_SCENE_TYPE_SOURCE,
    get_thalamus_agent,
    resolve_scene_type_from_intent_decision,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.settings.chat import get_chat_settings
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.chat_handler")


def _action_arbiter_enabled() -> bool:
    """Dedicated cutover flag for the post-Governor execution precondition."""
    return get_chat_settings().action_arbiter_enabled


def _mateengine_chat_bubble_enabled() -> bool:
    return get_chat_settings().mateengine_chat_bubble_enabled


def _mateengine_bubble_text_limit() -> int:
    return get_chat_settings().mateengine_chat_bubble_max_chars


def _clip_mateengine_bubble_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    limit = _mateengine_bubble_text_limit()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "..."


def _extract_text_from_commit_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in ("narrative", "text", "content", "message", "reply"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _clip_mateengine_bubble_text(value)

    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        nested_text = _extract_text_from_commit_payload(nested_payload)
        if nested_text:
            return nested_text

    return ""


def _extract_mateengine_commit_text(event_payload: Any) -> str:
    if not isinstance(event_payload, dict):
        return ""
    if event_payload.get("phase") != "COMMIT" or not event_payload.get("done"):
        return ""
    if str(event_payload.get("decision") or "").upper() == "REJECT":
        return ""

    direct_text = _extract_text_from_commit_payload(event_payload)
    if direct_text:
        return direct_text

    envelope = event_payload.get("reply_envelope")
    if isinstance(envelope, dict):
        envelope_kind = str(envelope.get("kind") or "").lower()
        if envelope_kind and envelope_kind not in {"final_reply", "assistant_reply", "chat_reply"}:
            return ""
        return _extract_text_from_commit_payload(envelope)

    return ""


def _extract_mateengine_commit_performance(event_payload: Any) -> Dict[str, Any]:
    if not isinstance(event_payload, dict):
        return {}
    envelope = event_payload.get("reply_envelope")
    audit_refs = event_payload.get("audit_refs")
    candidates: list[Dict[str, Any]] = []
    if isinstance(envelope, dict):
        performance = envelope.get("performance")
        audit = envelope.get("audit")
        if isinstance(performance, dict):
            candidates.append(performance)
        if isinstance(audit, dict):
            candidates.append(audit)
    if isinstance(audit_refs, dict):
        refs_envelope = audit_refs.get("reply_envelope")
        if isinstance(refs_envelope, dict):
            performance = refs_envelope.get("performance")
            audit = refs_envelope.get("audit")
            if isinstance(performance, dict):
                candidates.append(performance)
            if isinstance(audit, dict):
                candidates.append(audit)
        surface = audit_refs.get("surface_main_chain")
        if isinstance(surface, dict):
            candidates.append(surface)

    def _candidate_intensity(candidate: Dict[str, Any]) -> float | None:
        raw_intensity = (
            candidate.get("emotion_intensity")
            or candidate.get("expression_intensity")
            or candidate.get("emotion_confidence")
        )
        try:
            return max(0.0, min(1.0, float(raw_intensity)))
        except (TypeError, ValueError):
            return None

    emotion_tag = ""
    emotion_intensity: float | None = None
    fallback_tag = ""
    fallback_intensity: float | None = None
    for candidate in candidates:
        intensity = _candidate_intensity(candidate)
        for key in ("natsume_emotion_tag", "assistant_emotion_tag"):
            candidate_tag = str(candidate.get(key) or "").strip().upper()
            if candidate_tag and candidate_tag != "NEUTRAL":
                emotion_tag = candidate_tag
                emotion_intensity = intensity
                break
        if emotion_tag:
            break
        candidate_tag = str(candidate.get("emotion_tag") or "").strip().upper()
        if candidate_tag and not fallback_tag:
            fallback_tag = candidate_tag
            fallback_intensity = intensity
        if candidate_tag and candidate_tag != "NEUTRAL":
            emotion_tag = candidate_tag
            emotion_intensity = intensity
            break
    if not emotion_tag:
        emotion_tag = fallback_tag
        emotion_intensity = fallback_intensity
    return {
        "emotion_tag": emotion_tag,
        "emotion_intensity": emotion_intensity,
    }


def _send_mateengine_chat_bubble(
    text: str,
    request_id: str,
    *,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,
) -> None:
    if not text or not _mateengine_chat_bubble_enabled():
        return
    try:
        from host_brain.channels.embodiment import queue_mateengine_command  # noqa: PLC0415

        queue_mateengine_command(
            action="command",
            payload={
                "command": {
                    "type": "say",
                    "text": text,
                    "emotion": str(emotion_tag or "NEUTRAL").strip().upper(),
                    "intensity": emotion_intensity,
                }
            },
            request_id=request_id,
            idempotency_key=f"{request_id}:mateengine:chat-bubble",
        )
    except Exception as exc:
        _logger.debug(
            "[%s] MateEngine chat bubble send failed: %s",
            request_id,
            exc,
        )


def _mateengine_state_cues_enabled() -> bool:
    return get_chat_settings().mateengine_state_cues_enabled


def _mateengine_thinking_cue_delay_s() -> float:
    return get_chat_settings().mateengine_thinking_cue_delay_ms / 1000.0


def _mateengine_thinking_cue_text() -> str:
    return get_chat_settings().mateengine_thinking_cue_text.strip()


_MATEENGINE_STATE_CUE_DEFAULTS = {
    "thinking": "...",
    "approved": "",
    "memory_recall": "翻一下记忆...",
    "tool_running": "处理中...",
    "error": "这里卡住了。",
}

_MATEENGINE_VISIBLE_STATE_CUES = {
    "thinking",
    "memory_recall",
    "tool_running",
    "processing",
    "speaking",
    "interrupted",
    "error",
}


def _mateengine_state_cue_text(state: str) -> str:
    key = str(state or "").strip().lower()
    if not key:
        return ""
    settings = get_chat_settings()
    configured_cues = {
        "thinking": settings.mateengine_thinking_cue_text,
        "approved": settings.mateengine_approved_cue_text,
        "memory_recall": settings.mateengine_memory_recall_cue_text,
        "tool_running": settings.mateengine_tool_running_cue_text,
        "processing": settings.mateengine_processing_cue_text,
        "speaking": settings.mateengine_speaking_cue_text,
        "interrupted": settings.mateengine_interrupted_cue_text,
        "error": settings.mateengine_error_cue_text,
    }
    return str(configured_cues.get(key, _MATEENGINE_STATE_CUE_DEFAULTS.get(key)) or "").strip()


def _send_mateengine_state_cue(
    state: str,
    *,
    request_id: str,
    lifecycle: Dict[str, Any],
    force: bool = False,
) -> bool:
    if not _mateengine_state_cues_enabled():
        return False
    if lifecycle.get("done") and not force:
        return False
    clean_state = str(state or "").strip().lower()
    if not clean_state:
        return False
    cue_text = _mateengine_state_cue_text(state)
    if not cue_text and clean_state not in _MATEENGINE_VISIBLE_STATE_CUES:
        return False
    if lifecycle.get("last_state") == clean_state and not force:
        return False
    lifecycle["state_cued"] = True
    lifecycle["last_state"] = clean_state
    try:
        from host_brain.channels.embodiment import queue_mateengine_command  # noqa: PLC0415

        queue_mateengine_command(
            action="command",
            payload={
                "command": {
                    "type": "state",
                    "state": clean_state,
                    "text": cue_text,
                }
            },
            request_id=request_id,
            idempotency_key=f"{request_id}:mateengine:state:{clean_state}",
        )
    except Exception as exc:
        _logger.debug(
            "[%s] MateEngine state action send failed: %s",
            request_id,
            exc,
        )
        if cue_text:
            _send_mateengine_chat_bubble(cue_text, request_id=request_id)
    return True


def _infer_mateengine_state_from_intent(intent_draft: Dict[str, Any] | None) -> str:
    if not isinstance(intent_draft, dict):
        return ""
    values = " ".join(
        str(intent_draft.get(key) or "")
        for key in ("route", "target_id", "action", "route_reason", "reason_code", "task")
    ).lower()
    if "memory" in values or "recall" in values:
        return "memory_recall"
    if any(token in values for token in ("tool", "agent", "workflow", "dispatch", "executive")):
        return "tool_running"
    return ""


def _infer_mateengine_state_from_event(event_payload: Any) -> str:
    if not isinstance(event_payload, dict):
        return ""
    phase = str(event_payload.get("phase") or "").strip().upper()
    event = str(event_payload.get("event") or "").strip().upper()
    reason = str(event_payload.get("reason_code") or event_payload.get("route_reason") or "").strip().upper()
    joined = f"{phase} {event} {reason}"

    if phase == "COMMIT" and event_payload.get("done"):
        if str(event_payload.get("decision") or "").upper() == "REJECT":
            if any(token in reason for token in ("ERROR", "EXCEPTION", "INTERNAL", "TIMEOUT")):
                return "error"
        return ""
    if any(token in joined for token in ("MEMORY", "RECALL")):
        return "memory_recall"
    if any(token in joined for token in ("TOOL", "EXEC", "ACTION", "WORKFLOW", "RUNNING", "DISPATCH")):
        return "tool_running"
    return ""


async def _send_delayed_mateengine_thinking_cue(
    request_id: str,
    lifecycle: Dict[str, Any],
) -> None:
    if not _mateengine_state_cues_enabled():
        return
    delay_s = _mateengine_thinking_cue_delay_s()
    if delay_s > 0:
        await asyncio.sleep(delay_s)
    if lifecycle.get("done") or lifecycle.get("state_cued"):
        return
    _send_mateengine_state_cue("thinking", request_id=request_id, lifecycle=lifecycle)


def _should_use_inline_governor(intent_draft: Dict[str, Any] | None) -> bool:
    return False


def _surface_control_requests_memory_recall(surface_task_control: Any) -> bool:
    if not isinstance(surface_task_control, dict) or not surface_task_control:
        return False
    task_mode = str(surface_task_control.get("task_mode") or "").strip().lower()
    response_move = str(surface_task_control.get("response_move") or "").strip().lower()
    recall_markers = {
        "memory_recall",
        "memory_followup",
        "recall_memory",
        "recall_preference",
        "recall_relationship",
    }
    return task_mode in recall_markers or response_move in recall_markers


def _normalize_surface_memory_recall_intent(
    *,
    intent_draft: Dict[str, Any] | None,
    request_body: ChatRequest,
    request_id: str,
    session_id: str,
) -> Dict[str, Any] | None:
    draft = dict(intent_draft or {})
    target_id = str(draft.get("target_id") or draft.get("skill_id") or "").strip().lower()
    action = str(draft.get("action") or "").strip().lower()
    control_requests_recall = _surface_control_requests_memory_recall(getattr(request_body, "surface_task_control", None))
    if not control_requests_recall and target_id != "memory_recall" and action != "memory_recall":
        return intent_draft
    if target_id not in {"", "memory_recall"} and action != "memory_recall":
        return intent_draft

    task_text = str(draft.get("task") or getattr(request_body, "message", "") or "").strip()
    args_payload = dict(draft.get("args") or {}) if isinstance(draft.get("args"), dict) else {}
    original_route = {
        "route": draft.get("route"),
        "target_id": draft.get("target_id"),
        "action": draft.get("action"),
        "reason_code": draft.get("reason_code") or draft.get("route_reason"),
    }
    draft.update(
        {
            "schema_version": str(draft.get("schema_version") or "Natsume.intent.v1"),
            "id": str(draft.get("id") or f"intent::{request_id}"),
            "session_id": str(draft.get("session_id") or session_id),
            "type": "query",
            "action": "exec_intent",
            "route": "chat",
            "target_id": "",
            "target": str(draft.get("target") or "host"),
            "task": task_text,
            "route_reason": "SURFACE_MAIN_CHAIN_MEMORY_RECALL",
            "reason_code": "SURFACE_MAIN_CHAIN_MEMORY_RECALL",
            "args": {"text": str(args_payload.get("text") or task_text)},
            "nonce": str(draft.get("nonce") or f"nonce::{request_id}"),
            "_surface_route_override": original_route,
        }
    )
    return draft


async def _evaluate_governor_with_timing(
    *,
    engine: Any,
    intent_draft: Dict[str, Any] | None,
    request_id: str,
    thread_id: str | None,
    governance_snapshot: Dict[str, Any],
    inline: bool = False,
) -> tuple[Dict[str, Any], dict[str, int]]:
    submit_started_ms = int(time.time() * 1000)
    timing: dict[str, int] = {
        "submit_started_ms": submit_started_ms,
        "executor_started_ms": 0,
        "executor_finished_ms": 0,
    }

    def _run() -> Dict[str, Any]:
        timing["executor_started_ms"] = int(time.time() * 1000)
        try:
            return dict(
                engine.evaluate_intent(
                    raw_intent=intent_draft,
                    request_id=request_id,
                    thread_id=thread_id,
                    governance_snapshot=governance_snapshot,
                )
                or {}
            )
        finally:
            timing["executor_finished_ms"] = int(time.time() * 1000)

    # IntentDecisionEngine persists intent/governor rows through the canonical
    # SQLiteLedger connection, which is owned by the event-loop thread.  Moving
    # this call to a generic worker thread silently drops the authoritative
    # grant with sqlite3's thread-affinity error.  The local policy evaluation
    # is bounded and therefore remains on its owning thread.
    del inline
    result = _run()
    finished_ms = int(time.time() * 1000)
    executor_started_ms = int(timing.get("executor_started_ms") or finished_ms)
    executor_finished_ms = int(timing.get("executor_finished_ms") or finished_ms)
    summary = {
        "total_latency_ms": max(0, finished_ms - submit_started_ms),
        "queue_wait_ms": max(0, executor_started_ms - submit_started_ms),
        "execute_ms": max(0, executor_finished_ms - executor_started_ms),
    }
    return result, summary


def _resolve_sys_mode_narrative(mode: str, *, render_target_lang: str = "zh") -> str:
    mode_key = str(mode or "").strip().lower()
    lang_key = "ja" if str(render_target_lang).strip().lower() == "ja" else "zh"
    if mode_key == "locked":
        return "当前 host 已锁定，暂时不处理聊天请求。" if lang_key == "zh" else "現在 host はロック中です。"
    return "当前 host 处于维护状态，稍后再试。" if lang_key == "zh" else "現在 host はメンテナンス中です。"


async def handle_chat(
    request_body: ChatRequest,
    request: Request,
    metrics: Any,
    governed_command_application: Any | None = None,
) -> StreamingResponse:
    """POST /v1/chat 统一入口。

Governor 鉴权后由 thalamus_agent 统一收口 canonical intent（Phase XV+）：
    - route=chat → 云优先单节点（Gemini → Codex → DeepSeek → host_14b），token 级流式 SSE
    - route=delegate/skill/plan → cortex_orchestrator 调度对应 Agent

    模式门控：
    - locked 模式下拒绝所有 chat 请求（返回 403 提示）
    - maintenance 模式下返回维护提示（SSE 快速完成）
    """
    metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    request_started_ms = int(getattr(request.state, "request_started_ms", ts_ms) or ts_ms)
    session_id = request_body.session_id or f"session::{request_id}"
    input_source = str(getattr(request_body, "input_source", None) or "").strip().upper()
    render_target_lang = str(getattr(request_body, "render_target_lang", None) or "").strip().lower() or "zh"
    if input_source == "GATEWAY":
        render_target_lang = "zh"
    request_body.person_id = resolve_person_id(
        getattr(request_body, "person_id", None),
        session_id=session_id,
        platform=getattr(request_body, "reply_channel", None),
        conversation_id=getattr(request_body, "reply_target", None),
    )

    # ── sys_mode 门控：locked / maintenance 模式下拦截 chat 请求 ──
    _current_sys_mode = get_chat_settings().sys_mode.strip().lower()
    if _current_sys_mode == "locked":
        async def _locked_sse():
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': 'SYS_MODE_LOCKED', 'narrative': _resolve_sys_mode_narrative('locked', render_target_lang=render_target_lang), 'request_id': request_id}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            _locked_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if _current_sys_mode == "maintenance":
        async def _maintenance_sse():
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': 'SYS_MODE_MAINTENANCE', 'narrative': _resolve_sys_mode_narrative('maintenance', render_target_lang=render_target_lang), 'request_id': request_id}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            _maintenance_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return StreamingResponse(
        _unified_chat_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            request_started_ms=request_started_ms,
            intent_draft=None,
            metrics=metrics,
            governed_command_application=governed_command_application,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _unified_chat_sse_generator(
    *,
    request_body: ChatRequest,
    request_id: str,
    session_id: str,
    ts_ms: int,
    request_started_ms: int,
    intent_draft: Dict[str, Any] | None,
    metrics: Any,
    governed_command_application: Any | None = None,
    _pre_recall_evidence: list[dict] | None = None,
    _pre_approved_governor_result: Dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """统一 SSE 生成器。

    流程：
      PRESENCE(THINKING)
      → thalamus canonical intent 收口（若未预传入）
      → Governor 鉴权（若 _pre_approved_governor_result 传入则跳过重复鉴权）
      → PRESENCE(GOV_APPROVED)
      → Thalamus 根据 canonical intent 给出场景标签（仅记录/审计，不再走 app 预分类器）
      → cortex_orchestrator_sse_generator 统一处理
    """
    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    input_source = str(getattr(request_body, "input_source", None) or "").strip().upper()
    render_target_lang = str(getattr(request_body, "render_target_lang", None) or "").strip().lower() or "zh"
    if input_source == "GATEWAY":
        render_target_lang = "zh"

    first_presence_delay_ms = max(0, int(time.time() * 1000) - int(request_started_ms or ts_ms))
    _logger.info(
        "[%s] chat_handler first_presence_delay_ms=%s",
        request_id,
        first_presence_delay_ms,
    )
    try:
        from host_brain.tribe.default_mode_agent import mark_recent_foreground_interaction  # noqa: PLC0415

        mark_recent_foreground_interaction(ts_ms=request_started_ms)
    except Exception:
        pass
    yield _sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id}) + ": keepalive\n\n"
    await asyncio.sleep(0)

    mateengine_lifecycle = {"done": False, "state_cued": False, "last_state": ""}
    mateengine_thinking_task = None
    if _mateengine_state_cues_enabled():
        mateengine_thinking_task = asyncio.create_task(
            _send_delayed_mateengine_thinking_cue(
                request_id,
                mateengine_lifecycle,
            )
        )

    try:
        if intent_draft is None:
            classify_started_ms = int(time.time() * 1000)
            intent_draft = await get_thalamus_agent().classify_and_compile_intent_draft(
                request_id=request_id,
                session_id=session_id,
                user_message=request_body.message,
                person_id=request_body.person_id,
                existing_intent_draft=request_body.intent_draft,
                source="chat_handler",
                input_source=getattr(request_body, "input_source", None),
                ts_ms=ts_ms,
                attachments=list(getattr(request_body, "attachments", []) or []),
                multimodal_observations=dict(getattr(request_body, "multimodal_observations", {}) or {}),
            )
            classify_latency_ms = int(time.time() * 1000) - classify_started_ms
            _logger.info(
                "[%s] chat_handler thalamus classify_latency_ms=%s request_to_classify_ms=%s route=%s reason=%s",
                request_id,
                classify_latency_ms,
                max(0, classify_started_ms - int(request_started_ms or ts_ms)),
                str((dict(intent_draft or {})).get("route") or ""),
                str((dict(intent_draft or {})).get("route_reason") or (dict(intent_draft or {})).get("reason_code") or ""),
            )

        intent_draft = _normalize_surface_memory_recall_intent(
            intent_draft=intent_draft,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
        )
        inferred_state = _infer_mateengine_state_from_intent(intent_draft)
        if inferred_state:
            _send_mateengine_state_cue(
                inferred_state,
                request_id=request_id,
                lifecycle=mateengine_lifecycle,
            )

        emotion_context = resolve_chat_emotion_context(request_body=request_body)
        governance_snapshot = build_chat_governance_snapshot(
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )
        emotion_snapshot = build_chat_emotion_snapshot(
            emotion_context=emotion_context,
            source="chat_request",
            ts_ms=ts_ms,
        )

        # ── Governor 鉴权 ─────────────────────────────────────────────────
        if _pre_approved_governor_result is not None:
            # 上游已完成真实鉴权，直接复用结果，避免重复鉴权
            governor_result = _pre_approved_governor_result
            _logger.debug(
                "unified_chat_sse_generator 使用预鉴权结果（来自 GovernorNode），跳过重复鉴权",
                extra={"request_id": request_id},
            )
        else:
            engine = get_intent_engine()
            use_inline_governor = _should_use_inline_governor(intent_draft)
            governor_started_ms = int(time.time() * 1000)
            governor_result, governor_timing = await _evaluate_governor_with_timing(
                engine=engine,
                intent_draft=intent_draft,
                request_id=request_id,
                thread_id=request_body.thread_id,
                governance_snapshot=governance_snapshot,
                inline=use_inline_governor,
            )
            governor_latency_ms = int(time.time() * 1000) - governor_started_ms
            if isinstance(governor_result, dict):
                governor_result.setdefault("latency_ms", governor_latency_ms)
                governor_result.setdefault("queue_wait_ms", int(governor_timing.get("queue_wait_ms") or 0))
                governor_result.setdefault("execute_ms", int(governor_timing.get("execute_ms") or 0))
            _logger.info(
                "[%s] chat_handler governor_latency_ms=%s request_to_governor_ms=%s queue_wait_ms=%s execute_ms=%s inline=%s decision=%s reason=%s",
                request_id,
                governor_latency_ms,
                max(0, governor_started_ms - int(request_started_ms or ts_ms)),
                int(governor_timing.get("queue_wait_ms") or 0),
                int(governor_timing.get("execute_ms") or 0),
                use_inline_governor,
                str((dict(governor_result or {})).get("decision") or ""),
                str((dict(governor_result or {})).get("reason_code") or ""),
            )

        decision = governor_result.get("decision")
        reason_code = governor_result.get("reason_code")
        intent_id = governor_result.get("intent_id")

        if decision != "APPROVE":
            metrics.inc_rejection(reason_code or "UNKNOWN")
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": reason_code or "REJECTED",
                "request_id": request_id,
            })
            return

        # Route commitment is checked before publishing GOV_APPROVED and before
        # Cortex can observe the request.  During cutover, a completely absent
        # legacy token pair is tolerated only while the dedicated feature flag
        # is disabled.  Any supplied-but-invalid pair remains fail closed, and
        # AIRLOCK is always a paused review state rather than an approval.
        route_validation = validate_governor_route_token(governor_result)
        route_gate_enabled = _action_arbiter_enabled()
        route_metadata_present = bool(
            route_validation.token_ref or route_validation.detail_token
        )
        if route_validation.disposition == RouteTokenDisposition.PAUSE:
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "PAUSE",
                "execution_state": "PAUSED_REVIEW",
                "reason_code": "GOV_AIRLOCK_REVIEW",
                "route_validation_reason_code": route_validation.reason_code,
                "committed_route_token": route_validation.token,
                "request_id": request_id,
            })
            return
        if (
            route_validation.disposition == RouteTokenDisposition.BLOCK
            and (route_gate_enabled or route_metadata_present)
        ):
            metrics.inc_rejection(route_validation.reason_code)
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "execution_state": "BLOCKED",
                "reason_code": route_validation.reason_code,
                "committed_route_token": route_validation.token or None,
                "request_id": request_id,
            })
            return
        if route_validation.disposition == RouteTokenDisposition.BLOCK:
            _logger.warning(
                "[%s] legacy Governor output has no route-token pair; "
                "compatibility cutover remains active",
                request_id,
            )

        yield _sse({"phase": "PRESENCE", "event": "GOV_APPROVED", "request_id": request_id})
        _send_mateengine_state_cue(
            "approved",
            request_id=request_id,
            lifecycle=mateengine_lifecycle,
        )
        await asyncio.sleep(0)

        # ── 鉴权后记录场景标签 ────────────────────────────────────────────
        # 修复审计问题 #3：对客户端显式传入的 scene_type 进行合法性校验，
        # 防止客户端强制路由到任意场景（如 VISION_UI 等高门槛场景）。
        # ALLOWED_EXPLICIT_SCENE_TYPES 统一在 graph_config_builder 中维护，此处直接引用。
        explicit_scene_raw = str(request_body.scene_type or "").strip().upper()
        explicit_scene_type = explicit_scene_raw.removeprefix("SCENE_")
        if explicit_scene_type:
            if explicit_scene_type not in ALLOWED_EXPLICIT_SCENE_TYPES:
                _logger.warning(
                    "客户端传入非法 scene_type，已降级为 SCENE_CHAT",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "rejected_scene_type": explicit_scene_raw,
                    },
                )
                scene_type = SCENE_CHAT
                scene_type_source = "fallback_invalid_explicit"
            else:
                scene_type = explicit_scene_type
                scene_type_source = "explicit"
        else:
            scene_type = resolve_scene_type_from_intent_decision(
                intent_draft,
                fallback_text=str(request_body.message or ""),
            )
            scene_type_source = THALAMUS_SCENE_TYPE_SOURCE

        _logger.info(
            "chat scene classified",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "scene_type": scene_type,
                "scene_type_source": scene_type_source,
            },
        )

        # ── Phase XV：Cortex Orchestrator 统一入口（唯一路径）──────────────
        # 旧角色本人，统一处理所有场景。
        # 闲聊路径：Airlock → LLM → 反思链 → Airlock → 异步后处理
        # 执行路径：Cortex Orchestrator 调度 Executive Router / 子 Agent
        from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator  # noqa: PLC0415
        _chat_committed = False
        _mateengine_bubbled = False
        async for event in cortex_orchestrator_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            governor_result=governor_result,
            intent_id=intent_id,
            metrics=metrics,
            governed_command_application=governed_command_application,
            command_authority={},
        ):
            yield event
            # T-94：检测 COMMIT done=True，对话完成后发布 TASK_SUCCESS 事件
            try:
                _ev = json.loads(event[len("data: "):].strip()) if event.startswith("data: ") else {}
                event_state = _infer_mateengine_state_from_event(_ev)
                if event_state:
                    _send_mateengine_state_cue(
                        event_state,
                        request_id=request_id,
                        lifecycle=mateengine_lifecycle,
                        force=event_state == "error",
                    )
                if _ev.get("phase") == "COMMIT" and _ev.get("done") and not _chat_committed:
                    _chat_committed = True
                    mateengine_lifecycle["done"] = True
                    mate_text = _extract_mateengine_commit_text(_ev)
                    if mate_text and not _mateengine_bubbled:
                        _mateengine_bubbled = True
                        mate_performance = _extract_mateengine_commit_performance(_ev)
                        _send_mateengine_state_cue(
                            "speaking",
                            request_id=request_id,
                            lifecycle=mateengine_lifecycle,
                            force=True,
                        )
                        _send_mateengine_chat_bubble(
                            mate_text,
                            request_id=request_id,
                            emotion_tag=mate_performance.get("emotion_tag"),
                            emotion_intensity=mate_performance.get("emotion_intensity"),
                        )
                    try:
                        from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415
                        get_ans_agent().emit_task_success(
                            session_id=session_id,
                            task_type="chat",
                            intensity=0.35,
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    except Exception as exc:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        metrics.inc_error()
        _logger.error(
            "unified chat SSE generator failed request_id=%s error=%s\n%s",
            request_id,
            exc,
            _tb_str,
        )
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "INTERNAL_SERVER_ERROR",
            "request_id": request_id,
        })
        _send_mateengine_state_cue(
            "error",
            request_id=request_id,
            lifecycle=mateengine_lifecycle,
            force=True,
        )
    finally:
        mateengine_lifecycle["done"] = True
        if mateengine_thinking_task is not None and not mateengine_thinking_task.done():
            mateengine_thinking_task.cancel()
