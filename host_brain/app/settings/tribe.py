"""TribeSettings——Tribe / Edge RPC / 群落协作相关配置（OPT-4）

聚焦于 `.env.example` 中的 Edge RPC、Tribe、Inner World、群落协作类字段。
这类字段大多有默认值，可选设置；我们只收敛"行为开关 + 超时/端点"这类
高频被调用方硬编码 `os.environ.get` 的字段。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import SettingsFieldReport, inspect_setting


class TribeSettings(BaseSettings):
    """Tribe / Edge RPC / 群落协作相关配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Edge RPC
    edge_rpc_base_url: str | None = Field(default=None, alias="HOST_EDGE_RPC_BASE_URL")
    edge_rpc_shared_key: str | None = Field(default=None, alias="HOST_EDGE_RPC_SHARED_KEY")
    edge_rpc_timeout_ms: int = Field(default=3000, ge=100, le=120_000, alias="HOST_EDGE_RPC_TIMEOUT_MS")
    edge_rpc_retries: int = Field(default=1, ge=0, le=10, alias="HOST_EDGE_RPC_RETRIES")

    # 内部鉴权
    internal_tribe_token: str | None = Field(default=None, alias="HOST_INTERNAL_TRIBE_TOKEN")

    # 行为开关
    enable_retrieval: bool = Field(default=True, alias="HOST_ENABLE_RETRIEVAL")
    enable_vision: bool = Field(default=True, alias="HOST_ENABLE_VISION")
    enable_edge_rpc: bool = Field(default=True, alias="HOST_ENABLE_EDGE_RPC")

    # 系统模式 / 空闲阈值
    sys_mode: str = Field(default="normal", alias="HOST_SYS_MODE")
    idle_threshold_ms: int = Field(default=7_200_000, ge=60_000, alias="HOST_IDLE_THRESHOLD_MS")

    @classmethod
    def readiness_fields(cls) -> list[SettingsFieldReport]:
        s = get_tribe_settings()
        domain = "tribe"
        items: list[tuple[str, str, object, object, bool]] = [
            ("edge_rpc_base_url", "HOST_EDGE_RPC_BASE_URL", None, s.edge_rpc_base_url, False),
            ("edge_rpc_shared_key", "HOST_EDGE_RPC_SHARED_KEY", None, s.edge_rpc_shared_key, False),
            ("edge_rpc_timeout_ms", "HOST_EDGE_RPC_TIMEOUT_MS", 3000, s.edge_rpc_timeout_ms, False),
            ("edge_rpc_retries", "HOST_EDGE_RPC_RETRIES", 1, s.edge_rpc_retries, False),
            ("internal_tribe_token", "HOST_INTERNAL_TRIBE_TOKEN", None, s.internal_tribe_token, False),
            ("enable_retrieval", "HOST_ENABLE_RETRIEVAL", True, s.enable_retrieval, False),
            ("enable_vision", "HOST_ENABLE_VISION", True, s.enable_vision, False),
            ("enable_edge_rpc", "HOST_ENABLE_EDGE_RPC", True, s.enable_edge_rpc, False),
            ("sys_mode", "HOST_SYS_MODE", "normal", s.sys_mode, False),
            ("idle_threshold_ms", "HOST_IDLE_THRESHOLD_MS", 7_200_000, s.idle_threshold_ms, False),
        ]
        return [
            inspect_setting(
                domain=domain, field_name=name, env_key=key,
                default_value=default, current_value=current, is_required=required,
            )
            for name, key, default, current, required in items
        ]


@lru_cache(maxsize=1)
def get_tribe_settings() -> TribeSettings:
    return TribeSettings()
