"""LLMSettings——LLM 调用相关的集中配置（OPT-4）

聚焦于以下几类字段（对应 .env.example 中最重要的 LLM 区块）：
- provider / timeout / max_retries
- base_url 覆盖（openai/codex/gemini/deepseek）
- 模型 ID 覆盖
- 冷启动策略（是否允许缺失 key 启动）
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import SettingsFieldReport, inspect_setting

LLMProvider = Literal["openai", "codex", "gemini", "deepseek", "minimax", "mock", "disabled"]


class LLMSettings(BaseSettings):
    """LLM 调用配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    provider: LLMProvider = Field(default="openai", alias="HOST_LLM_PROVIDER")
    timeout_ms: int = Field(default=15000, ge=100, le=600_000, alias="HOST_LLM_TIMEOUT_MS")
    max_retries: int = Field(default=1, ge=0, le=10, alias="HOST_LLM_MAX_RETRIES")
    cold_start_allowed: bool = Field(default=True, alias="HOST_COLD_START_ALLOWED")

    # base url 覆盖
    openai_base_url: str | None = Field(default=None, alias="HOST_OPENAI_BASE_URL")
    codex_base_url: str | None = Field(default=None, alias="HOST_CODEX_BASE_URL")
    gemini_base_url: str | None = Field(default=None, alias="HOST_GEMINI_BASE_URL")
    deepseek_base_url: str | None = Field(default=None, alias="HOST_DEEPSEEK_BASE_URL")
    minimax_base_url: str | None = Field(default=None, alias="HOST_MINIMAX_BASE_URL")

    # 模型 ID 覆盖
    model_id: str | None = Field(default=None, alias="HOST_LLM_MODEL_ID")
    openai_model_id: str | None = Field(default=None, alias="HOST_OPENAI_MODEL_ID")
    codex_model_id: str | None = Field(default=None, alias="HOST_CODEX_MODEL_ID")
    gemini_model_id: str | None = Field(default=None, alias="HOST_GEMINI_MODEL_ID")
    deepseek_model_id: str | None = Field(default=None, alias="HOST_DEEPSEEK_MODEL_ID")
    minimax_model_id: str | None = Field(default=None, alias="HOST_MINIMAX_MODEL_ID")

    @classmethod
    def readiness_fields(cls) -> list[SettingsFieldReport]:
        s = get_llm_settings()
        domain = "llm"
        items: list[tuple[str, str, object, object, bool]] = [
            ("provider", "HOST_LLM_PROVIDER", "openai", s.provider, False),
            ("timeout_ms", "HOST_LLM_TIMEOUT_MS", 15000, s.timeout_ms, False),
            ("max_retries", "HOST_LLM_MAX_RETRIES", 1, s.max_retries, False),
            ("cold_start_allowed", "HOST_COLD_START_ALLOWED", True, s.cold_start_allowed, False),
            ("openai_base_url", "HOST_OPENAI_BASE_URL", None, s.openai_base_url, False),
            ("codex_base_url", "HOST_CODEX_BASE_URL", None, s.codex_base_url, False),
            ("gemini_base_url", "HOST_GEMINI_BASE_URL", None, s.gemini_base_url, False),
            ("deepseek_base_url", "HOST_DEEPSEEK_BASE_URL", None, s.deepseek_base_url, False),
            ("minimax_base_url", "HOST_MINIMAX_BASE_URL", None, s.minimax_base_url, False),
            ("model_id", "HOST_LLM_MODEL_ID", None, s.model_id, False),
            ("openai_model_id", "HOST_OPENAI_MODEL_ID", None, s.openai_model_id, False),
            ("codex_model_id", "HOST_CODEX_MODEL_ID", None, s.codex_model_id, False),
            ("gemini_model_id", "HOST_GEMINI_MODEL_ID", None, s.gemini_model_id, False),
            ("deepseek_model_id", "HOST_DEEPSEEK_MODEL_ID", None, s.deepseek_model_id, False),
            ("minimax_model_id", "HOST_MINIMAX_MODEL_ID", None, s.minimax_model_id, False),
        ]
        return [
            inspect_setting(
                domain=domain, field_name=name, env_key=key,
                default_value=default, current_value=current, is_required=required,
            )
            for name, key, default, current, required in items
        ]


@lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    return LLMSettings()
