"""Chat 核心业务逻辑模块。

拆分后的主入口，仅保留：
  - handle_chat：POST /v1/chat 统一入口
  - _unified_chat_sse_generator：统一 SSE 生成器（Governor 鉴权 + 场景分类 + 路由分发）

子模块职责：
  - chat_handler_edge.py    — Gaming Edge RPC 路由（_gaming_edge_chat_sse_generator）
  - chat_handler_complex.py — 复杂场景 LangGraph 编排（_complex_scene_sse_generator）
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
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    pre_intent_classifier,
)
from host_brain.app.schemas import ChatRequest
from host_brain.core.ids import new_request_id

# 从子模块导入，供测试 monkeypatch 使用
from host_brain.app.chat_handler_edge import (  # noqa: F401
    _run_gaming_edge_chat,
    _post_edge_fallback_rpc,
)
from host_brain.app.dependencies import (  # noqa: F401
    get_retrieval_orchestrator,
    get_episodic_store,
)
from host_brain.app.observability_persist import (  # noqa: F401
    persist_chat_episodic_memory,
)

_logger = logging.getLogger("Natsume.chat_handler")


async def handle_chat(request_body: ChatRequest, request: Request, metrics: Any) -> StreamingResponse:
    """POST /v1/chat 统一入口。

    Governor 鉴权后由 pre_intent_classifier 分类场景：
    - SCENE_CHAT（日常对话）→ 本地 qwen8B 单节点，token 级流式 SSE
    - 复杂场景（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT）→ 云端 Gemini 多节点，节点级进度 SSE
    """
    metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    session_id = request_body.session_id or f"session::{request_id}"

    intent_draft = request_body.intent_draft
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
        intent_draft = {
            "schema_version": "Natsume.intent.v1",
            "id": f"intent::{request_id}",
            "ts_ms": ts_ms,
            "session_id": session_id,
            "seq": 1,
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
) -> AsyncGenerator[str, None]:
    """统一 SSE 生成器。

    流程：
      PRESENCE(THINKING)
      → Governor 鉴权
      → PRESENCE(GOV_APPROVED)
      → pre_intent_classifier 分类（鉴权后）
      → SCENE_CHAT：委托 _sse_event_generator（token 级流式）
      → 复杂场景：LangGraph 多节点 + 节点级进度通知
    """
    from host_brain.app.chat_stream_handler import _sse_event_generator  # noqa: PLC0415
    from host_brain.app.chat_handler_edge import _gaming_edge_chat_sse_generator, _parse_bool  # noqa: PLC0415
    from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415

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
        _ALLOWED_EXPLICIT_SCENE_TYPES = {
            SCENE_CHAT,
            "SCENE_WORK_CODE",
            "SCENE_WORK_TRANSLATE",
            "SCENE_EXEC_INTENT",
        }
        explicit_scene_type = str(request_body.scene_type or "").strip().upper()
        if explicit_scene_type:
            if explicit_scene_type not in _ALLOWED_EXPLICIT_SCENE_TYPES:
                _logger.warning(
                    "客户端传入非法 scene_type，已降级为 SCENE_CHAT",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "rejected_scene_type": explicit_scene_type,
                    },
                )
                scene_type = SCENE_CHAT
                scene_type_source = "fallback_invalid_explicit"
            else:
                scene_type = explicit_scene_type
                scene_type_source = "explicit"
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

        # ── 修复审计问题 #11：SCENE_VISION_UI 专用拦截（高门槛场景，暂不支持）──
        if scene_type == "SCENE_VISION_UI":
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "SCENE_VISION_UI_NOT_SUPPORTED",
                "narrative": "视觉界面场景暂未开放，请稍后再试。",
                "request_id": request_id,
            })
            return

        # ── SCENE_CHAT：委托给 token 级流式生成器 ─────────────────────────
        if scene_type == SCENE_CHAT:
            sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower() or "normal"
            gaming_edge_chat_enabled = _parse_bool(
                os.environ.get("HOST_GAMING_EDGE_CHAT_ENABLED"),
                default=False,
            )
            host_edge_rpc_enabled = _parse_bool(
                os.environ.get("HOST_ENABLE_EDGE_RPC"),
                default=True,
            )
            use_gaming_edge_chat = bool(
                sys_mode == "gaming"
                and gaming_edge_chat_enabled
                and host_edge_rpc_enabled
            )
            if use_gaming_edge_chat:
                async for event in _gaming_edge_chat_sse_generator(
                    request_body=request_body,
                    request_id=request_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    intent_draft=intent_draft,
                    governor_result=governor_result,
                    emotion_context=emotion_context,
                    emotion_snapshot=emotion_snapshot,
                    intent_id=intent_id,
                    reason_code=reason_code,
                    sys_mode=sys_mode,
                    metrics=metrics,
                ):
                    yield event
                return

            async for event in _sse_event_generator(
                message=request_body.message,
                session_id=session_id,
                thread_id=request_body.thread_id,
                mood_state=request_body.mood_state,
                emotion_tag=request_body.emotion_tag,
                request_id=request_id,
                ts_ms=ts_ms,
                intent_draft=intent_draft,
                _pre_approved_governor_result=governor_result,
            ):
                yield event
            return

        # ── 复杂场景：LangGraph 多节点 + 节点级进度通知 ───────────────────
        yield _sse({
            "phase": "PRESENCE",
            "event": "SCENE_ROUTED",
            "scene_type": scene_type,
            "scene_type_source": scene_type_source,
            "request_id": request_id,
        })

        async for event in _complex_scene_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            scene_type=scene_type,
            scene_type_source=scene_type_source,
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id=intent_id,
            reason_code=reason_code,
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
