"""CapabilityForgeSettings —— Capability Forge 自动提案域配置（OPT-4 第二批）。

背景：`host_brain/app/capability_forge.py` 中 `_get_auto_plan_thresholds()` 使用 10 个
`HOST_CAPABILITY_FORGE_AUTO_*` 环境变量控制自动提案的门槛、冷却、金丝雀比例、
自动审批策略。这些配置原本散落在 `_parse_bool_env/_parse_int_env/_parse_float_env`
的逐项 `os.getenv(...)` 调用中，属于 OPTIMIZATION_PLAN_20260420 §8 OPT-4 要求
迁移的"散点环境读取"。

本模块把它们统一收敛为 pydantic-settings 子域，并保留：
- 原有的默认值（与 capability_forge.py 中 `default=` 完全对齐，防止回归）；
- 原有的取值范围（ge/le 硬约束，防止非法配置）；
- readiness_fields() 报告，让 /health/env-readiness 能看到这 10 个字段的来源。

迁移后，capability_forge.py 只需要调用 `get_capability_forge_settings()` 拿到强类型
的设置对象，不再需要运行时 `os.getenv` 解析。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import SettingsFieldReport, inspect_setting


class CapabilityForgeSettings(BaseSettings):
    """Capability Forge 自动提案域配置（10 个 HOST_CAPABILITY_FORGE_AUTO_* 环境变量）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    auto_proposal_enabled: bool = Field(
        default=True, alias="HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED"
    )
    auto_min_detected: int = Field(
        default=3, ge=1, le=100, alias="HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED"
    )
    auto_min_sessions: int = Field(
        default=2, ge=1, le=100, alias="HOST_CAPABILITY_FORGE_AUTO_MIN_SESSIONS"
    )
    auto_min_failure_rate: float = Field(
        default=0.35, ge=0.0, le=1.0, alias="HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE"
    )
    auto_min_failed: int = Field(
        default=2, ge=0, le=100, alias="HOST_CAPABILITY_FORGE_AUTO_MIN_FAILED"
    )
    auto_dedupe_cooldown_ms: int = Field(
        default=1_800_000, ge=60_000, le=86_400_000,
        alias="HOST_CAPABILITY_FORGE_AUTO_DEDUPE_COOLDOWN_MS",
    )
    auto_canary_traffic_percent: int = Field(
        default=5, ge=1, le=100,
        alias="HOST_CAPABILITY_FORGE_AUTO_CANARY_TRAFFIC_PERCENT",
    )
    auto_approve_enabled: bool = Field(
        default=False, alias="HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED"
    )
    auto_approve_stage: str = Field(
        default="shadow", alias="HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE"
    )
    auto_approver: str = Field(
        default="auto_forge", alias="HOST_CAPABILITY_FORGE_AUTO_APPROVER"
    )

    @classmethod
    def readiness_fields(cls) -> list[SettingsFieldReport]:
        s = get_capability_forge_settings()
        domain = "capability_forge"
        specs = [
            ("auto_proposal_enabled", "HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED", True, s.auto_proposal_enabled),
            ("auto_min_detected", "HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED", 3, s.auto_min_detected),
            ("auto_min_sessions", "HOST_CAPABILITY_FORGE_AUTO_MIN_SESSIONS", 2, s.auto_min_sessions),
            ("auto_min_failure_rate", "HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE", 0.35, s.auto_min_failure_rate),
            ("auto_min_failed", "HOST_CAPABILITY_FORGE_AUTO_MIN_FAILED", 2, s.auto_min_failed),
            ("auto_dedupe_cooldown_ms", "HOST_CAPABILITY_FORGE_AUTO_DEDUPE_COOLDOWN_MS", 1_800_000, s.auto_dedupe_cooldown_ms),
            ("auto_canary_traffic_percent", "HOST_CAPABILITY_FORGE_AUTO_CANARY_TRAFFIC_PERCENT", 5, s.auto_canary_traffic_percent),
            ("auto_approve_enabled", "HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED", False, s.auto_approve_enabled),
            ("auto_approve_stage", "HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE", "shadow", s.auto_approve_stage),
            ("auto_approver", "HOST_CAPABILITY_FORGE_AUTO_APPROVER", "auto_forge", s.auto_approver),
        ]
        return [
            inspect_setting(
                domain=domain,
                field_name=name,
                env_key=env_key,
                default_value=default,
                current_value=current,
                is_required=False,
            )
            for name, env_key, default, current in specs
        ]


@lru_cache(maxsize=1)
def get_capability_forge_settings() -> CapabilityForgeSettings:
    """惰性单例。测试可通过 `get_capability_forge_settings.cache_clear()` 重置。"""
    return CapabilityForgeSettings()
