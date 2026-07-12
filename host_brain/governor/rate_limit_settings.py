"""Typed cold settings for Governor rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_MEMORY = "memory"
BACKEND_SQLITE = "sqlite"
BACKEND_REDIS = "redis"
ALLOWED_BACKENDS = frozenset({BACKEND_MEMORY, BACKEND_SQLITE, BACKEND_REDIS})
DEFAULT_WINDOW_MS = 60_000
DEFAULT_MAX_REQUESTS = 30


@dataclass(frozen=True)
class RateLimitConfig:
    backend: str = BACKEND_MEMORY
    window_ms: int = DEFAULT_WINDOW_MS
    max_requests: int = DEFAULT_MAX_REQUESTS
    sqlite_db_path: str = ":memory:"
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "Natsume:ratelimit:"
    redis_timeout_ms: int = 1_000


class RateLimitSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    backend: str = Field(default=BACKEND_MEMORY, alias="HOST_RATE_LIMIT_BACKEND")
    window_ms: int = Field(
        default=DEFAULT_WINDOW_MS,
        ge=1_000,
        le=3_600_000,
        alias="HOST_RATE_LIMIT_WINDOW_MS",
    )
    max_requests: int = Field(
        default=DEFAULT_MAX_REQUESTS,
        ge=1,
        le=1_000_000,
        alias="HOST_RATE_LIMIT_MAX_REQUESTS",
    )
    sqlite_db_path: str = Field(default="", alias="HOST_RATE_LIMIT_SQLITE_DB_PATH")
    canonical_db_path: str = Field(
        default="host_brain/data/audit.sqlite3",
        alias="NATSUME_AUDIT_DB",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="HOST_RATE_LIMIT_REDIS_URL",
    )
    redis_key_prefix: str = Field(
        default="Natsume:ratelimit:",
        alias="HOST_RATE_LIMIT_REDIS_KEY_PREFIX",
    )
    redis_timeout_ms: int = Field(
        default=1_000,
        ge=50,
        le=60_000,
        alias="HOST_RATE_LIMIT_REDIS_TIMEOUT_MS",
    )

    def to_config(self) -> RateLimitConfig:
        backend = str(self.backend or BACKEND_MEMORY).strip().lower()
        if backend not in ALLOWED_BACKENDS:
            backend = BACKEND_MEMORY
        return RateLimitConfig(
            backend=backend,
            window_ms=int(self.window_ms),
            max_requests=int(self.max_requests),
            sqlite_db_path=(
                str(self.sqlite_db_path or "").strip()
                or str(self.canonical_db_path or "").strip()
                or ":memory:"
            ),
            redis_url=str(self.redis_url or "redis://localhost:6379/0").strip()
            or "redis://localhost:6379/0",
            redis_key_prefix=str(self.redis_key_prefix or "Natsume:ratelimit:").strip()
            or "Natsume:ratelimit:",
            redis_timeout_ms=int(self.redis_timeout_ms),
        )


def load_rate_limit_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> RateLimitConfig:
    if env is None and not local_overrides:
        return RateLimitSettings().to_config()
    merged = dict(env or {})
    if local_overrides:
        merged.update(dict(local_overrides))
    backend = str(merged.get("HOST_RATE_LIMIT_BACKEND") or BACKEND_MEMORY).strip().lower()
    if backend not in ALLOWED_BACKENDS:
        backend = BACKEND_MEMORY
    sqlite_path = (
        str(merged.get("HOST_RATE_LIMIT_SQLITE_DB_PATH") or "").strip()
        or str(merged.get("NATSUME_AUDIT_DB") or "").strip()
        or str(merged.get("Natsume_AUDIT_DB") or "").strip()
        or ":memory:"
    )
    return RateLimitConfig(
        backend=backend,
        window_ms=_bounded_int(
            merged.get("HOST_RATE_LIMIT_WINDOW_MS"),
            default=DEFAULT_WINDOW_MS,
            minimum=1_000,
            maximum=3_600_000,
        ),
        max_requests=_bounded_int(
            merged.get("HOST_RATE_LIMIT_MAX_REQUESTS"),
            default=DEFAULT_MAX_REQUESTS,
            minimum=1,
            maximum=1_000_000,
        ),
        sqlite_db_path=sqlite_path,
        redis_url=str(
            merged.get("HOST_RATE_LIMIT_REDIS_URL") or "redis://localhost:6379/0"
        ).strip()
        or "redis://localhost:6379/0",
        redis_key_prefix=str(
            merged.get("HOST_RATE_LIMIT_REDIS_KEY_PREFIX") or "Natsume:ratelimit:"
        ).strip()
        or "Natsume:ratelimit:",
        redis_timeout_ms=_bounded_int(
            merged.get("HOST_RATE_LIMIT_REDIS_TIMEOUT_MS"),
            default=1_000,
            minimum=50,
            maximum=60_000,
        ),
    )


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip()) if value is not None else int(default)
    except (TypeError, ValueError):
        parsed = int(default)
    return min(max(parsed, minimum), maximum)


__all__ = [
    "ALLOWED_BACKENDS",
    "BACKEND_MEMORY",
    "BACKEND_REDIS",
    "BACKEND_SQLITE",
    "DEFAULT_MAX_REQUESTS",
    "DEFAULT_WINDOW_MS",
    "RateLimitConfig",
    "RateLimitSettings",
    "load_rate_limit_config",
]
