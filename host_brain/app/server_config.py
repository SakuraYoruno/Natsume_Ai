from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware


def resolve_uvicorn_access_log_enabled() -> bool:
    raw = str(os.environ.get("HOST_FASTAPI_APP_UVICORN_ACCESS_LOG_ENABLED") or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "yes", "on"}


def configure_http_middleware(app: FastAPI) -> None:
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
