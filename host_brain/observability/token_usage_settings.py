"""Typed settings for the token-usage query projection."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TokenUsageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    audit_db_path: str = Field(
        default="host_brain/data/audit.sqlite3",
        alias="NATSUME_AUDIT_DB",
    )


__all__ = ["TokenUsageSettings"]
