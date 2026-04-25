"""input_service_ack.py — 输入路由 ACK/进度推送服务层。

从 input_routes.py 提取的即时 ACK、进度推送、Bond/情绪上下文解析相关函数。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from host_brain.app.input_service_log import build_reply_preview

_logger = logging.getLogger("Natsume.input_routes")


def _parse_bool_env(name: str, *, default: bool) -> bool:
    import os  # noqa: PLC0415
    val = str(os.environ.get(name, "")).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_env(name: str, *, default: int) -> int:
    import os  # noqa: PLC0415
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _gateway_auto_reply_enabled() -> bool:
    return _parse_bool_env("HOST_GATEWAY_AUTO_REPLY_ENABLED", default=True)


# ---------------------------------------------------------------------------
# Bond / 情绪上下文解析
# ---------------------------------------------------------------------------

def resolve_bond_stage_for_ack(*, payload: dict[str, Any]) -> str:
    """从 payload 解析 Bond 阶段，用于 ACK 文本渲染。"""
    stage = str((dict(payload.get("bond_summary") or {})).get("stage") or "").strip().upper()
    if stage:
        return stage
    person_id = str(payload.get("person_id") or "").strip()
    if not person_id:
        return "STRANGER"
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
        return get_social_brain_agent().resolve_bond_stage(person_id=person_id, default="STRANGER")
    except Exception:
        return "STRANGER"


def resolve_emotion_tag_for_ack(*, payload: dict[str, Any]) -> str:
    """从 payload 解析情绪标签，用于 ACK 文本渲染。"""
    for key in ("emotion_tag", "emotion", "mood"):
        value = str(payload.get(key) or "").strip().upper()
        if value:
            return value
    return "NEUTRAL"


def format_elapsed_compact(elapsed_ms: int) -> str:
    """将毫秒时长格式化为紧凑字符串（如 '1m30s'）。"""
    total_seconds = max(0, int(elapsed_ms // 1000))
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes, seconds = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


# ---------------------------------------------------------------------------
# 即时 ACK
# ---------------------------------------------------------------------------

def should_send_gateway_immediate_ack(
    *,
    source: str,
    text: str,
    payload: dict[str, Any],
    looks_like_progress_query_fn: Any,
    looks_like_execution_intent_fn: Any,
) -> tuple[bool, str]:
    """判断是否需要发送即时 ACK。"""
    return False, "ACK_RETIRED_REPLY_ENVELOPE_ONLY"
    if not _gateway_auto_reply_enabled():
        return False, "ACK_AUTO_REPLY_DISABLED"
    if not _parse_bool_env("HOST_GATEWAY_IMMEDIATE_ACK_ENABLED", default=True):
        return False, "ACK_DISABLED"
    if str(source or "").strip().upper() != "GATEWAY":
        return False, "ACK_SOURCE_NOT_GATEWAY"
    text_value = str(text or payload.get("content") or "").strip()
    if not text_value:
        return False, "ACK_EMPTY_TEXT"
    if looks_like_progress_query_fn(text_value):
        return False, "ACK_PROGRESS_QUERY"
    if not looks_like_execution_intent_fn(text_value):
        return False, "ACK_NON_ACTION_INTENT"
    return True, "ACK_ACTION_INTENT"


async def deliver_gateway_immediate_ack(
    *,
    request_id: str,
    payload: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    """发送即时 ACK 回复（非阻塞，失败静默）。"""
    return {
        "success": False,
        "status": "SKIPPED",
        "reason_code": "ACK_RETIRED_REPLY_ENVELOPE_ONLY",
        "latency_ms": 0,
        "ack_preview": "",
    }
    from host_brain.app.chat_agent import render_quick_gateway_ack_text  # noqa: PLC0415
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    platform = str(payload.get("platform") or "").strip().lower()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return {
            "success": False,
            "status": "SKIPPED",
            "reason_code": "ACK_TARGET_MISSING",
            "latency_ms": 0,
            "ack_preview": "",
        }

    ack_text = render_quick_gateway_ack_text(
        user_message=text,
        bond_stage=resolve_bond_stage_for_ack(payload=payload),
        emotion_tag=resolve_emotion_tag_for_ack(payload=payload),
        sender_name=str(payload.get("sender_name") or "").strip() or None,
    )
    normalized_ack = str(ack_text or "").strip()
    if not normalized_ack:
        return {
            "success": False,
            "status": "SKIPPED",
            "reason_code": "ACK_TEXT_EMPTY",
            "latency_ms": 0,
            "ack_preview": "",
        }

    wait_result = _parse_bool_env("HOST_GATEWAY_IMMEDIATE_ACK_WAIT_RESULT", default=False)
    timeout_ms = max(1000, _parse_int_env("HOST_GATEWAY_IMMEDIATE_ACK_TIMEOUT_MS", default=2500))
    result = await post_gateway_reply(
        platform=platform,
        conversation_id=conversation_id,
        content=normalized_ack,
        msg_type="text",
        request_id=request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    return {
        "success": bool(getattr(result, "success", False)),
        "status": str(getattr(result, "status", "UNKNOWN")),
        "reason_code": str(getattr(result, "reason_code", "") or ""),
        "latency_ms": int(getattr(result, "latency_ms", 0)),
        "ack_preview": build_reply_preview(normalized_ack, limit=60),
    }


def log_gateway_ack_task_exception(task: asyncio.Task) -> None:
    """记录即时 ACK 任务的静默异常。"""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.warning("input_routes: immediate gateway ack task failed silently: %s", exc, exc_info=exc)


# ---------------------------------------------------------------------------
# 进度回复
# ---------------------------------------------------------------------------

def build_gateway_progress_reply_text(
    *,
    request_id: str,
    session_id: str,
    payload: dict[str, Any],
    exclude_request_id: str | None = None,
) -> tuple[str, str, dict[str, Any] | None]:
    """构建进度回复文本，返回 (reply_text, reason, summary)。"""
    from host_brain.app.chat_agent import render_quick_gateway_progress_text  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    person_id = str(payload.get("person_id") or "").strip()
    summary = None
    if person_id:
        summary = get_runtime_task_registry().summarize_for_chat(
            person_id=person_id,
            session_id=session_id,
            exclude_request_id=exclude_request_id,
        )
    if not isinstance(summary, dict):
        return (
            render_quick_gateway_progress_text(
                status="NONE",
                bond_stage=resolve_bond_stage_for_ack(payload=payload),
                emotion_tag=resolve_emotion_tag_for_ack(payload=payload),
                sender_name=str(payload.get("sender_name") or "").strip() or None,
            ),
            "PROGRESS_NO_SUMMARY",
            None,
        )

    status = str(summary.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
    target_id = str(summary.get("target_id") or "").strip()
    progress_message = str(summary.get("progress_message") or summary.get("detail") or "").strip()
    result_text = str(summary.get("result_text") or "").strip()
    elapsed_ms = int(summary.get("elapsed_ms") or 0)
    if not progress_message and status in {"RUNNING", "RECOVERING"}:
        progress_message = f"已持续 {format_elapsed_compact(elapsed_ms)}。"
    reply_text = render_quick_gateway_progress_text(
        status=status,
        target_id=target_id,
        progress_message=progress_message,
        result_text=result_text,
        elapsed_ms=elapsed_ms,
        bond_stage=resolve_bond_stage_for_ack(payload=payload),
        emotion_tag=resolve_emotion_tag_for_ack(payload=payload),
        sender_name=str(payload.get("sender_name") or "").strip() or None,
    )
    return reply_text, "PROGRESS_SUMMARY_FOUND", dict(summary)


def log_progress_push_task_exception(task: asyncio.Task) -> None:
    """记录进度推送任务的静默异常。"""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.warning("input_routes: progress push task failed silently: %s", exc, exc_info=exc)


__all__ = [
    "resolve_bond_stage_for_ack",
    "resolve_emotion_tag_for_ack",
    "format_elapsed_compact",
    "should_send_gateway_immediate_ack",
    "deliver_gateway_immediate_ack",
    "log_gateway_ack_task_exception",
    "build_gateway_progress_reply_text",
    "log_progress_push_task_exception",
]
