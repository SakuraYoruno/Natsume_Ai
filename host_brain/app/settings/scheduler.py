"""Typed cold settings for the governed scheduler composition."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SchedulerSettings(BaseSettings):
    """Process-owned scheduler clock and service identity configuration.

    The clock is disabled by default because it may only start with an
    explicitly provisioned service credential.  HTTP scheduler administration
    remains available through Secure Ingress when the clock is disabled.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    clock_enabled: bool = Field(
        default=False,
        alias="HOST_SCHEDULER_CLOCK_ENABLED",
    )
    background_tasks_enabled: bool = Field(
        default=True,
        alias="HOST_BACKGROUND_TASKS_ENABLED",
    )
    seed_builtin_definitions: bool = Field(
        default=True,
        alias="HOST_SCHEDULER_SEED_BUILTINS",
    )
    distillation_enabled: bool = Field(
        default=True,
        alias="HOST_SCHEDULER_DISTILLATION_ENABLED",
    )
    distillation_interval_seconds: int = Field(
        default=600,
        ge=60,
        le=86_400,
        alias="HOST_SCHEDULER_DISTILLATION_INTERVAL_SECONDS",
    )
    agent_immune_enabled: bool = Field(
        default=False,
        alias="HOST_CODEX_IMMUNE_SCHEDULER_ENABLED",
    )
    agent_immune_interval_seconds: int = Field(
        default=300,
        ge=15,
        le=86_400,
        alias="HOST_CODEX_IMMUNE_SCHEDULER_INTERVAL_SECONDS",
    )
    clock_poll_interval_seconds: float = Field(
        default=1.0,
        ge=0.05,
        le=300.0,
        alias="HOST_SCHEDULER_CLOCK_POLL_INTERVAL_SECONDS",
    )
    clock_batch_limit: int = Field(
        default=64,
        ge=1,
        le=1_000,
        alias="HOST_SCHEDULER_CLOCK_BATCH_LIMIT",
    )
    clock_principal_id: str = Field(
        default="",
        max_length=255,
        alias="HOST_SCHEDULER_CLOCK_PRINCIPAL_ID",
    )
    clock_tenant_id: str = Field(
        default="",
        max_length=255,
        alias="HOST_SCHEDULER_CLOCK_TENANT_ID",
    )
    clock_key_id: str = Field(
        default="",
        max_length=255,
        alias="HOST_SCHEDULER_CLOCK_KEY_ID",
    )
    clock_hmac_secret: SecretStr = Field(
        default=SecretStr(""),
        alias="HOST_SCHEDULER_CLOCK_HMAC_SECRET",
    )
    clock_credential_ttl_seconds: int = Field(
        default=300,
        ge=30,
        le=86_400,
        alias="HOST_SCHEDULER_CLOCK_CREDENTIAL_TTL_SECONDS",
    )


@lru_cache(maxsize=1)
def get_scheduler_settings() -> SchedulerSettings:
    return SchedulerSettings()


__all__ = ["SchedulerSettings", "get_scheduler_settings"]
