"""Typed cold settings for authenticated internal Agent routes."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InternalAgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    runtime_event_db_path: str = Field(
        default="host_brain/data/audit.sqlite3",
        alias="HOST_RUNTIME_EVENT_DB_PATH",
    )


@lru_cache(maxsize=1)
def get_internal_agent_settings() -> InternalAgentSettings:
    return InternalAgentSettings()


__all__ = ["InternalAgentSettings", "get_internal_agent_settings"]
