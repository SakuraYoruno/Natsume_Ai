"""Typed cold settings for the optional Markdown KB fallback."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarkdownKbSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=True, alias="SEMANTIC_RECALL_MARKDOWN_KB_ENABLED")
    db_paths: str = Field(default="", alias="SEMANTIC_RECALL_MARKDOWN_KB_DBS")
    db_path: str = Field(default="", alias="SEMANTIC_RECALL_MARKDOWN_KB_DB")
    kb_ids: str = Field(default="", alias="SEMANTIC_RECALL_MARKDOWN_KB_IDS")

    def as_environment(self) -> dict[str, str]:
        return {
            "SEMANTIC_RECALL_MARKDOWN_KB_ENABLED": "true" if self.enabled else "false",
            "SEMANTIC_RECALL_MARKDOWN_KB_DBS": str(self.db_paths or ""),
            "SEMANTIC_RECALL_MARKDOWN_KB_DB": str(self.db_path or ""),
            "SEMANTIC_RECALL_MARKDOWN_KB_IDS": str(self.kb_ids or ""),
        }


__all__ = ["MarkdownKbSettings"]
