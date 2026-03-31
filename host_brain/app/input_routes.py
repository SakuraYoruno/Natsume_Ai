"""Unified Host input route for ASR / Gateway events."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.input_routes")

router = APIRouter(prefix="/v1/host", tags=["input"])

_PLACEHOLDER_GATEWAY_CONVERSATIONS: set[str] = {
    "active_chat",
    "active chat",
    "current_chat",
    "current chat",
    "current_session",
    "default",
    "unknown",
    "none",
    "null",
    "-",
    "_",
    "na",
    "n/a",
    "当前聊天",
    "当前会话",
}


class InputPayload(BaseModel):
    """Ingress payload reported by Edge."""

    source: str = Field(..., description="Input source: ASR or GATEWAY")

    # ── ASR 专属字段 ──
    session_id: str | None = Field(None, description="[ASR] Session ID")
    text: str | None = Field(None, description="[ASR] Transcript")
    confidence: float | None = Field(None, description="[ASR] Confidence 0~1")
    is_final: bool | None = Field(None, description="[ASR] Final segment")
    seq: int | None = Field(None, description="[ASR] Sequence")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best candidates")
    evidence_ref: str | None = Field(None, description="[ASR] Evidence ref")

    # ── GATEWAY 专属字段（来自 UnifiedMessage.as_report_payload()）──
    platform: str | None = Field(None, description="[GATEWAY] Platform: wechat/qq/feishu/wecom")
    msg_id: str | None = Field(None, description="[GATEWAY] Message ID")
    from_id: str | None = Field(None, description="[GATEWAY] Sender ID")
    to_id: str | None = Field(None, description="[GATEWAY] Receiver ID")
    conversation_id: str | None = Field(None, description="[GATEWAY] Conversation ID")
    content: str | None = Field(None, description="[GATEWAY] Message content")
    msg_type: str | None = Field(None, description="[GATEWAY] Message type")
    is_group: bool | None = Field(None, description="[GATEWAY] Is group message")
    sender_name: str | None = Field(None, description="[GATEWAY] Sender display name")
    media_url: str | None = Field(None, description="[GATEWAY] Media URL")
    reply_to_msg_id: str | None = Field(None, description="[GATEWAY] Reply-to message ID")
    raw_extra: dict[str, Any] | None = Field(None, description="[GATEWAY] Extra fields")

    ts_ms: int | None = Field(None, description="Event timestamp in ms")


class InputResponse(BaseModel):
    """Ingress response."""

    success: bool
    request_id: str
    source: str
    status: str
    detail: str
    ts_ms: int


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _is_invalid_gateway_conversation_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_GATEWAY_CONVERSATIONS


@router.post("/input", response_model=InputResponse)
async def receive_input(body: InputPayload, request: Request) -> InputResponse:
    _ = request
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    source = str(body.source or "").upper().strip()

    _logger.info("input_routes: received edge input source=%s request_id=%s", source, request_id)

    if source == "ASR":
        if not body.text or not body.session_id:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="ASR input missing required fields: text, session_id",
                ts_ms=ts_ms,
            )
        text = str(body.text).strip()
        session_id = str(body.session_id).strip()
        if body.is_final is not True:
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail="ASR non-final segment ignored",
                ts_ms=ts_ms,
            )
    elif source == "GATEWAY":
        if not body.content or not body.conversation_id or not body.platform:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="GATEWAY input missing required fields: content, conversation_id, platform",
                ts_ms=ts_ms,
            )
        if _is_invalid_gateway_conversation_id(body.conversation_id):
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail=f"invalid gateway conversation_id: {body.conversation_id}",
                ts_ms=ts_ms,
            )
        text = str(body.content).strip()
        session_id = str(body.conversation_id).strip()
    else:
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=f"unsupported source: {source}, only ASR/GATEWAY are accepted",
            ts_ms=ts_ms,
        )

    try:
        # 通知 ActivityMonitor 有用户互动（触发 sleep→normal 唤醒 + 空闲计时器重置）
        from host_brain.app.dependencies import notify_user_interaction  # noqa: PLC0415
        notify_user_interaction()
    except Exception:
        pass  # 降级：通知失败不影响消息处理

    try:
        await _dispatch_to_graph(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=body.model_dump(exclude_none=True),
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=True,
            request_id=request_id,
            source=source,
            status="ACCEPTED",
            detail="input accepted for processing",
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.error("input_routes: dispatch failed source=%s request_id=%s: %s", source, request_id, exc)
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="ERROR",
            detail=f"dispatch failed: {exc}",
            ts_ms=ts_ms,
        )


def _get_ledger_safe():
    try:
        from host_brain.app.dependencies import get_ledger

        return get_ledger()
    except Exception:
        return None


def _log_input_event(
    *,
    ledger,
    request_id: str,
    session_id: str,
    source: str,
    input_payload: dict[str, Any] | None = None,
    phase: str,
    status: str,
    detail: str = "",
    reason_code: str | None = None,
    latency_ms: int | None = None,
    ts_ms: int,
) -> None:
    if ledger is None:
        return
    try:
        payload: dict[str, Any] = {
            "source": source,
            "phase": phase,
            "latency_ms": latency_ms,
        }
        if source == "GATEWAY":
            gateway_fields = (
                "platform",
                "conversation_id",
                "msg_id",
                "msg_type",
                "from_id",
                "to_id",
                "sender_name",
                "media_url",
                "reply_to_msg_id",
                "is_group",
            )
            for field in gateway_fields:
                value = (input_payload or {}).get(field)
                if value is not None:
                    payload[field] = value
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": f"INPUT_{phase}",
                "direction": "EDGE_TO_HOST",
                "status": status,
                "reason_code": reason_code,
                "stage": f"input_dispatch.{phase.lower()}",
                "detail": detail[:400] if detail else None,
                "payload": payload,
                "ts_ms": ts_ms,
            }
        )
    except Exception as log_exc:
        _logger.debug("input_routes: ledger log failed (non-blocking): %s", log_exc)


def _log_dispatch_task_exception(task: asyncio.Task) -> None:
    """fire-and-forget task 异常日志回调——防止 asyncio 静默吞掉异常。"""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.error(
            "input_routes: dispatch task failed silently: %s",
            exc,
            exc_info=exc,
        )


async def _dispatch_to_graph(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> None:
    ledger = _get_ledger_safe()
    run_in_thread = _parse_bool_env("HOST_INPUT_DISPATCH_IN_THREAD", default=True)
    dispatch_mode = "fire_and_forget.threaded" if run_in_thread else "fire_and_forget.inline"

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACCEPTED",
        status="ACCEPTED",
        detail=f"source={source} text_len={len(text)}",
        ts_ms=ts_ms,
    )

    asyncio.create_task(
        _dispatch_to_graph_worker(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
            ledger=ledger,
            dispatch_mode=dispatch_mode,
            run_in_thread=run_in_thread,
        )
    ).add_done_callback(_log_dispatch_task_exception)


async def _run_main_chain_dispatch(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    run_in_thread: bool,
) -> None:
    try:
        from host_brain.app.graph.main_chain import run_main_chain_from_input  # type: ignore[import-not-found]
    except ImportError:
        _logger.warning("[%s] MainChain 已废弃，input_routes 走降级路径", request_id)
        return

    if not run_in_thread:
        await run_main_chain_from_input(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        return

    # Isolate heavy/possibly blocking chain work from FastAPI main event loop.
    def _thread_entry() -> None:
        asyncio.run(
            run_main_chain_from_input(
                request_id=request_id,
                session_id=session_id,
                source=source,
                text=text,
                payload=payload,
                ts_ms=ts_ms,
            )
        )

    await asyncio.to_thread(_thread_entry)


async def _dispatch_to_graph_worker(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    dispatch_mode: str,
    run_in_thread: bool,
) -> None:
    dispatch_ts = int(time.time() * 1000)
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="DISPATCHED",
        status="DISPATCHED",
        detail=f"dispatch_mode={dispatch_mode} source={source}",
        ts_ms=dispatch_ts,
    )
    try:
        await _run_main_chain_dispatch(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
            run_in_thread=run_in_thread,
        )
        complete_ts = int(time.time() * 1000)
        latency_ms = complete_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="COMPLETED",
            status="OK",
            detail=f"main_chain completed source={source} latency_ms={latency_ms}",
            latency_ms=latency_ms,
            ts_ms=complete_ts,
        )
        _logger.info(
            "input_routes: graph completed request_id=%s source=%s latency_ms=%d mode=%s",
            request_id,
            source,
            latency_ms,
            dispatch_mode,
        )
    except ImportError:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="NOT_WIRED",
            status="DEGRADED",
            reason_code="INPUT_MAIN_CHAIN_NOT_WIRED",
            detail="run_main_chain_from_input not implemented",
            ts_ms=int(time.time() * 1000),
        )
        _logger.info(
            "input_routes: main_chain.run_main_chain_from_input not implemented, request_id=%s source=%s text=%.60s",
            request_id,
            source,
            text,
        )
    except Exception as exc:
        fail_ts = int(time.time() * 1000)
        latency_ms = fail_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="FAILED",
            status="FAILED",
            reason_code="INPUT_GRAPH_ERROR",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=latency_ms,
            ts_ms=fail_ts,
        )
        _logger.error("input_routes: graph failed request_id=%s: %s", request_id, exc)
