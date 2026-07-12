"""Typed cold settings for the optional Codex Spark reply judge."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CodexSparkReplyJudgeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=False, alias="HOST_CODEX_SPARK_REPLY_JUDGE_ENABLED")
    rewrite_enabled: bool = Field(
        default=True,
        alias="HOST_CODEX_SPARK_REPLY_JUDGE_REWRITE_ENABLED",
    )
    mode: str = Field(default="suspicious", alias="HOST_CODEX_SPARK_REPLY_JUDGE_MODE")
    model_id: str = Field(
        default="gpt-5.3-codex-spark",
        alias="HOST_CODEX_SPARK_REPLY_JUDGE_MODEL_ID",
    )
    timeout_ms: int = Field(
        default=12_000,
        ge=1_000,
        le=60_000,
        alias="HOST_CODEX_SPARK_REPLY_JUDGE_TIMEOUT_MS",
    )
    host_audit_db_path: str = Field(default="", alias="HOST_AUDIT_DB_PATH")
    legacy_audit_db_path: str = Field(default="", alias="AUDIT_DB_PATH")
    canonical_db_path: str = Field(
        default="host_brain/data/audit.sqlite3",
        alias="NATSUME_AUDIT_DB",
    )

    @property
    def resolved_audit_db_path(self) -> str:
        return (
            str(self.host_audit_db_path or "").strip()
            or str(self.legacy_audit_db_path or "").strip()
            or str(self.canonical_db_path or "").strip()
            or "host_brain/data/audit.sqlite3"
        )


__all__ = ["CodexSparkReplyJudgeSettings"]
