"""input_service_log.py — 输入路由日志/审计服务层。

从 input_routes.py 提取的日志与审计相关函数，供 input_routes.py 和其他模块复用。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

_logger = logging.getLogger("Natsume.input_routes")


def get_ledger_safe() -> Any:
    """安全获取 ledger 实例，失败时返回 None。"""
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        return get_ledger()
    except Exception:
        return None


def build_reply_preview(text: str, *, limit: int = 120) -> str:
    """构建回复预览文本（截断超长内容）。"""
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)]}..."


def build_input_preview(payload: dict[str, Any] | None, *, source: str, limit: int = 120) -> str | None:
    """从 payload 中提取输入预览文本。"""
    payload_dict = dict(payload or {})
    preview_source = None
    if str(source or "").upper() == "GATEWAY":
        preview_source = payload_dict.get("content")
    elif str(source or "").upper() == "ASR":
        preview_source = payload_dict.get("text")
    preview = build_reply_preview(str(preview_source or ""), limit=limit).strip()
    return preview or None


def log_input_event(
    *,
    ledger: Any,
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
    """将输入处理事件写入 ledger（非阻塞，失败静默）。"""
    if ledger is None:
        return
    try:
        payload: dict[str, Any] = {
            "source": source,
            "phase": phase,
            "latency_ms": latency_ms,
        }
        input_preview = build_input_preview(input_payload, source=source)
        if input_preview:
            payload["input_preview"] = input_preview
        person_id = str((input_payload or {}).get("person_id") or "").strip()
        if person_id:
            payload["person_id"] = person_id
        route_fields = (
            "route_type", "route_target", "route_task", "route_source",
            "route_reason", "route_confidence", "route_override_reason", "llm_route_used",
            "exec_quality_degraded", "exec_quality_reason_code",
            "exec_quality_tags", "exec_quality_skill_exec_count",
        )
        for field in route_fields:
            value = (input_payload or {}).get(field)
            if value is not None:
                payload[field] = value
        if source == "GATEWAY":
            gateway_fields = (
                "platform", "conversation_id", "msg_id", "msg_type",
                "from_id", "to_id", "sender_name", "media_url",
                "reply_to_msg_id", "is_group", "decision",
                "gateway_status", "gateway_reason_code", "gateway_detail", "gateway_latency_ms",
                "reply_text_len", "reply_preview", "reply_form", "reply_form_reason", "voice_sent",
                "immediate_ack_status", "immediate_ack_latency_ms",
                "immediate_ack_preview", "immediate_ack_reason",
                "progress_fastpath_status", "progress_fastpath_latency_ms",
                "progress_fastpath_preview", "progress_fastpath_reason",
                "progress_push_status", "progress_push_reason",
                "progress_push_preview", "progress_push_updates",
                "deviation_reason", "deviation_overlap_ratio",
                "deviation_retry_applied", "deviation_retry_overlap_ratio",
                "gateway_voice_waited", "gateway_voice_success", "gateway_voice_status",
                "gateway_voice_reason_code", "gateway_voice_detail",
                "gateway_voice_latency_ms", "gateway_voice_started",
                "gateway_voice_vts_started", "gateway_voice_vts_expression_success",
                "gateway_voice_event",
            )
            for field in gateway_fields:
                value = (input_payload or {}).get(field)
                if value is not None:
                    payload[field] = value
        ledger.log_voice_event({
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
        })
    except Exception as log_exc:
        _logger.debug("input_routes: ledger log failed (non-blocking): %s", log_exc)


def log_dispatch_task_exception(task: asyncio.Task) -> None:
    """记录 fire-and-forget 任务的静默异常。"""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _logger.error("input_routes: dispatch task failed silently: %s", exc, exc_info=exc)


def get_request_skill_exec_rows(ledger: Any, *, request_id: str) -> list[dict[str, Any]]:
    """从 ledger 中获取指定 request_id 的 skill 执行记录。"""
    if ledger is None:
        return []
    rid = str(request_id or "").strip()
    if not rid:
        return []
    try:
        if hasattr(ledger, "trace_request"):
            trace = ledger.trace_request(rid)
            rows = trace.get("skill_exec_log") if isinstance(trace, dict) else None
            if isinstance(rows, list):
                return [dict(row or {}) for row in rows if isinstance(row, dict)]
    except Exception:
        pass
    try:
        fallback_rows = getattr(ledger, "skill_exec_log", None)
        if isinstance(fallback_rows, list):
            matched: list[dict[str, Any]] = []
            for row in fallback_rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("request_id") or "").strip() == rid:
                    matched.append(dict(row))
            return matched
    except Exception:
        pass
    return []


__all__ = [
    "get_ledger_safe",
    "build_reply_preview",
    "build_input_preview",
    "log_input_event",
    "log_dispatch_task_exception",
    "get_request_skill_exec_rows",
]
