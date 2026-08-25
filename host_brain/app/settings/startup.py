"""Typed startup, warmup, and Edge probe settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict




class StartupSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    edge_rpc_enabled: bool = Field(default=True, alias="HOST_ENABLE_EDGE_RPC")
    edge_rpc_timeout_ms: int = Field(
        default=3_000,
        ge=1_000,
        le=10_000,
        alias="HOST_EDGE_RPC_TIMEOUT_MS",
    )
    surface_quote_warmup_enabled: bool = Field(
        default=True,
        alias="HOST_SURFACE_QUOTE_RUNTIME_WARMUP_ENABLED",
    )
    codex_exec_intent_warmup_enabled: bool = Field(
        default=True,
        alias="HOST_STARTUP_CODEX_EXEC_INTENT_WARMUP_ENABLED",
    )
    codex_exec_intent_warmup_timeout_ms: int = Field(
        default=30_000,
        ge=1_000,
        le=600_000,
        alias="HOST_CODEX_EXEC_INTENT_WARMUP_TIMEOUT_MS",
    )
    retrieval_warmup_resource_gate_enabled: bool = Field(
        default=False,
        alias="HOST_STARTUP_RETRIEVAL_WARMUP_RESOURCE_GATE_ENABLED",
    )
    retrieval_warmup_embedding_enabled: bool = Field(
        default=True,
        alias="HOST_STARTUP_RETRIEVAL_WARMUP_EMBEDDING",
    )


def get_startup_settings() -> StartupSettings:
    # Startup functions are called once per process; avoiding a hidden cache
    # keeps isolated tests and explicit environment snapshots deterministic.
    return StartupSettings()


__all__ = ["StartupSettings", "get_startup_settings"]
