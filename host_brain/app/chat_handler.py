"""Chat 核心业务逻辑模块。

拆分后的主入口，仅保留：
  - handle_chat：POST /v1/chat 统一入口
  - _unified_chat_sse_generator：统一 SSE 生成器（Governor 鉴权 + 场景分类 + 路由分发）

Phase XV 收口架构：
  cortex_orchestrator 统一承接主入口，本模块负责 /v1/chat 的鉴权与流式转发。
意图收口由 thalamus_agent.classify_and_compile_intent_draft 完成（Phase XV+），再按 canonical intent 路由分发到对应 handler。

子模块职责：
  - chat_handler_complex.py — 复杂场景 LangGraph 编排（_complex_scene_sse_generator）
  - 非 text 视觉输入在 chat_routes 的真实入口拒绝；不存在独立视觉 Chat helper。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Callable, Dict

from fastapi import Request
from fastapi.responses import StreamingResponse
from host_brain.app.direct_chat_scope import bind_direct_chat_scope
from host_brain.app.graph_config_constants import ALLOWED_EXPLICIT_SCENE_TYPES
from host_brain.model_runtime.prompt_builder_utils import SCENE_CHAT
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.governor.route_token import (
    RouteTokenDisposition,
    validate_governor_route_token,
)
from host_brain.tribe.thalamus_agent import (
    THALAMUS_SCENE_TYPE_SOURCE,
    resolve_scene_type_from_intent_decision,
)
from host_brain.app.schemas import ChatRequest
from host_brain.core.orchestration_settings import get_orchestration_settings
from host_brain.core.ids import new_request_id
from host_brain.core.runtime_helpers import _sse

_logger = logging.getLogger("Natsume.chat_handler")


def _action_arbiter_enabled() -> bool:
    """Governor 之后执行前置条件的专用切换开关。"""
    return get_orchestration_settings().action_arbiter_enabled


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
    control_requests_recall = _surface_control_requests_memory_recall(
        getattr(request_body, "surface_task_control", None))
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

    # IntentDecisionEngine 通过事件循环线程所属的规范 SQLiteLedger 持久化 intent/governor
    # 行。若移到通用工作线程，会因 sqlite3 线程亲和性错误静默丢失权威 grant；本地策略
    # 求值是有界的，因此必须留在其所属线程。
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


def _resolve_injected_sys_mode(
    hard_state_provider: Callable[[], Any] | None,
) -> tuple[str, str | None]:
    """读取应用所属系统模式；所有者缺失或值非法时安全地拒绝为维护模式。"""

    if not callable(hard_state_provider):
        return "maintenance", "SYS_MODE_OWNER_UNAVAILABLE"
    try:
        hard_state = hard_state_provider()
        if isinstance(hard_state, dict):
            raw_mode = hard_state.get("sys_mode")
        else:
            raw_mode = getattr(hard_state, "sys_mode", None)
        mode = str(raw_mode or "").strip().lower()
    except Exception:
        return "maintenance", "SYS_MODE_OWNER_UNAVAILABLE"
    if mode not in {
        "normal",
        "focus",
        "gaming",
        "sleep",
        "maintenance",
        "locked",
    }:
        return "maintenance", "SYS_MODE_OWNER_INVALID"
    return mode, None


async def handle_chat(
    request_body: ChatRequest,
    request: Request,
    metrics: Any,
    governed_command_application: Any | None = None,
    hard_state_provider: Callable[[], Any] | None = None,
    input_runtime_ports: InputRuntimeApplicationPorts | None = None,
    cortex_orchestrator_sse_generator: Callable[..., Any] | None = None,
) -> StreamingResponse:
    """
        处理 POST /v1/chat 请求并返回 SSE 响应。

        Args:
            request_body:
                已解析的聊天请求数据，包含 message、session_id、
                person_id、scene_type 等用户输入字段。

            request:
                FastAPI 原始 Request，用于读取 request.state、
                app runtime、请求级上下文等信息。

            metrics:
                聊天链监控指标对象，用于记录请求数、拒绝数和异常数。

            governed_command_application:
                受治理任务执行入口。
                当意图被分流到 Work / Task 链时使用。

            hard_state_provider:
                Host 硬状态读取函数，用于获取 sys_mode，
                例如 normal、maintenance、locked。

            input_runtime_ports:
                输入运行时依赖入口。
                提供 Thalamus、Governor、情绪上下文等能力。

            cortex_orchestrator_sse_generator:
                Cortex 主编排生成器。
                Governor 审批完成后，由它根据 canonical_route
                分流到 Chat / Work / Read-only 链。

        Returns:
            StreamingResponse:
                SSE 流式响应。
        """
    metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    request_started_ms = int(getattr(request.state, "request_started_ms", ts_ms) or ts_ms)
    session_id = request_body.session_id or f"session::{request_id}"
    direct_chat_scope = bind_direct_chat_scope(
        request_body,
        session_id=session_id,
    )
    session_id = direct_chat_scope.session_id
    input_source = str(getattr(request_body, "input_source", None) or "").strip().upper()
    render_target_lang = str(getattr(request_body, "render_target_lang", None) or "").strip().lower() or "zh"
    if input_source == "GATEWAY":
        render_target_lang = "zh"
    # ── sys_mode 门控：locked / maintenance 模式下拦截 chat 请求 ──
    _current_sys_mode, _sys_mode_owner_error = _resolve_injected_sys_mode(
        hard_state_provider
    )
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
            yield f"data: {json.dumps({'phase': 'COMMIT', 'done': True, 'decision': 'REJECT', 'reason_code': _sys_mode_owner_error or 'SYS_MODE_MAINTENANCE', 'narrative': _resolve_sys_mode_narrative('maintenance', render_target_lang=render_target_lang), 'request_id': request_id}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            _maintenance_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if not isinstance(input_runtime_ports, InputRuntimeApplicationPorts):
        raise RuntimeError("HOST_INPUT_RUNTIME_PORTS_NOT_INJECTED")
    input_runtime_ports.require_thalamus_agent()

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
            input_runtime_ports=input_runtime_ports,
            cortex_orchestrator_sse_generator=(
                cortex_orchestrator_sse_generator
            ),
            command_authority=direct_chat_scope.as_command_authority(),
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
    input_runtime_ports: InputRuntimeApplicationPorts | None = None,
    cortex_orchestrator_sse_generator: Callable[..., Any] | None = None,
    command_authority: Dict[str, Any] | None = None,
    _pre_recall_evidence: list[dict] | None = None,
    _pre_approved_governor_result: Dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """
  统一聊天 SSE 主流程。

  核心数据流：
      ChatRequest
      → Thalamus 生成 intent_draft
      → 构建 emotion / governance 上下文
      → Governor 生成 governor_result
      → Cortex 根据 route 执行
      → 将 Cortex 事件转换为 SSE 返回客户端

  输入上下文：
      request_body:
          当前聊天请求，提供用户消息、身份、附件等原始业务数据。
      request_id:
          当前 HTTP 请求唯一标识，用于日志和链路追踪。
      session_id:
          当前会话标识。
      ts_ms / request_started_ms:
          当前事件时间及请求开始时间，用于延迟统计。
      intent_draft:
          已生成的意图草稿；为 None 时由 Thalamus 生成。
      command_authority:
          当前请求进入 Cortex 时携带的执行权限上下文。

  运行时依赖：
      metrics:
          指标统计。
      input_runtime_ports:
          提供 Thalamus、Governor、情绪上下文等输入侧能力。
      governed_command_application:
          Work / Task 路径的受治理执行入口。
      cortex_orchestrator_sse_generator:
          Cortex 主编排入口，消费 intent + governor 结果并产生 SSE 事件。

  预计算输入：
      _pre_approved_governor_result:
          上游已完成 Governor 鉴权时复用，避免重复鉴权。
  """

    if not isinstance(input_runtime_ports, InputRuntimeApplicationPorts):
        raise RuntimeError("HOST_INPUT_RUNTIME_PORTS_NOT_INJECTED")

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
            thalamus_agent = input_runtime_ports.require_thalamus_agent()
            intent_draft = await thalamus_agent.classify_and_compile_intent_draft(
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
                str((dict(intent_draft or {})).get("route_reason") or (dict(intent_draft or {})).get(
                    "reason_code") or ""),
            )

        intent_draft = _normalize_surface_memory_recall_intent(
            intent_draft=intent_draft,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
        )
        emotion_context = input_runtime_ports.resolve_chat_emotion_context(
            request_body=request_body,
            request_id=request_id,
        )
        governance_snapshot = input_runtime_ports.build_chat_governance_snapshot(
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )
        emotion_snapshot = input_runtime_ports.build_chat_emotion_snapshot(
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
            engine = input_runtime_ports.get_intent_engine()
            governor_started_ms = int(time.time() * 1000)
            governor_result, governor_timing = await (
                input_runtime_ports.evaluate_governor_with_timing(
                    engine=engine,
                    intent_draft=intent_draft,
                    request_id=request_id,
                    thread_id=request_body.thread_id,
                    governance_snapshot=governance_snapshot,
                )
            )
            governor_latency_ms = int(time.time() * 1000) - governor_started_ms
            if isinstance(governor_result, dict):
                governor_result.setdefault("latency_ms", governor_latency_ms)
                governor_result.setdefault("queue_wait_ms", int(governor_timing.get("queue_wait_ms") or 0))
                governor_result.setdefault("execute_ms", int(governor_timing.get("execute_ms") or 0))
            _logger.info(
                "[%s] chat_handler governor_latency_ms=%s request_to_governor_ms=%s queue_wait_ms=%s execute_ms=%s decision=%s reason=%s",
                request_id,
                governor_latency_ms,
                max(0, governor_started_ms - int(request_started_ms or ts_ms)),
                int(governor_timing.get("queue_wait_ms") or 0),
                int(governor_timing.get("execute_ms") or 0),
                str((dict(governor_result or {})).get("decision") or ""),
                str((dict(governor_result or {})).get("reason_code") or ""),
            )

        decision = governor_result.get("decision")
        reason_code = governor_result.get("reason_code")

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
        if not callable(cortex_orchestrator_sse_generator):
            raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
        async for event in cortex_orchestrator_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            governor_result=governor_result,
            governed_command_application=governed_command_application,
            command_authority=dict(command_authority or {}),
        ):
            yield event

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
