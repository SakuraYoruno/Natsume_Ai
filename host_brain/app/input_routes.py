"""Unified Host input route for ASR / Gateway events.

路由层（薄层）：只负责参数解析和响应格式化。
业务逻辑已拆分到以下 service 模块：
  - input_service_log.py      日志/审计
  - input_service_gateway.py  gateway 意图路由
  - input_service_ack.py      即时 ACK / 进度推送
  - input_service_quality.py  执行质量分析 / 偏航检测
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from host_brain.core.ids import new_request_id

# ── service 层 import ──────────────────────────────────────────────────────
from host_brain.app.input_service_log import (
    get_ledger_safe as _get_ledger_safe,
    build_reply_preview as _build_reply_preview,
    build_input_preview as _build_input_preview,
    log_input_event as _log_input_event,
    log_dispatch_task_exception as _log_dispatch_task_exception,
    get_request_skill_exec_rows as _get_request_skill_exec_rows,
)
from host_brain.app.input_service_gateway import (
    looks_like_progress_query as _looks_like_progress_query_for_ack_skip,
    looks_like_execution_intent as _looks_like_execution_intent_for_ack,
    normalize_route_confidence as _normalize_route_confidence,
)
from host_brain.tribe.cortex_route_decider import classify_task_intent as _classify_task_intent
from host_brain.app.input_service_ack import (
    resolve_bond_stage_for_ack as _resolve_bond_stage_for_ack,
    resolve_emotion_tag_for_ack as _resolve_emotion_tag_for_ack,
    format_elapsed_compact as _format_elapsed_compact,
    should_send_gateway_immediate_ack as _should_send_gateway_immediate_ack_impl,
    deliver_gateway_immediate_ack as _deliver_gateway_immediate_ack,
    log_gateway_ack_task_exception as _log_gateway_ack_task_exception,
    build_gateway_progress_reply_text as _build_gateway_progress_reply_text,
    log_progress_push_task_exception as _log_progress_push_task_exception,
)
from host_brain.app.input_service_quality import (
    analyze_request_execution_quality as _analyze_request_execution_quality,
    detect_gateway_execution_deviation as _detect_gateway_execution_deviation_impl,
    build_realign_user_message as _build_realign_user_message,
    maybe_send_gateway_realign_notice as _maybe_send_gateway_realign_notice_impl,
    compute_keyword_overlap as _compute_keyword_overlap,
)

_logger = logging.getLogger("Natsume.input_routes")

router = APIRouter(prefix="/v1/host", tags=["input"])

# ASR 相关常量（未迁移，仅在本文件使用）
_PLACEHOLDER_GATEWAY_CONVERSATIONS: set[str] = {
    "active_chat", "active chat", "current_chat", "current chat",
    "current_session", "default", "unknown", "none", "null",
    "-", "_", "na", "n/a", "当前聊天", "当前会话",
}
_ASR_PLACEHOLDER_PATTERN = re.compile(r"^\[[A-Z0-9_:\-]{3,}\]$")
_ASR_SPEECH_CHAR_PATTERN = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]")


class InputPayload(BaseModel):
    """Ingress payload reported by Edge."""

    source: str = Field(..., description="Input source: ASR or GATEWAY")
    person_id: str | None = Field(None, description="Optional canonical person id")

    session_id: str | None = Field(None, description="[ASR] Session ID")
    text: str | None = Field(None, description="[ASR] Transcript")
    confidence: float | None = Field(None, description="[ASR] Confidence 0~1")
    is_final: bool | None = Field(None, description="[ASR] Final segment")
    seq: int | None = Field(None, description="[ASR] Sequence")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best candidates")
    evidence_ref: str | None = Field(None, description="[ASR] Evidence ref")

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


# ── 本文件保留的 ASR 相关工具函数（未迁移，仅在本文件使用）──────────────────

def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float_env(name: str, *, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _parse_int_env(name: str, *, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _looks_like_asr_placeholder_text(text: str) -> bool:
    normalized = str(text or "").strip().upper()
    if not normalized:
        return True
    return _ASR_PLACEHOLDER_PATTERN.fullmatch(normalized) is not None


def _contains_speech_chars(text: str) -> bool:
    return _ASR_SPEECH_CHAR_PATTERN.search(str(text or "")) is not None


def _evaluate_asr_noise_gate(
    *,
    text: str,
    payload: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    text_value = str(text or "").strip()
    if not text_value:
        return False, "ASR_TEXT_EMPTY"
    if _looks_like_asr_placeholder_text(text_value):
        return False, "ASR_PLACEHOLDER_TEXT"
    min_text_len = max(1, _parse_int_env("HOST_INPUT_ASR_MIN_TEXT_LEN", default=2))
    if len(text_value) < min_text_len:
        return False, "ASR_TEXT_TOO_SHORT"
    require_speech_chars = _parse_bool_env("HOST_INPUT_ASR_REQUIRE_SPEECH_CHARS", default=True)
    if require_speech_chars and not _contains_speech_chars(text_value):
        return False, "ASR_NON_SPEECH_TEXT"
    payload_dict = dict(payload or {})
    reason_code = str(payload_dict.get("reason_code") or "").strip().upper()
    if reason_code in {"ASR_VAD_NO_SPEECH", "ASR_LOW_CONFIDENCE"}:
        return False, reason_code
    low_confidence = _coerce_bool(payload_dict.get("low_confidence"), default=False)
    if low_confidence:
        return False, "ASR_LOW_CONFIDENCE"
    min_confidence = max(0.0, min(1.0, _parse_float_env("HOST_INPUT_ASR_MIN_CONFIDENCE", default=0.75)))
    require_confidence = _parse_bool_env("HOST_INPUT_ASR_REQUIRE_CONFIDENCE", default=False)
    confidence = _coerce_float(payload_dict.get("confidence"))
    if confidence is None and require_confidence:
        return False, "ASR_CONFIDENCE_MISSING"
    if confidence is not None and confidence < min_confidence:
        return False, "ASR_LOW_CONFIDENCE"
    return True, None


def _is_invalid_gateway_conversation_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_GATEWAY_CONVERSATIONS


def _evaluate_immune_gate_for_input(
    *,
    text: str,
    source: str,
    session_id: str,
    person_id: str | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
    return get_immune_agent().inspect_text(
        text=text,
        source=f"http.v1.host.input.{str(source or '').lower()}",
        metadata={
            "session_id": str(session_id or "").strip() or None,
            "person_id": str(person_id or "").strip() or None,
        },
    )


class _NoopMetrics:
    """Minimal metrics shim for cortex orchestrator direct invocation."""
    def __getattr__(self, _name: str):
        def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None
        return _noop


def _should_send_gateway_immediate_ack(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """包装 service 层函数，传入本文件的判断函数。"""
    return _should_send_gateway_immediate_ack_impl(
        source=source,
        text=text,
        payload=payload,
        looks_like_progress_query_fn=_looks_like_progress_query_for_ack_skip,
        looks_like_execution_intent_fn=_looks_like_execution_intent_for_ack,
    )


def _detect_gateway_execution_deviation(
    *,
    source: str,
    user_text: str,
    decision: str,
    narrative: str,
) -> dict[str, Any]:
    """包装 service 层函数，传入本文件的判断函数。"""
    return _detect_gateway_execution_deviation_impl(
        source=source,
        user_text=user_text,
        decision=decision,
        narrative=narrative,
        looks_like_execution_intent_fn=_looks_like_execution_intent_for_ack,
    )


async def _maybe_send_gateway_realign_notice(
    *,
    request_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """包装 service 层函数，传入本文件的上下文解析函数。"""
    return await _maybe_send_gateway_realign_notice_impl(
        request_id=request_id,
        payload=payload,
        resolve_bond_stage_fn=_resolve_bond_stage_for_ack,
        resolve_emotion_tag_fn=_resolve_emotion_tag_for_ack,
    )


@router.post("/input", response_model=InputResponse)
async def receive_input(body: InputPayload, request: Request) -> InputResponse:


def _parse_int_env(name: str, *, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _gateway_auto_reply_enabled() -> bool:
    return _parse_bool_env("HOST_INPUT_GATEWAY_AUTO_REPLY", default=True)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _looks_like_asr_placeholder_text(text: str) -> bool:
    normalized = str(text or "").strip().upper()
    if not normalized:
        return True
    return _ASR_PLACEHOLDER_PATTERN.fullmatch(normalized) is not None


def _contains_speech_chars(text: str) -> bool:
    return _ASR_SPEECH_CHAR_PATTERN.search(str(text or "")) is not None


def _evaluate_asr_noise_gate(
    *,
    text: str,
    payload: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    text_value = str(text or "").strip()
    if not text_value:
        return False, "ASR_TEXT_EMPTY"
    if _looks_like_asr_placeholder_text(text_value):
        return False, "ASR_PLACEHOLDER_TEXT"

    min_text_len = max(1, _parse_int_env("HOST_INPUT_ASR_MIN_TEXT_LEN", default=2))
    if len(text_value) < min_text_len:
        return False, "ASR_TEXT_TOO_SHORT"

    require_speech_chars = _parse_bool_env("HOST_INPUT_ASR_REQUIRE_SPEECH_CHARS", default=True)
    if require_speech_chars and not _contains_speech_chars(text_value):
        return False, "ASR_NON_SPEECH_TEXT"

    payload_dict = dict(payload or {})
    reason_code = str(payload_dict.get("reason_code") or "").strip().upper()
    if reason_code in {"ASR_VAD_NO_SPEECH", "ASR_LOW_CONFIDENCE"}:
        return False, reason_code

    low_confidence = _coerce_bool(payload_dict.get("low_confidence"), default=False)
    if low_confidence:
        return False, "ASR_LOW_CONFIDENCE"

    min_confidence = max(
        0.0,
        min(1.0, _parse_float_env("HOST_INPUT_ASR_MIN_CONFIDENCE", default=0.75)),
    )
    require_confidence = _parse_bool_env("HOST_INPUT_ASR_REQUIRE_CONFIDENCE", default=False)
    confidence = _coerce_float(payload_dict.get("confidence"))
    if confidence is None and require_confidence:
        return False, "ASR_CONFIDENCE_MISSING"
    if confidence is not None and confidence < min_confidence:
        return False, "ASR_LOW_CONFIDENCE"

    return True, None


def _is_invalid_gateway_conversation_id(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    return normalized in _PLACEHOLDER_GATEWAY_CONVERSATIONS


def _evaluate_immune_gate_for_input(
    *,
    text: str,
    source: str,
    session_id: str,
    person_id: str | None = None,
) -> dict[str, Any]:
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

    return get_immune_agent().inspect_text(
        text=text,
        source=f"http.v1.host.input.{str(source or '').lower()}",
        metadata={
            "session_id": str(session_id or "").strip() or None,
            "person_id": str(person_id or "").strip() or None,
        },
    )


def _parse_sse_data_event(raw_event: str) -> dict[str, Any] | None:
    """解析 SSE data 事件行，返回 JSON 对象或 None。"""
    line = str(raw_event or "").strip()
    if not line.startswith("data:"):
        return None
    payload_str = line.removeprefix("data:").strip()
    if not payload_str:
        return None
    import json  # noqa: PLC0415
    try:
        obj = json.loads(payload_str)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _build_gateway_reply_text(
    *,
    decision: str,
    narrative: str,
    reason_code: str | None,
) -> str:
    """构建 gateway 最终回复文本。"""
    from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415
    templates_raw = resolve_prompt_template_json_merged("input_routes.fallback_texts")
    templates = dict(templates_raw) if isinstance(templates_raw, dict) else {}
    reply_text = str(narrative or "").strip()
    if reply_text:
        return reply_text
    if str(decision or "").upper() != "APPROVE":
        return str(templates.get("reject_default") or "")
    _ = reason_code
    return str(templates.get("approve_empty") or "")


# ── gateway 路由函数已迁移到 input_service_gateway.py ──────────────────────
# 本文件通过顶部 import 使用 service 层函数


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

    payload = body.model_dump(exclude_none=True)
    if source == "ASR":
        allow_asr, reject_reason = _evaluate_asr_noise_gate(text=text, payload=payload)
        if not allow_asr:
            _logger.info(
                "input_routes: ASR ignored by noise gate request_id=%s reason=%s",
                request_id,
                reject_reason,
            )
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="IGNORED",
                detail=f"ASR ignored by noise gate: {reject_reason}",
                ts_ms=ts_ms,
            )

    immune_result: dict[str, Any] = {}
    try:
        immune_result = _evaluate_immune_gate_for_input(
            text=text,
            source=source,
            session_id=session_id,
            person_id=str(body.person_id or "").strip() or None,
        )
    except Exception as exc:
        _logger.warning("input_routes: immune gate evaluate degraded request_id=%s: %s", request_id, exc)
        immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    payload["immune_gate"] = {
        "decision": immune_decision,
        "risk_level": immune_result.get("risk_level"),
        "score": immune_result.get("score"),
        "reason_codes": list(immune_result.get("reason_codes") or []),
    }
    if immune_decision == "BLOCK":
        _log_input_event(
            ledger=_get_ledger_safe(),
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=payload,
            phase="REJECTED",
            status="REJECTED",
            reason_code="IMMUNE_BLOCKED_HIGH_RISK",
            detail="immune gate blocked high-risk input",
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail="immune gate blocked high-risk input",
            ts_ms=ts_ms,
        )
    if immune_decision == "REPORT":
        _logger.warning(
            "input_routes: immune gate report request_id=%s source=%s reasons=%s",
            request_id,
            source,
            immune_result.get("reason_codes"),
        )

    try:
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
        from host_brain.app.dependencies import notify_user_interaction  # noqa: PLC0415

        payload["person_id"] = resolve_person_id(
            str(body.person_id or "").strip() or None,
            session_id=session_id,
            platform=str(body.platform or "").strip() or None,
            from_id=str(body.from_id or "").strip() or None,
            conversation_id=str(body.conversation_id or "").strip() or None,
        )
        notify_user_interaction()
    except Exception:
        pass

    if source == "GATEWAY":
        handled_by_progress_fastpath = await _try_handle_gateway_progress_fastpath(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ledger=_get_ledger_safe(),
            ts_ms=ts_ms,
        )
        if handled_by_progress_fastpath:
            return InputResponse(
                success=True,
                request_id=request_id,
                source=source,
                status="COMPLETED",
                detail="gateway progress fastpath replied",
                ts_ms=int(time.time() * 1000),
            )

    try:
        await _dispatch_to_graph(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
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


async def _try_handle_gateway_progress_fastpath(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
    ts_ms: int,
) -> bool:
    if not _gateway_auto_reply_enabled():
        return False
    if str(source or "").strip().upper() != "GATEWAY":
        return False
    if not _parse_bool_env("HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED", default=True):
        return False
    if not _looks_like_progress_query_for_ack_skip(text):
        return False

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "progress_fastpath_reason": "PROGRESS_TARGET_MISSING"},
            phase="PROGRESS_FASTPATH_SKIPPED",
            status="IGNORED",
            reason_code="PROGRESS_TARGET_MISSING",
            detail="gateway progress fastpath skipped: missing platform/conversation_id",
            ts_ms=ts_ms,
        )
        return False

    try:
        from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415
    except Exception:
        return False

    try:
        reply_text, reason, _summary = _build_gateway_progress_reply_text(
            request_id=request_id,
            session_id=session_id,
            payload=payload,
            exclude_request_id=request_id,
        )
    except Exception as exc:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "progress_fastpath_reason": "PROGRESS_BUILD_FAILED"},
            phase="PROGRESS_FASTPATH_FAILED",
            status="FAILED",
            reason_code="PROGRESS_BUILD_FAILED",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            ts_ms=int(time.time() * 1000),
        )
        return False

    wait_result = _parse_bool_env("HOST_GATEWAY_PROGRESS_FASTPATH_WAIT_RESULT", default=True)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_PROGRESS_FASTPATH_TIMEOUT_MS", default=6000))
    started_ms = int(time.time() * 1000)
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=reply_text,
        msg_type="text",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    success = bool(getattr(result, "success", False))
    finished_ms = int(time.time() * 1000)
    latency_ms = max(0, finished_ms - started_ms)
    payload_with_result = dict(payload or {})
    payload_with_result["progress_fastpath_status"] = str(getattr(result, "status", "UNKNOWN"))
    payload_with_result["progress_fastpath_latency_ms"] = int(getattr(result, "latency_ms", 0))
    payload_with_result["progress_fastpath_preview"] = _build_reply_preview(reply_text, limit=100)
    payload_with_result["progress_fastpath_reason"] = reason

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload_with_result,
        phase="PROGRESS_FASTPATH",
        status=("OK" if success else "DEGRADED"),
        reason_code=(None if success else str(getattr(result, "reason_code", "") or "PROGRESS_REPLY_FAILED")),
        detail=f"gateway progress fastpath status={getattr(result, 'status', 'UNKNOWN')} success={success}",
        latency_ms=latency_ms,
        ts_ms=finished_ms,
    )
    return success


def _should_start_gateway_progress_push(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    if not _gateway_auto_reply_enabled():
        return False, "PROGRESS_PUSH_AUTO_REPLY_DISABLED"
    if not _parse_bool_env("HOST_GATEWAY_PROGRESS_PUSH_ENABLED", default=True):
        return False, "PROGRESS_PUSH_DISABLED"
    if str(source or "").strip().upper() != "GATEWAY":
        return False, "PROGRESS_PUSH_SOURCE_NOT_GATEWAY"
    if _looks_like_progress_query_for_ack_skip(text):
        return False, "PROGRESS_PUSH_PROGRESS_QUERY"
    if not _looks_like_execution_intent_for_ack(text):
        return False, "PROGRESS_PUSH_NON_ACTION_INTENT"
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return False, "PROGRESS_PUSH_TARGET_MISSING"
    return True, "PROGRESS_PUSH_ENABLED"


async def _run_gateway_proactive_progress_push(
    *,
    request_id: str,
    session_id: str,
    source: str,
    payload: dict[str, Any],
    ledger: Any,
    stop_event: asyncio.Event,
) -> None:
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return

    warmup_ms = max(0, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_WARMUP_MS", default=15000))
    interval_ms = max(2000, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_INTERVAL_MS", default=15000))
    max_updates = max(1, min(8, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_MAX_UPDATES", default=2)))
    wait_result = _parse_bool_env("HOST_GATEWAY_PROGRESS_PUSH_WAIT_RESULT", default=False)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_PROGRESS_PUSH_TIMEOUT_MS", default=4000))

    if warmup_ms > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(warmup_ms) / 1000.0)
            return
        except asyncio.TimeoutError:
            pass

    sent_count = 0
    last_signature = ""
    while not stop_event.is_set() and sent_count < max_updates:
        reply_text, reason, summary = _build_gateway_progress_reply_text(
            request_id=request_id,
            session_id=session_id,
            payload=payload,
            exclude_request_id=None,
        )
        summary_dict = dict(summary or {})
        status = str(summary_dict.get("status") or "NONE").strip().upper()
        target_id = str(summary_dict.get("target_id") or "").strip()
        progress_message = str(summary_dict.get("progress_message") or summary_dict.get("detail") or "").strip()
        signature = f"{status}|{target_id}|{progress_message}"

        if reason == "PROGRESS_SUMMARY_FOUND" and status in {"RUNNING", "RECOVERING"} and signature != last_signature:
            started_ms = int(time.time() * 1000)
            result = await post_gateway_reply(
                platform=platform,
                conversation_id=conversation_id,
                content=reply_text,
                msg_type="text",
                request_id=request_id,
                wait_result=wait_result,
                timeout_ms=timeout_ms,
            )
            success = bool(getattr(result, "success", False))
            finished_ms = int(time.time() * 1000)
            latency_ms = max(0, finished_ms - started_ms)
            push_payload = dict(payload or {})
            push_payload["progress_push_status"] = str(getattr(result, "status", "UNKNOWN"))
            push_payload["progress_push_reason"] = reason
            push_payload["progress_push_preview"] = _build_reply_preview(reply_text, limit=100)
            push_payload["progress_push_updates"] = sent_count + 1
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=push_payload,
                phase="PROGRESS_PUSHED",
                status=("OK" if success else "DEGRADED"),
                reason_code=(None if success else str(getattr(result, "reason_code", "") or "PROGRESS_PUSH_REPLY_FAILED")),
                detail=f"gateway progress push status={getattr(result, 'status', 'UNKNOWN')} success={success}",
                latency_ms=latency_ms,
                ts_ms=finished_ms,
            )
            sent_count += 1
            last_signature = signature
        elif reason == "PROGRESS_SUMMARY_FOUND" and status in {"COMPLETED", "FAILED"}:
            return

        if sent_count >= max_updates:
            return

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(interval_ms) / 1000.0)
            return
        except asyncio.TimeoutError:
            continue


async def _stop_progress_push_task(
    *,
    stop_event: asyncio.Event | None,
    task: asyncio.Task | None,
) -> None:
    if stop_event is not None:
        stop_event.set()
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=0.4)
    except asyncio.TimeoutError:
        task.cancel()
    except Exception:
        return


def _maybe_schedule_gateway_immediate_ack(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ledger: Any,
) -> None:
    should_send, reason = _should_send_gateway_immediate_ack(source=source, text=text, payload=payload)
    now_ms = int(time.time() * 1000)
    if not should_send:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "immediate_ack_reason": reason},
            phase="ACK_SKIPPED",
            status="IGNORED",
            reason_code=reason,
            detail=f"gateway immediate ack skipped: {reason}",
            ts_ms=now_ms,
        )
        return

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=payload,
        phase="ACK_PENDING",
        status="ACCEPTED",
        detail="gateway immediate ack scheduled",
        ts_ms=now_ms,
    )

    async def _runner() -> None:
        start_ms = int(time.time() * 1000)
        try:
            ack_result = await _deliver_gateway_immediate_ack(
                request_id=request_id,
                payload=payload,
                text=text,
            )
        except Exception as exc:
            finish_ms = int(time.time() * 1000)
            latency_ms = max(0, finish_ms - start_ms)
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="ACK_FAILED",
                status="FAILED",
                reason_code="ACK_DELIVERY_EXCEPTION",
                detail=f"{type(exc).__name__}: {str(exc)[:200]}",
                latency_ms=latency_ms,
                ts_ms=finish_ms,
            )
            return

        finish_ms = int(time.time() * 1000)
        latency_ms = max(0, finish_ms - start_ms)
        ack_payload = dict(payload or {})
        ack_payload["immediate_ack_status"] = ack_result.get("status")
        ack_payload["immediate_ack_latency_ms"] = ack_result.get("latency_ms")
        ack_payload["immediate_ack_preview"] = ack_result.get("ack_preview")
        ack_payload["immediate_ack_reason"] = ack_result.get("reason_code")
        ack_success = bool(ack_result.get("success"))
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=ack_payload,
            phase=("ACKED" if ack_success else "ACK_FAILED"),
            status=("OK" if ack_success else "FAILED"),
            reason_code=(str(ack_result.get("reason_code") or "") or None),
            detail=f"gateway immediate ack status={ack_result.get('status')} success={ack_success}",
            latency_ms=latency_ms,
            ts_ms=finish_ms,
        )

    asyncio.create_task(_runner()).add_done_callback(_log_gateway_ack_task_exception)


async def _run_cortex_orchestrator_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    from host_brain.app.emotion_context import (  # noqa: PLC0415
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415
    from host_brain.app.schemas import ChatRequest  # noqa: PLC0415
    from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator  # noqa: PLC0415

    reply_channel = str(payload.get("platform") or "").strip() or None
    reply_target = str(payload.get("conversation_id") or "").strip() or None
    person_id = resolve_person_id(
        str(payload.get("person_id") or "").strip() or None,
        session_id=session_id,
        platform=reply_channel,
        from_id=str(payload.get("from_id") or "").strip() or None,
        conversation_id=reply_target,
    )
    request_body = ChatRequest(
        message=text,
        person_id=person_id,
        session_id=session_id,
        input_source=source,
        reply_channel=reply_channel,
        reply_target=reply_target,
        # Phase XV：Gateway 回复时 TTS/VTS 与 ASR 链路一致，Natsume 在微信里回复时本地同步发声 + VTS 动画
        suppress_edge_tts=None,
        suppress_edge_vts=None,
        # Phase XV P4：Gateway 通道文字永远中文，注入 render_target_lang
        # ASR/Web 通道保持 None，由 infer_render_target_lang() 推断（向后兼容）
        render_target_lang=("zh" if source == "GATEWAY" else None),
    )
    emotion_context = resolve_chat_emotion_context(request_body=request_body)
    emotion_snapshot = build_chat_emotion_snapshot(
        emotion_context=emotion_context,
        source=f"input_routes.{str(source or '').lower()}",
        ts_ms=ts_ms,
    )
    intent_draft = await _classify_task_intent(
        request_id=request_id,
        session_id=session_id,
        text=text,
    )
    # 将 classify_task_intent 结果包装为 intent_draft 格式
    _route = str(intent_draft.get("route") or "chat").strip().lower()
    _target_id = str(intent_draft.get("target_id") or "").strip()
    _action = "narrative_only" if _route == "chat" else (_target_id or _route)
    intent_draft = {
        "id": f"intent::{request_id}",
        "action": _action,
        "source": "input_routes",
        "input_source": source,
        "route": _route,
        "target_id": _target_id,
        "task": str(intent_draft.get("task") or text).strip(),
        "route_reason": str(intent_draft.get("reason_code") or "TRIBE_INTENT").strip(),
        "route_source": str(intent_draft.get("route_source") or "rule").strip(),
        "route_confidence": _normalize_route_confidence(intent_draft.get("confidence")),
        "llm_route_used": str(intent_draft.get("route_source") or "") == "llm",
    }
    if _route == "delegate" and _target_id:
        intent_draft["delegate_agent"] = _target_id
        intent_draft["agent_id"] = _target_id
    elif _route == "skill" and _target_id:
        intent_draft["skill_id"] = _target_id
        intent_draft["skill_name"] = _target_id
    governor_result: dict[str, Any] = {"decision": "APPROVE", "source": "input_routes"}

    commit_event: dict[str, Any] | None = None
    async for event in cortex_orchestrator_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=request_id,
        metrics=_NoopMetrics(),
    ):
        parsed = _parse_sse_data_event(event)
        if parsed and str(parsed.get("phase") or "").upper() == "COMMIT":
            commit_event = parsed

    if commit_event is None:
        raise RuntimeError("cortex_orchestrator_commit_missing")

    return {
        "decision": str(commit_event.get("decision") or "").strip().upper() or "REJECT",
        "reason_code": str(commit_event.get("reason_code") or "").strip() or None,
        "narrative": str(commit_event.get("narrative") or "").strip(),
        "commit_event": commit_event,
        "intent_draft": dict(intent_draft or {}),
        "route_decision": {
            "route": str(intent_draft.get("route") or "chat").strip().lower() or "chat",
            "target_id": str(intent_draft.get("target_id") or "").strip(),
            "task": str(intent_draft.get("task") or "").strip(),
            "reason_code": str(intent_draft.get("route_reason") or "").strip() or None,
            "source": str(intent_draft.get("route_source") or "").strip() or None,
            "confidence": _normalize_route_confidence(intent_draft.get("route_confidence")),
            "override_reason": str(intent_draft.get("route_override_reason") or "").strip() or None,
            "llm_route_used": bool(intent_draft.get("llm_route_used")),
        },
    }


async def _run_cortex_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    return await _run_cortex_orchestrator_pipeline(
        request_id=request_id,
        session_id=session_id,
        source=source,
        text=text,
        payload=payload,
        ts_ms=ts_ms,
    )


async def _maybe_realign_gateway_pipeline(
    *,
    request_id: str,
    session_id: str,
    source: str,
    user_text: str,
    payload: dict[str, Any],
    ts_ms: int,
    pipeline_result: dict[str, Any],
    ledger: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
    narrative = str(pipeline_result.get("narrative") or "").strip()
    detection = _detect_gateway_execution_deviation(
        source=source,
        user_text=user_text,
        decision=decision,
        narrative=narrative,
    )
    if not bool(detection.get("triggered")):
        return pipeline_result, detection

    now_ms = int(time.time() * 1000)
    detection_payload = dict(payload or {})
    detection_payload["deviation_reason"] = detection.get("reason_code")
    detection_payload["deviation_overlap_ratio"] = detection.get("overlap_ratio")
    detection_payload["deviation_keyword_count"] = detection.get("keyword_count")
    detection_payload["deviation_hit_count"] = detection.get("hit_count")
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        input_payload=detection_payload,
        phase="DEVIATION_DETECTED",
        status="DEGRADED",
        reason_code=str(detection.get("reason_code") or "DEVIATION_DETECTED"),
        detail=(
            f"overlap={detection.get('overlap_ratio')} "
            f"keywords={detection.get('keyword_count')} hits={detection.get('hit_count')}"
        ),
        ts_ms=now_ms,
    )

    notice_result = await _maybe_send_gateway_realign_notice(
        request_id=request_id,
        payload=payload,
    )
    detection["notice"] = notice_result

    if not _parse_bool_env("HOST_GATEWAY_DEVIATION_RETRY_ENABLED", default=True):
        detection["retry_applied"] = False
        detection["retry_reason"] = "DEVIATION_RETRY_DISABLED"
        return pipeline_result, detection

    try:
        retry_message = _build_realign_user_message(
            original_user_text=user_text,
            first_narrative=narrative,
        )
        retry_result = await _run_cortex_orchestrator_pipeline(
            request_id=f"{request_id}::realign",
            session_id=session_id,
            source=source,
            text=retry_message,
            payload=payload,
            ts_ms=int(time.time() * 1000),
        )
    except Exception as exc:
        detection["retry_applied"] = False
        detection["retry_reason"] = "DEVIATION_RETRY_FAILED"
        detection["retry_error"] = f"{type(exc).__name__}:{str(exc)[:160]}"
        return pipeline_result, detection

    retry_decision = str(retry_result.get("decision") or "REJECT").strip().upper()
    retry_narrative = str(retry_result.get("narrative") or "").strip()
    retry_overlap, retry_kw_count, retry_hit_count = _compute_keyword_overlap(
        source_text=user_text,
        target_text=retry_narrative,
    )
    min_overlap = float(detection.get("min_overlap") or _parse_float_env("HOST_GATEWAY_DEVIATION_MIN_OVERLAP", default=0.18))
    retry_ok = retry_decision == "APPROVE" and bool(retry_narrative) and retry_overlap >= min_overlap
    detection["retry_applied"] = retry_ok
    detection["retry_overlap_ratio"] = retry_overlap
    detection["retry_keyword_count"] = retry_kw_count
    detection["retry_hit_count"] = retry_hit_count

    if retry_ok:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={**dict(payload or {}), "deviation_retry_overlap_ratio": retry_overlap},
            phase="DEVIATION_RECOVERED",
            status="OK",
            reason_code="DEVIATION_RECOVERED",
            detail=f"retry decision={retry_decision} overlap={retry_overlap}",
            ts_ms=int(time.time() * 1000),
        )
        return retry_result, detection
    return pipeline_result, detection


async def _deliver_gateway_reply(
    *,
    request_id: str,
    payload: dict[str, Any],
    decision: str,
    reason_code: str | None,
    narrative: str,
    # Phase XV P4：ReplyFormDecider 所需的上下文
    emotion_tag: str = "NEUTRAL",
    bond_stage: str = "STRANGER",
    user_msg_type: str = "text",
    # Phase XV P6：双轨语言，Gateway 文字永远中文（narrative 已是中文）
    # tts_text_ja 为日语 TTS 文本（双语输出时使用，当前为占位）
    tts_text_ja: str | None = None,
) -> dict[str, Any]:
    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()

    reply_text = _build_gateway_reply_text(
        decision=decision,
        narrative=narrative,
        reason_code=reason_code,
    )
    preview = _build_reply_preview(reply_text, limit=120)
    if not _gateway_auto_reply_enabled():
        return {
            "success": True,
            "status": "SKIPPED",
            "reason_code": "GATEWAY_REPLY_DISABLED",
            "detail": "gateway reply skipped: HOST_INPUT_GATEWAY_AUTO_REPLY=false",
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "auto_reply_disabled",
            "voice_sent": False,
        }
    if not platform or not conversation_id:
        return {
            "success": False,
            "status": "FAILED",
            "reason_code": "GATEWAY_REPLY_MISSING_ROUTE",
            "detail": "gateway reply skipped: missing platform or conversation_id",
            "latency_ms": 0,
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": "text",
            "reply_form_reason": "missing_route",
            "voice_sent": False,
        }

    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415
    from host_brain.app.reply_form_decider import (  # noqa: PLC0415
        get_reply_form_decider, MsgType,
    )

    # ── Phase XV P4：ReplyFormDecider ──────────────────────────────────
    # 将用户消息类型字符串转换为 MsgType
    try:
        user_mtype = MsgType(str(user_msg_type or "text").strip().lower())
    except ValueError:
        user_mtype = MsgType.TEXT

    decider = get_reply_form_decider()
    form_decision = decider.decide(
        user_msg_type=user_mtype,
        narrative_zh=reply_text,
        emotion_tag=emotion_tag,
        bond_stage=bond_stage,
    )

    # ── 主回复（文字/语音）─────────────────────────────────────────────
    primary_content = reply_text
    primary_msg_type = form_decision.primary.value

    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=primary_content,
        msg_type=primary_msg_type,
        request_id=request_id,
        wait_result=True,
        timeout_ms=12000,
    )
    if not bool(getattr(result, "success", False)):
        status = str(getattr(result, "status", "FAILED"))
        rc = str(getattr(result, "reason_code", "") or "") or "GATEWAY_REPLY_FAILED"
        detail = str(getattr(result, "detail", "") or "")
        return {
            "success": False,
            "status": status,
            "reason_code": rc,
            "detail": detail,
            "latency_ms": int(getattr(result, "latency_ms", 0)),
            "reply_text_len": len(reply_text),
            "reply_preview": preview,
            "reply_form": form_decision.primary.value,
            "reply_form_reason": form_decision.reason,
            "voice_sent": False,
        }

    # ── 双发模式：先文字字幕，再日语语音（不阻塞主回复）────────────
    voice_sent = False
    if form_decision.is_dual:
        # P3/P6 完整实现：用 tts_text_ja 做 TTS 合成后发语音
        # P4 当前：发送一条文字作为语音消息的占位（保留接口，等 P6 双语输出完成后启用真正的 TTS）
        _voice_text = tts_text_ja or reply_text  # P6 完成后改为只用 tts_text_ja
        if _voice_text and form_decision.secondary == MsgType.VOICE:
            try:
                # 异步发送，不等待结果，不阻塞主回复
                import asyncio as _asyncio  # noqa: PLC0415
                _asyncio.create_task(_send_gateway_voice_reply(
                    platform=platform,
                    conversation_id=conversation_id,
                    tts_text=_voice_text,
                    request_id=request_id,
                    emotion_tag=emotion_tag,
                ))
                voice_sent = True
            except Exception:
                pass  # 语音发送失败不影响主回复

    return {
        "success": True,
        "status": str(getattr(result, "status", "OK")),
        "reason_code": None,
        "detail": str(getattr(result, "detail", "") or ""),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
        "reply_text_len": len(reply_text),
        "reply_preview": preview,
        "reply_form": form_decision.primary.value,
        "reply_form_reason": form_decision.reason,
        "voice_sent": voice_sent,
    }


async def _send_gateway_voice_reply(
    *,
    platform: str,
    conversation_id: str,
    tts_text: str,
    request_id: str,
    emotion_tag: str = "NEUTRAL",
) -> None:
    """向 Gateway 发送语音消息（异步，fire-and-forget）。

    Phase XV P4 当前实现：
      - 调用 Edge TTS 合成（post_speak），等待合成完成
      - 当 Edge 端支持 TTS→音频文件 后，改为发送语音文件
      - 当前 post_gateway_reply msg_type="voice" 需要 content 为音频 base64 或文件路径
      - 临时方案：发送 msg_type="text" 的语音字幕（标记 🎵 前缀区分）

    Phase XV P6/P7 完整实现：
      - Edge TTS 合成 → 返回音频文件路径/base64
      - post_gateway_reply(msg_type="voice", content=audio_data)
    """
    import logging as _logging  # noqa: PLC0415
    _vlogger = _logging.getLogger("Natsume.gateway_voice")
    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        # 触发 TTS 合成（本地扬声器）——Gateway 用户在外面听不到，
        # 但这样可以验证 TTS 合成链路正常工作
        # P6/P7 完成后：获取合成音频 → 发送 voice 消息
        _tts_text_normalized = str(tts_text or "").strip()
        if not _tts_text_normalized:
            return

        await post_speak(
            text=_tts_text_normalized,
            request_id=request_id,
            lang_hint="ja",  # Phase XV P6: 语音通道永远日语
            emotion_tag=emotion_tag,
        )
        _vlogger.debug(
            "gateway_voice TTS triggered (P4 placeholder): platform=%s len=%d",
            platform, len(_tts_text_normalized),
        )
    except Exception as exc:
        _vlogger.debug("gateway_voice send failed (non-critical): %s", exc)


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
    source_upper = str(source or "").upper().strip()
    if source_upper == "ASR":
        allow_asr, reject_reason = _evaluate_asr_noise_gate(text=text, payload=payload)
        if not allow_asr:
            ignored_ts = int(time.time() * 1000)
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="IGNORED",
                status="IGNORED",
                reason_code=reject_reason,
                detail=f"ASR ignored by noise gate: {reject_reason}",
                ts_ms=ignored_ts,
            )
            _logger.info(
                "input_routes: dispatch ignored request_id=%s source=%s reason=%s",
                request_id,
                source_upper,
                reject_reason,
            )
            return

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
    if source_upper == "GATEWAY":
        _maybe_schedule_gateway_immediate_ack(
            request_id=request_id,
            session_id=session_id,
            source=source_upper,
            text=text,
            payload=payload,
            ledger=ledger,
        )

    if run_in_thread:
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
        return

    await _dispatch_to_graph_worker(
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
    """Run cortex orchestrator pipeline and optionally deliver Gateway reply."""

    dispatch_ts = int(time.time() * 1000)
    source_upper = str(source or "").strip().upper()
    progress_push_stop_event: asyncio.Event | None = None
    progress_push_task: asyncio.Task | None = None
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
    if source_upper == "GATEWAY":
        should_push, push_reason = _should_start_gateway_progress_push(
            source=source_upper,
            text=text,
            payload=payload,
        )
        if should_push:
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload=payload,
                phase="PROGRESS_PUSH_PENDING",
                status="ACCEPTED",
                detail="gateway proactive progress push scheduled",
                ts_ms=dispatch_ts,
            )
            progress_push_stop_event = asyncio.Event()
            progress_push_task = asyncio.create_task(
                _run_gateway_proactive_progress_push(
                    request_id=request_id,
                    session_id=session_id,
                    source=source,
                    text=text,
                    payload=payload,
                    ledger=ledger,
                    stop_event=progress_push_stop_event,
                )
            )
            progress_push_task.add_done_callback(_log_progress_push_task_exception)
        else:
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                input_payload={**dict(payload or {}), "progress_push_reason": push_reason},
                phase="PROGRESS_PUSH_SKIPPED",
                status="IGNORED",
                reason_code=push_reason,
                detail=f"gateway proactive progress push skipped: {push_reason}",
                ts_ms=dispatch_ts,
            )
    try:
        pipeline_result = await _run_cortex_orchestrator_pipeline(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        deviation_meta: dict[str, Any] | None = None
        if source_upper == "GATEWAY":
            pipeline_result, deviation_meta = await _maybe_realign_gateway_pipeline(
                request_id=request_id,
                session_id=session_id,
                source=source,
                user_text=text,
                payload=payload,
                ts_ms=ts_ms,
                pipeline_result=pipeline_result,
                ledger=ledger,
            )
        decision = str(pipeline_result.get("decision") or "REJECT").strip().upper()
        reason_code = str(pipeline_result.get("reason_code") or "").strip() or None
        narrative = str(pipeline_result.get("narrative") or "").strip()
        route_decision = dict(pipeline_result.get("route_decision") or {})
        route_type = str(route_decision.get("route") or "chat").strip().lower() or "chat"
        route_target = str(route_decision.get("target_id") or "").strip()
        route_task = str(route_decision.get("task") or "").strip()
        route_source = str(route_decision.get("source") or "").strip() or None
        route_reason = str(route_decision.get("reason_code") or "").strip() or None
        route_override_reason = str(route_decision.get("override_reason") or "").strip() or None
        route_confidence = _normalize_route_confidence(route_decision.get("confidence"))
        route_llm_used = bool(route_decision.get("llm_route_used"))

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload={
                **dict(payload or {}),
                "route_type": route_type,
                "route_target": route_target,
                "route_task": route_task,
                "route_source": route_source,
                "route_reason": route_reason,
                "route_confidence": route_confidence,
                "route_override_reason": route_override_reason,
                "llm_route_used": route_llm_used,
            },
            phase="ROUTE_DECIDED",
            status="OK",
            reason_code=route_reason,
            detail=(
                f"route={route_type} target={route_target or '-'} "
                f"source={route_source or '-'} llm_used={str(route_llm_used).lower()}"
            ),
            ts_ms=int(time.time() * 1000),
        )

        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
        progress_push_stop_event = None
        progress_push_task = None

        gateway_delivery: dict[str, Any] | None = None
        if source == "GATEWAY":
            # Phase XV P4：从 pipeline_result 提取情感和 Bond 上下文
            commit_event = pipeline_result.get("commit_event") or {}
            audit_refs = commit_event.get("audit_refs") or {}
            _emotion_tag = str(audit_refs.get("emotion_tag") or "NEUTRAL").strip().upper()
            # bond_stage 从 payload 的 bond_summary 取（若存在），否则用 STRANGER
            _bond_stage = str((payload.get("bond_summary") or {}).get("stage") or "STRANGER").strip().upper()
            _user_msg_type = str(payload.get("msg_type") or "text").strip().lower()

            try:
                gateway_delivery = await _deliver_gateway_reply(
                    request_id=request_id,
                    payload=payload,
                    decision=decision,
                    reason_code=reason_code,
                    narrative=narrative,
                    emotion_tag=_emotion_tag,
                    bond_stage=_bond_stage,
                    user_msg_type=_user_msg_type,
                )
            except Exception as exc:
                gateway_delivery = {
                    "success": False,
                    "status": "FAILED",
                    "reason_code": "GATEWAY_REPLY_EXCEPTION",
                    "detail": f"{type(exc).__name__}: {str(exc)[:180]}",
                    "latency_ms": 0,
                    "reply_text_len": 0,
                    "reply_preview": "",
                    "reply_form": "text",
                    "reply_form_reason": "exception",
                    "voice_sent": False,
                }

        complete_ts = int(time.time() * 1000)
        latency_ms = complete_ts - dispatch_ts
        completion_status = "OK" if decision == "APPROVE" else "DEGRADED"
        completion_reason_code = reason_code
        detail_parts = [
            f"cortex_orchestrator decision={decision}",
            f"mode={dispatch_mode}",
            f"thread_flag={str(bool(run_in_thread)).lower()}",
            f"latency_ms={latency_ms}",
        ]
        if reason_code:
            detail_parts.append(f"reason_code={reason_code}")
        detail_parts.append(f"route_type={route_type}")
        if route_target:
            detail_parts.append(f"route_target={route_target}")
        if route_source:
            detail_parts.append(f"route_source={route_source}")
        if route_reason:
            detail_parts.append(f"route_reason={route_reason}")
        if route_override_reason:
            detail_parts.append(f"route_override_reason={route_override_reason}")
        if route_confidence is not None:
            detail_parts.append(f"route_confidence={route_confidence:.3f}")
        detail_parts.append(f"route_llm_used={str(route_llm_used).lower()}")
        if isinstance(deviation_meta, dict):
            detail_parts.append(f"deviation_reason={deviation_meta.get('reason_code')}")
            detail_parts.append(f"deviation_retry_applied={bool(deviation_meta.get('retry_applied'))}")
            if deviation_meta.get("overlap_ratio") is not None:
                detail_parts.append(f"deviation_overlap={deviation_meta.get('overlap_ratio')}")
            if deviation_meta.get("retry_overlap_ratio") is not None:
                detail_parts.append(f"deviation_retry_overlap={deviation_meta.get('retry_overlap_ratio')}")
        if gateway_delivery is not None:
            gateway_success = bool(gateway_delivery.get("success", True))
            gateway_reason_code = str(gateway_delivery.get("reason_code") or "")
            gateway_detail = str(gateway_delivery.get("detail") or "")
            if not gateway_success:
                degrade_on_gateway_failure = _parse_bool_env(
                    "HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT",
                    default=True,
                )
                if degrade_on_gateway_failure:
                    completion_status = "DEGRADED"
                    if not completion_reason_code:
                        completion_reason_code = gateway_reason_code or "GATEWAY_REPLY_DELIVERY_FAILED"
                else:
                    detail_parts.append("gateway_failure_ignored=true")
            detail_parts.append(f"gateway_success={str(gateway_success).lower()}")
            detail_parts.append(f"gateway_status={gateway_delivery.get('status')}")
            detail_parts.append(f"gateway_latency_ms={gateway_delivery.get('latency_ms')}")
            detail_parts.append(f"reply_text_len={gateway_delivery.get('reply_text_len')}")
            detail_parts.append(f"reply_preview={gateway_delivery.get('reply_preview')}")
            if gateway_reason_code:
                detail_parts.append(f"gateway_reason_code={gateway_reason_code}")
            if gateway_detail:
                detail_parts.append(f"gateway_detail={_build_reply_preview(gateway_detail, limit=160)}")

        execution_quality = {
            "degraded": False,
            "reason_code": None,
            "detail_tags": [],
            "skill_exec_count": 0,
        }
        if (
            _parse_bool_env("HOST_INPUT_EXEC_QUALITY_GATE_ENABLED", default=True)
            and isinstance(pipeline_result.get("commit_event"), dict)
        ):
            execution_quality = _analyze_request_execution_quality(
                ledger=ledger,
                request_id=request_id,
                decision=decision,
                route_type=route_type,
                route_target=route_target,
            )
            if bool(execution_quality.get("degraded")):
                completion_status = "DEGRADED"
                if not completion_reason_code:
                    completion_reason_code = str(
                        execution_quality.get("reason_code") or "EXECUTION_QUALITY_DEGRADED"
                    )
            detail_parts.append(f"exec_quality_degraded={str(bool(execution_quality.get('degraded'))).lower()}")
            if execution_quality.get("reason_code"):
                detail_parts.append(f"exec_quality_reason_code={execution_quality.get('reason_code')}")
            for tag in list(execution_quality.get("detail_tags") or ()):
                if str(tag or "").strip():
                    detail_parts.append(f"exec_quality_tag={tag}")

        completed_payload = dict(payload or {})
        completed_payload["decision"] = decision
        if completion_reason_code:
            completed_payload["reason_code"] = completion_reason_code
        completed_payload["route_type"] = route_type
        completed_payload["route_target"] = route_target
        completed_payload["route_task"] = route_task
        completed_payload["route_source"] = route_source
        completed_payload["route_reason"] = route_reason
        completed_payload["route_confidence"] = route_confidence
        completed_payload["route_override_reason"] = route_override_reason
        completed_payload["llm_route_used"] = route_llm_used
        completed_payload["exec_quality_degraded"] = bool(execution_quality.get("degraded"))
        completed_payload["exec_quality_reason_code"] = execution_quality.get("reason_code")
        completed_payload["exec_quality_tags"] = list(execution_quality.get("detail_tags") or [])
        completed_payload["exec_quality_skill_exec_count"] = int(execution_quality.get("skill_exec_count") or 0)
        if isinstance(deviation_meta, dict):
            completed_payload["deviation_reason"] = deviation_meta.get("reason_code")
            completed_payload["deviation_overlap_ratio"] = deviation_meta.get("overlap_ratio")
            completed_payload["deviation_retry_applied"] = bool(deviation_meta.get("retry_applied"))
            completed_payload["deviation_retry_overlap_ratio"] = deviation_meta.get("retry_overlap_ratio")
        if gateway_delivery is not None:
            completed_payload["gateway_success"] = bool(gateway_delivery.get("success", True))
            completed_payload["gateway_status"] = gateway_delivery.get("status")
            completed_payload["gateway_reason_code"] = gateway_delivery.get("reason_code")
            completed_payload["gateway_detail"] = gateway_delivery.get("detail")
            completed_payload["gateway_latency_ms"] = gateway_delivery.get("latency_ms")
            completed_payload["reply_text_len"] = gateway_delivery.get("reply_text_len")
            completed_payload["reply_preview"] = gateway_delivery.get("reply_preview")

        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            input_payload=completed_payload,
            phase="COMPLETED",
            status=completion_status,
            reason_code=completion_reason_code,
            detail=" ".join(detail_parts),
            latency_ms=latency_ms,
            ts_ms=complete_ts,
        )
        _logger.info(
            "input_routes: dispatch completed request_id=%s source=%s decision=%s latency_ms=%d mode=%s",
            request_id,
            source,
            decision,
            latency_ms,
            dispatch_mode,
        )
    except Exception as exc:
        await _stop_progress_push_task(
            stop_event=progress_push_stop_event,
            task=progress_push_task,
        )
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
        _logger.error("input_routes: dispatch failed request_id=%s: %s", request_id, exc)
