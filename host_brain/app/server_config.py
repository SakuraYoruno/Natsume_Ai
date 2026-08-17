"""Host HTTP 框架中间件与访问日志的部署级配置。"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware


def resolve_uvicorn_access_log_enabled() -> bool:
    """读取显式访问日志开关；未配置时默认关闭，避免高频请求日志成为意外数据面。"""
    raw = str(os.environ.get("HOST_FASTAPI_APP_UVICORN_ACCESS_LOG_ENABLED") or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "yes", "on"}


def configure_http_middleware(app: FastAPI) -> None:
    """为应用安装 CORS 与压缩中间件。

    默认 CORS 仅含本机开发地址；部署扩展来源必须显式配置。CORS 只约束浏览器行为，
    不构成身份认证或授权，敏感路由仍须在其自身 ingress/依赖中失败关闭。
    """
    cors_origins_raw = os.environ.get(
        "Natsume_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).strip()
    if cors_origins_raw:
        cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


__all__ = ["configure_http_middleware", "resolve_uvicorn_access_log_enabled"]
