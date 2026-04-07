"""Chat 核心业务逻辑模块。

拆分后的主入口，仅保留：
  - handle_chat：POST /v1/chat 统一入口
  - _unified_chat_sse_generator：统一 SSE 生成器（Governor 鉴权 + 场景分类 + 路由分发）

Phase XIV v4 架构：
  top_agent.py 处理 80% 闲聊直通路径，本模块负责剩余场景的 SSE 生成。
  场景分类由 pre_intent_classifier 完成，按场景类型分发到对应 handler。

子模块职责：
  - chat_handler_complex.py — 复杂场景 LangGraph 编排（_complex_scene_sse_generator）
  - chat_handler_vision.py  — 视觉界面场景（_vision_ui_sse_generator）
"""

from __future__ import annotations

import asyncio
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
from host_brain.app.intent_seq import allocate_intent_seq
from host_brain.app.graph_config_builder import (
    ALLOWED_EXPLICIT_SCENE_TYPES,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    pre_intent_classifier,
)
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


def _resolve_sys_mode_narrative(mode: str) -> str:
    templates = resolve_prompt_template_json_merged("chat_handler.sys_mode.narratives")
    mapping = dict(templates) if isinstance(templates, dict) else {}
    mode_key = str(mode or "").strip().lower()
    fallback_key = mode_key if mode_key in {"locked", "maintenance"} else "maintenance"
    return str(mapping.get(fallback_key) or mapping.get("maintenance") or "")


async def handle_chat(request_body: ChatRequest, request: Request, metrics: Any) -> StreamingResponse:
    """POST /v1/chat 统一入口。

    Governor 鉴权后由 pre_intent_classifier 分类场景：
    - SCENE_CHAT（日常対話）→ 云优先单节点（Gemini → Codex → DeepSeek → host_14b），token 级流式 SSE
    - 複雑場景（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT）→ 多节点编排，节点级进度 SSE

    模式门控：
    - locked 模式下拒绝所有 chat 请求（返回 403 提示）
    - maintenance 模式下返回维护提示（SSE 快速完成）
    """
    metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    session_id = request_body.session_id or f"session::{request_id}"
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
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': 'SYS_MODE_LOCKED', 'narrative': _resolve_sys_mode_narrative('locked'), 'request_id': request_id}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            _locked_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if _current_sys_mode == "maintenance":
        async def _maintenance_sse():
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': 'SYS_MODE_MAINTENANCE', 'narrative': _resolve_sys_mode_narrative('maintenance'), 'request_id': request_id}, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            _maintenance_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    intent_draft = request_body.intent_draft
    _pre_scene: str | None = None
    if intent_draft is None:
        # 修复审计问题 #2：根据 pre_intent_classifier 预分类结果动态设置 action，
        # 使 Governor 能感知真实意图类型，EXEC_INTENT 不再以 narrative_only 绕过情感门控。
        _pre_scene = pre_intent_classifier(request_body.message)
        _action_map = {
            SCENE_EXEC_INTENT: "exec_intent",
            SCENE_WORK_CODE: "work_code",
            SCENE_WORK_TRANSLATE: "work_translate",
        }
        _draft_action = _action_map.get(_pre_scene, "narrative_only")
        _intent_seq = allocate_intent_seq(session_id=session_id)
        intent_draft = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{request_id}",
            "ts_ms": ts_ms,
            "session_id": session_id,
            "seq": _intent_seq,
            "person_id": request_body.person_id,
            "type": "query",
            "action": _draft_action,
            "target": "host",
            "args": {"text": request_body.message},
            "nonce": f"nonce::{request_id}",
        }

    return StreamingResponse(
        _unified_chat_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            metrics=metrics,
            _pre_classified_scene=_pre_scene,
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
    intent_draft: Dict[str, Any],
    metrics: Any,
    _pre_classified_scene: str | None = None,
    _pre_recall_evidence: list[dict] | None = None,
    _pre_approved_governor_result: Dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """统一 SSE 生成器。

    流程：
      PRESENCE(THINKING)
      → Governor 鉴权（若 _pre_approved_governor_result 传入则跳过重复鉴权）
      → PRESENCE(GOV_APPROVED)
      → pre_intent_classifier 分类（鉴权后）
      → 按场景类型分发：
          SCENE_VISION_UI  → _vision_ui_sse_generator（视觉分析）
          SCENE_CHAT       → _sse_event_generator（token 级流式）
          复杂场景         → _complex_scene_sse_generator（LangGraph 多节点）
    """
    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    yield _sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    try:
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
            governor_result = await asyncio.to_thread(
                engine.evaluate_intent,
                raw_intent=intent_draft,
                request_id=request_id,
                thread_id=request_body.thread_id,
                governance_snapshot=governance_snapshot,
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
            # 复用 handle_chat 中已完成的预分类结果，避免重复调用 pre_intent_classifier
            scene_type = _pre_classified_scene
            scene_type_source = "pre_classifier"
        else:
            scene_type = pre_intent_classifier(request_body.message)
            scene_type_source = "pre_classifier"

        _logger.info(
            "chat scene classified",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "scene_type": scene_type,
                "scene_type_source": scene_type_source,
            },
        )

        # ── Phase XIV v4：顶级 Agent 统一入口（唯一路径）────────────────
        # 夏目蓝本人，统一处理所有场景。
        # 闲聊路径：Airlock → LLM → 反思链 → Airlock → 异步后处理
        # 执行路径：顶级 Agent 调度子 Agent → 子 Agent 内部走治理
        from host_brain.app.top_agent import top_agent_sse_generator  # noqa: PLC0415
        async for event in top_agent_sse_generator(
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
