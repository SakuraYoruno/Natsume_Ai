"""Pure typed settings shared by executive and agent runtimes."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestrationSettings(BaseSettings):
    """Cold process configuration consumed by governed request paths."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    post_process_executor_workers: int = Field(
        default=4,
        ge=1,
        le=16,
        alias="HOST_POST_PROCESS_EXECUTOR_WORKERS",
    )
    subagent_fallback_model_ids: str = Field(
        default="deepseek-v4-flash",
        alias="HOST_SUBAGENT_FALLBACK_MODEL_IDS",
    )
    recent_turn_direct_answer_enabled: bool = Field(
        default=True,
        alias="HOST_RECENT_TURN_DIRECT_ANSWER_ENABLED",
    )
    top_agent_retrieval_timeout_ms: int = Field(
        default=20_000,
        ge=500,
        le=60_000,
        alias="HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS",
    )
    selection_followup_retrieval_timeout_ms: int = Field(
        default=35_000,
        ge=1_000,
        le=90_000,
        alias="HOST_SELECTION_FOLLOWUP_RETRIEVAL_TIMEOUT_MS",
    )
    stream_reflection_buffer_enabled: bool = Field(
        default=True,
        alias="HOST_STREAM_REFLECTION_BUFFER_ENABLED",
    )
    clarification_direct_enabled: bool = Field(
        default=False,
        alias="HOST_CLARIFICATION_DIRECT_ENABLED",
    )
    stream_tts_enabled: bool = Field(
        default=True,
        alias="HOST_STREAM_TTS_ENABLED",
    )
    tts_sentence_stream_enabled: bool = Field(
        default=True,
        alias="HOST_TTS_SENTENCE_STREAM_ENABLED",
    )
    executive_auto_complex_plan_min_chars: int = Field(
        default=24,
        ge=8,
        le=400,
        alias="HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_MIN_CHARS",
    )
    subagent_dispatch_timeout_ms: int = Field(
        default=90_000,
        ge=5_000,
        le=300_000,
        alias="HOST_SUBAGENT_DISPATCH_TIMEOUT_MS",
    )
    action_arbiter_enabled: bool = Field(
        default=False,
        alias="HOST_ACTION_ARBITER_ENABLED",
    )


def get_orchestration_settings() -> OrchestrationSettings:
    return OrchestrationSettings()


__all__ = ["OrchestrationSettings", "get_orchestration_settings"]
