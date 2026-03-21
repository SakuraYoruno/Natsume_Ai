"""Chat 核心业务逻辑模块。

拆分后的主入口，仅保留：
  - handle_chat：POST /v1/chat 统一入口
  - _unified_chat_sse_generator：统一 SSE 生成器（Governor 鉴权 + 场景分类 + 路由分发）

[任务3] 场景路由已迁入 SceneRouter.route_sse()，不再使用 if-else 硬编码分流。
_unified_chat_sse_generator 只负责 Governor 鉴权 + 场景分类，路由委托给 SceneRouter。

子模块职责：
  - chat_handler_edge.py    — Gaming Edge RPC 路由（_gaming_edge_chat_sse_generator）
  - chat_handler_complex.py — 复杂场景 LangGraph 编排（_complex_scene_sse_generator）
  - chat_handler_vision.py  — 视觉界面场景（_vision_ui_sse_generator，任务4）
  - graph/main_chain.py     — SceneRouter.route_sse()（场景路由分发，任务3）
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
from host_brain.app.intent_seq import allocate_intent_seq
from host_brain.app.graph_config_builder import (
    ALLOWED_EXPLICIT_SCENE_TYPES,
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
      → [任务3] SceneRouter.route_sse() 节点化路由分发：
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
            # GovernorNode（MainChain 适配层）已完成真实鉴权，直接复用结果，避免重复鉴权
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

        # ── [任务3] SceneRouter 节点化路由：替换 if-else 硬编码分流 ─────────
        # 非 SCENE_CHAT 场景通知客户端已路由
        if scene_type != SCENE_CHAT:
            yield _sse({
                "phase": "PRESENCE",
                "event": "SCENE_ROUTED",
                "scene_type": scene_type,
                "scene_type_source": scene_type_source,
                "request_id": request_id,
            })

        from host_brain.app.graph.main_chain import SceneRouter  # noqa: PLC0415
        async for event in SceneRouter.route_sse(
            scene_type=scene_type,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            scene_type_source=scene_type_source,
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id=intent_id,
            reason_code=reason_code,
            metrics=metrics,
            _pre_recall_evidence=_pre_recall_evidence,
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
