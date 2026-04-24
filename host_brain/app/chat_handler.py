"""Chat 核心业务逻辑模块。

拆分后的主入口，仅保留：
  - handle_chat：POST /v1/chat 统一入口
  - _unified_chat_sse_generator：统一 SSE 生成器（Governor 鉴权 + 场景分类 + 路由分发）

Phase XV 收口架构：
  cortex_orchestrator 统一承接主入口，本模块负责 /v1/chat 的鉴权、场景判定与流式转发。
意图收口由 thalamus_agent.classify_and_compile_intent_draft 完成（Phase XV+），再按 canonical intent 路由分发到对应 handler。

子模块职责：
  - chat_handler_complex.py — 复杂场景 LangGraph 编排（_complex_scene_sse_generator）
  - chat_handler_vision.py  — 视觉界面场景（_vision_ui_sse_generator）
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict

from fastapi import Request
from fastapi.responses import StreamingResponse
from host_brain.app.dependencies import get_intent_engine
from host_brain.bond.user_identity import resolve_person_id
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import (
    ALLOWED_EXPLICIT_SCENE_TYPES,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_TYPE_SOURCE_TRIBE_DECIDER,
    SCENE_TYPE_SOURCE_TRIBE_DECIDER_DEFAULT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)
from host_brain.tribe.thalamus_agent import get_thalamus_agent
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.schemas import ChatRequest
from host_brain.core.ids import new_request_id

from host_brain.app.dependencies import (  # noqa: F401
    get_retrieval_orchestrator,
    get_episodic_store,
)
from host_brain.app.observability_persist import (  # noqa: F401
    persist_chat_episodic_memory,
)

_logger = logging.getLogger("Natsume.chat_handler")


def _resolve_governor_executor_workers() -> int:
    raw = str(os.environ.get("HOST_GOVERNOR_EXECUTOR_WORKERS") or "4").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 4
    return max(2, min(value, 12))


_GOVERNOR_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_resolve_governor_executor_workers(),
    thread_name_prefix="governor",
)


def _should_use_inline_governor(intent_draft: Dict[str, Any] | None) -> bool:
    return False


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

    if inline:
        result = _run()
    else:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_GOVERNOR_EXECUTOR, _run)
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
    templates = resolve_prompt_template_json_merged("chat_handler.sys_mode.narratives")
    mapping = dict(templates) if isinstance(templates, dict) else {}
    mode_key = str(mode or "").strip().lower()
    fallback_key = mode_key if mode_key in {"locked", "maintenance"} else "maintenance"
    raw = mapping.get(fallback_key) or mapping.get("maintenance") or ""
    if isinstance(raw, dict):
        lang_key = "ja" if str(render_target_lang).strip().lower() == "ja" else "zh"
        return str(raw.get(lang_key) or raw.get("zh") or raw.get("ja") or "").strip()
    return str(raw or "")


async def handle_chat(request_body: ChatRequest, request: Request, metrics: Any) -> StreamingResponse:
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
    _current_sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower()
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
            _pre_classified_scene=None,  # Phase XV: 场景类型由 cortex_orchestrator 决定
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
    _pre_classified_scene: str | None = None,
    _pre_recall_evidence: list[dict] | None = None,
    _pre_approved_governor_result: Dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """统一 SSE 生成器。

    流程：
      PRESENCE(THINKING)
      → thalamus canonical intent 收口（若未预传入）
      → Governor 鉴权（若 _pre_approved_governor_result 传入则跳过重复鉴权）
      → PRESENCE(GOV_APPROVED)
      → Phase XV：场景类型由 cortex_orchestrator 内的 tribe 层决定（已废弃 pre_intent_classifier）
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

        yield _sse({"phase": "PRESENCE", "event": "GOV_APPROVED", "request_id": request_id})
        await asyncio.sleep(0)

        # ── 鉴权后分类场景 ────────────────────────────────────────────────
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
        elif _pre_classified_scene is not None:
            # 复用 handle_chat 中已完成的预分类结果
            scene_type = _pre_classified_scene
            scene_type_source = SCENE_TYPE_SOURCE_TRIBE_DECIDER
        else:
            # Phase XV：不再调用 pre_intent_classifier，scene_type 由 cortex_orchestrator 决定
            scene_type = SCENE_CHAT
            scene_type_source = SCENE_TYPE_SOURCE_TRIBE_DECIDER_DEFAULT

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
        # 夏目蓝本人，统一处理所有场景。
        # 闲聊路径：Airlock → LLM → 反思链 → Airlock → 异步后处理
        # 执行路径：Cortex Orchestrator 调度 Executive Router / 子 Agent
        from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator  # noqa: PLC0415
        _chat_committed = False
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
        ):
            yield event
            # T-94：检测 COMMIT done=True，对话完成后发布 TASK_SUCCESS 事件
            try:
                _ev = json.loads(event[len("data: "):].strip()) if event.startswith("data: ") else {}
                if _ev.get("phase") == "COMMIT" and _ev.get("done") and not _chat_committed:
                    _chat_committed = True
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
            "unified chat SSE generator failed",
            extra={"request_id": request_id, "error": str(exc), "traceback": _tb_str},
        )
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "INTERNAL_SERVER_ERROR",
            "request_id": request_id,
        })
