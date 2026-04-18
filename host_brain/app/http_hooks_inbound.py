"""HTTP 入站 Hook：请求 ID 注入、审计日志中间件。

P3-2：从 http_hooks.py 拆分，负责入站请求的处理逻辑：
- 请求 ID 生成与注入
- 请求审计日志（开始/完成/异常）
- 慢请求检测
"""

from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import Request, Response

from host_brain.core.ids import new_request_id

_AUDIT_SILENT_PATH_PREFIXES: tuple[str, ...] = (
    "/monitor/",
    "/obs/",
    "/api/ui/",
    "/internal/tribe_event",
    "/docs",
    "/openapi",
    "/favicon",
    "/health",
    "/ready",
    "/metrics",
)
_AUDIT_SLOW_REQUEST_MS = 1200


def should_emit_request_info(path: str) -> bool:
    """判断是否需要输出请求信息日志（过滤监控/文档等静默路径）。"""
    normalized = str(path or "").strip().lower()
    return not any(normalized.startswith(prefix) for prefix in _AUDIT_SILENT_PATH_PREFIXES)


async def add_request_id_and_audit(
    *,
    request: Request,
    call_next: Callable,
    metrics: Any,
    logger: Any,
) -> Response:
    """入站中间件：注入请求 ID，记录请求审计日志，统计请求指标。"""
    request_id = new_request_id()
    request.state.request_id = request_id
    start_time = time.time()
    request.state.request_started_ms = int(start_time * 1000)
    path = request.url.path
    method = request.method
    emit_info = should_emit_request_info(path)
    if emit_info:
        logger.info(
            "HTTP request started",
            extra={
                "request_id": request_id,
                "path": path,
                "method": method,
                "client": request.client.host if request.client else "unknown",
            },
        )
    try:
        metrics.inc_request()
        response = await call_next(request)
        duration_ms = int((time.time() - start_time) * 1000)
        completed_extra = {
            "request_id": request_id,
            "path": path,
            "method": method,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
        if emit_info and (
            duration_ms >= _AUDIT_SLOW_REQUEST_MS
            or method.upper() != "GET"
            or int(response.status_code) >= 400
        ):
            logger.info("HTTP request completed", extra=completed_extra)
        return response
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "HTTP request failed with exception",
            extra={
                "request_id": request_id,
                "path": path,
                "method": method,
                "duration_ms": duration_ms,
                "exception": str(exc),
            },
        )
        raise


__all__ = ["add_request_id_and_audit", "should_emit_request_info"]
