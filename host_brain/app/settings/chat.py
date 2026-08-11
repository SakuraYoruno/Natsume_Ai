"""Typed settings owned by the HTTP chat cutover path."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatSettings(BaseSettings):
    """Environment-backed chat policy without module-global caching."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    action_arbiter_enabled: bool = Field(
        default=False,
        alias="HOST_ACTION_ARBITER_ENABLED",
    )
    sys_mode: str = Field(default="normal", alias="HOST_SYS_MODE")

    mateengine_chat_bubble_enabled: bool = Field(
        default=False,
        alias="HOST_MATEENGINE_CHAT_BUBBLE_ENABLED",
    )
    mateengine_chat_bubble_max_chars: int = Field(
        default=220,
        ge=24,
        alias="HOST_MATEENGINE_CHAT_BUBBLE_MAX_CHARS",
    )
    mateengine_state_cues_enabled: bool = Field(
        default=False,
        alias="HOST_MATEENGINE_STATE_CUES_ENABLED",
    )
    mateengine_thinking_cue_delay_ms: int = Field(
        default=900,
        ge=0,
        alias="HOST_MATEENGINE_THINKING_CUE_DELAY_MS",
    )
    mateengine_thinking_cue_text: str = Field(
        default="...",
        alias="HOST_MATEENGINE_THINKING_CUE_TEXT",
    )
    mateengine_approved_cue_text: str = Field(
        default="",
        alias="HOST_MATEENGINE_APPROVED_CUE_TEXT",
    )
    mateengine_memory_recall_cue_text: str = Field(
        default="翻一下记忆...",
        alias="HOST_MATEENGINE_MEMORY_RECALL_CUE_TEXT",
    )
    mateengine_tool_running_cue_text: str = Field(
        default="处理中...",
        alias="HOST_MATEENGINE_TOOL_RUNNING_CUE_TEXT",
    )
    mateengine_processing_cue_text: str = Field(
        default="",
        alias="HOST_MATEENGINE_PROCESSING_CUE_TEXT",
    )
    mateengine_speaking_cue_text: str = Field(
        default="",
        alias="HOST_MATEENGINE_SPEAKING_CUE_TEXT",
    )
    mateengine_interrupted_cue_text: str = Field(
        default="",
        alias="HOST_MATEENGINE_INTERRUPTED_CUE_TEXT",
    )
    mateengine_error_cue_text: str = Field(
        default="这里卡住了。",
        alias="HOST_MATEENGINE_ERROR_CUE_TEXT",
    )


def get_chat_settings() -> ChatSettings:
    """Resolve settings per use so tests and runtime env changes remain visible."""
    return ChatSettings()


__all__ = ["ChatSettings", "get_chat_settings"]
