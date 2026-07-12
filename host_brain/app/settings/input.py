"""Typed process settings for the Host input and Gateway delivery surface."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InputSettings(BaseSettings):
    """Cold configuration consumed by ``input_routes`` without runtime env reads."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    asr_min_text_len: int = Field(default=2, ge=1, le=256, alias="HOST_INPUT_ASR_MIN_TEXT_LEN")
    asr_require_speech_chars: bool = Field(
        default=True,
        alias="HOST_INPUT_ASR_REQUIRE_SPEECH_CHARS",
    )
    asr_min_confidence: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        alias="HOST_INPUT_ASR_MIN_CONFIDENCE",
    )
    asr_require_confidence: bool = Field(
        default=False,
        alias="HOST_INPUT_ASR_REQUIRE_CONFIDENCE",
    )
    gateway_multimodal_recent_ttl_s: float = Field(
        default=600.0,
        ge=60.0,
        le=86_400.0,
        alias="HOST_GATEWAY_MULTIMODAL_RECENT_TTL_S",
    )
    gateway_route_ack_voice_enabled: bool = Field(
        default=True,
        alias="HOST_GATEWAY_ROUTE_ACK_VOICE_ENABLED",
    )
    gateway_auto_reply: bool = Field(default=True, alias="HOST_INPUT_GATEWAY_AUTO_REPLY")
    gateway_voice_selection_mode: str = Field(
        default="",
        alias="HOST_GATEWAY_VOICE_SELECTION_MODE",
    )
    gateway_force_dual_voice_reply: bool = Field(
        default=False,
        alias="HOST_GATEWAY_FORCE_DUAL_VOICE_REPLY",
    )
    gateway_voice_reply_wait_result: bool = Field(
        default=False,
        alias="HOST_GATEWAY_VOICE_REPLY_WAIT_RESULT",
    )
    gateway_voice_reply_on_primary_failure: bool | None = Field(
        default=None,
        alias="HOST_GATEWAY_VOICE_REPLY_ON_PRIMARY_FAILURE",
    )
    gateway_send_voice_message: bool = Field(
        default=False,
        alias="HOST_GATEWAY_SEND_VOICE_MESSAGE",
    )
    gateway_default_voice_emotion_tag: str = Field(
        default="",
        alias="HOST_GATEWAY_DEFAULT_VOICE_EMOTION_TAG",
    )
    gateway_progress_fastpath_enabled: bool = Field(
        default=True,
        alias="HOST_GATEWAY_PROGRESS_FASTPATH_ENABLED",
    )
    gateway_progress_fastpath_wait_result: bool = Field(
        default=True,
        alias="HOST_GATEWAY_PROGRESS_FASTPATH_WAIT_RESULT",
    )
    gateway_progress_fastpath_timeout_ms: int = Field(
        default=6_000,
        ge=1_000,
        le=120_000,
        alias="HOST_GATEWAY_PROGRESS_FASTPATH_TIMEOUT_MS",
    )
    gateway_progress_push_warmup_ms: int = Field(
        default=15_000,
        ge=0,
        le=300_000,
        alias="HOST_GATEWAY_PROGRESS_PUSH_WARMUP_MS",
    )
    gateway_progress_push_interval_ms: int = Field(
        default=15_000,
        ge=2_000,
        le=300_000,
        alias="HOST_GATEWAY_PROGRESS_PUSH_INTERVAL_MS",
    )
    gateway_progress_push_max_updates: int = Field(
        default=2,
        ge=1,
        le=8,
        alias="HOST_GATEWAY_PROGRESS_PUSH_MAX_UPDATES",
    )
    gateway_progress_push_wait_result: bool = Field(
        default=False,
        alias="HOST_GATEWAY_PROGRESS_PUSH_WAIT_RESULT",
    )
    gateway_progress_push_timeout_ms: int = Field(
        default=4_000,
        ge=1_000,
        le=120_000,
        alias="HOST_GATEWAY_PROGRESS_PUSH_TIMEOUT_MS",
    )
    gateway_deviation_retry_enabled: bool = Field(
        default=True,
        alias="HOST_GATEWAY_DEVIATION_RETRY_ENABLED",
    )
    gateway_deviation_min_overlap: float = Field(
        default=0.18,
        ge=0.0,
        le=1.0,
        alias="HOST_GATEWAY_DEVIATION_MIN_OVERLAP",
    )
    gateway_media_reply_wait_result: bool = Field(
        default=True,
        alias="HOST_INPUT_GATEWAY_MEDIA_REPLY_WAIT_RESULT",
    )
    gateway_reply_wait_result: bool = Field(
        default=False,
        alias="HOST_INPUT_GATEWAY_REPLY_WAIT_RESULT",
    )
    gateway_text_reply_async: bool = Field(
        default=True,
        alias="HOST_INPUT_GATEWAY_TEXT_REPLY_ASYNC",
    )
    gateway_reply_timeout_ms: int = Field(
        default=12_000,
        ge=1_000,
        le=300_000,
        alias="HOST_INPUT_GATEWAY_REPLY_TIMEOUT_MS",
    )
    gateway_voice_reply_timeout_ms: int = Field(
        default=30_000,
        ge=1_000,
        le=300_000,
        alias="HOST_GATEWAY_VOICE_REPLY_TIMEOUT_MS",
    )
    gateway_reply_failure_degrade_input: bool = Field(
        default=True,
        alias="HOST_GATEWAY_REPLY_FAILURE_DEGRADE_INPUT",
    )
    execution_quality_gate_enabled: bool = Field(
        default=True,
        alias="HOST_INPUT_EXEC_QUALITY_GATE_ENABLED",
    )
    audit_db_path: str = Field(
        default="host_brain/data/audit.sqlite3",
        alias="NATSUME_AUDIT_DB",
    )
    secure_ingress_mode: str = Field(
        default="OFF",
        alias="HOST_SECURE_INGRESS_MODE",
    )
    secure_ingress_hmac_secrets_json: str = Field(
        default="",
        alias="HOST_SECURE_INGRESS_HMAC_SECRETS_JSON",
    )
    edge_gateway_hmac_key_id: str = Field(
        default="edge.gateway.hmac.v1",
        alias="HOST_EDGE_GATEWAY_HMAC_KEY_ID",
    )
    edge_gateway_hmac_secret: str = Field(
        default="",
        alias="HOST_EDGE_GATEWAY_HMAC_SECRET",
    )
    gateway_intent_skill_manifest_max: int = Field(
        default=64,
        ge=8,
        le=120,
        alias="HOST_GATEWAY_INTENT_SKILL_MANIFEST_MAX",
    )
    gateway_intent_skill_max_risk_level: int = Field(
        default=5,
        ge=1,
        le=5,
        alias="HOST_GATEWAY_INTENT_SKILL_MAX_RISK_LEVEL",
    )


@lru_cache(maxsize=1)
def get_input_settings() -> InputSettings:
    return InputSettings()


def input_settings_from_mapping(values: Mapping[str, str]) -> InputSettings:
    """Validate an explicit test/config mapping without reading process globals."""

    normalized = {
        str(key).strip().upper(): value
        for key, value in dict(values or {}).items()
    }
    return InputSettings.model_validate(normalized)


__all__ = [
    "InputSettings",
    "get_input_settings",
    "input_settings_from_mapping",
]
