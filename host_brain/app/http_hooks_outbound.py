"""HTTP 出站 Hook：全局异常处理、错误响应格式化。

P3-2：从 http_hooks.py 拆分，负责出站响应的处理逻辑：
- 全局异常捕获与格式化
- HTTP 错误响应构建
- 启动状态降级判断
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from host_brain.app.schemas import ErrorResponse
from host_brain.core.ids import new_request_id


def startup_status_is_degraded(value: Any) -> bool:
    """判断启动状态字符串是否表示降级/失败。"""
    normalized = str(value or "").strip().upper()
    if not normalized:
        return False
    return normalized.startswith("DEGRADED") or normalized.startswith("FAIL")


async def global_exception_handler(
    *,
    request: Request,
    exc: Exception,
    logger: Any,
) -> JSONResponse:
    """全局异常处理器：捕获所有未处理异常，返回标准化错误响应。"""
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        reason_code = f"HTTP_{status_code}"
        detail = exc.detail
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        reason_code = "INTERNAL_SERVER_ERROR"
        detail = str(exc) or "Internal server error"
    logger.error(
        "HTTP request failed",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "status_code": status_code,
            "reason_code": reason_code,
            "detail": detail,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=exc.__class__.__name__,
            reason_code=reason_code,
            detail=detail,
            request_id=request_id,
            timestamp=int(time.time() * 1000),
        ).dict(),
    )


__all__ = ["global_exception_handler", "startup_status_is_degraded"]
