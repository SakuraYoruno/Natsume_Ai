"""ObservabilitySettings——可观测性/指标/告警相关配置（OPT-4）

聚焦于 `host_brain/observability/` 下常被硬编码 `os.environ.get` 的字段：
- 心跳/健康检查间隔
- 指标后端开关
- 告警阈值
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import SettingsFieldReport, inspect_setting


class ObservabilitySettings(BaseSettings):
    """可观测性配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 监控代理
    monitor_proxy_enabled: bool = Field(default=True, alias="HOST_MONITOR_PROXY_ENABLED")
    monitor_proxy_host: str = Field(default="127.0.0.1", alias="HOST_MONITOR_PROXY_HOST")
    monitor_proxy_port: int = Field(default=18180, ge=1, le=65535, alias="HOST_MONITOR_PROXY_PORT")

    # 心跳 / 健康检查
    service_health_heartbeat_interval_s: int = Field(
        default=30, ge=1, le=3600, alias="HOST_SERVICE_HEALTH_HEARTBEAT_INTERVAL_S"
    )

    # SoftFailure 阈值（供 OPT-3 告警使用）
    soft_fail_alert_ratio_threshold: float = Field(
        default=0.05, ge=0.0, le=1.0, alias="HOST_SOFT_FAIL_ALERT_RATIO_THRESHOLD"
    )
    soft_fail_alert_min_samples: int = Field(
        default=20, ge=1, alias="HOST_SOFT_FAIL_ALERT_MIN_SAMPLES"
    )

    # Query cache settings.  Projection refresh is lifecycle-owned and has
    # no per-route warm-cache feature flags.
    # 会话列表缓存 TTL（毫秒）——replaces os.getenv("OBS_SESSIONS_CACHE_TTL_MS", "3000")
    obs_sessions_cache_ttl_ms: int = Field(
        default=3000, ge=0, alias="OBS_SESSIONS_CACHE_TTL_MS"
    )

    @classmethod
    def readiness_fields(cls) -> list[SettingsFieldReport]:
        s = get_observability_settings()
        domain = "observability"
        items: list[tuple[str, str, object, object, bool]] = [
            ("monitor_proxy_enabled", "HOST_MONITOR_PROXY_ENABLED", True, s.monitor_proxy_enabled, False),
            ("monitor_proxy_host", "HOST_MONITOR_PROXY_HOST", "127.0.0.1", s.monitor_proxy_host, False),
            ("monitor_proxy_port", "HOST_MONITOR_PROXY_PORT", 18180, s.monitor_proxy_port, False),
            ("service_health_heartbeat_interval_s",
             "HOST_SERVICE_HEALTH_HEARTBEAT_INTERVAL_S", 30,
             s.service_health_heartbeat_interval_s, False),
            ("soft_fail_alert_ratio_threshold",
             "HOST_SOFT_FAIL_ALERT_RATIO_THRESHOLD", 0.05,
             s.soft_fail_alert_ratio_threshold, False),
            ("soft_fail_alert_min_samples",
             "HOST_SOFT_FAIL_ALERT_MIN_SAMPLES", 20,
             s.soft_fail_alert_min_samples, False),
            # OPT-4 首批迁移字段
            ("obs_sessions_cache_ttl_ms",
             "OBS_SESSIONS_CACHE_TTL_MS", 3000,
             s.obs_sessions_cache_ttl_ms, False),
        ]
        return [
            inspect_setting(
                domain=domain, field_name=name, env_key=key,
                default_value=default, current_value=current, is_required=required,
            )
            for name, key, default, current, required in items
        ]


@lru_cache(maxsize=1)
def get_observability_settings() -> ObservabilitySettings:
    return ObservabilitySettings()
