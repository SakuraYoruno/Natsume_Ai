"""CoreSettings——最小必需配置集合（OPT-4）

覆盖：
- 环境标识（dev/test/prod）
- 数据目录（SQLite / Chroma / Neo4j 本地数据根）
- 日志级别
- Host HTTP 监听端点

这些是任何部署都必须先确定的"冷启动参数"。其他域（LLM/Tribe/Observability）
允许在启动后运行时协商，但 Core 不行。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import SettingsFieldReport, inspect_setting

LogLevel = Literal["DEBUG", "INFO", "WARN", "WARNING", "ERROR"]
RuntimeEnv = Literal["dev", "test", "prod"]


class CoreSettings(BaseSettings):
    """最小必需的运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: RuntimeEnv = Field(default="dev", alias="Natsume_ENV")
    data_dir: Path = Field(default=Path("./data"), alias="NATSUME_DATA_DIR")
    log_level: LogLevel = Field(default="INFO", alias="HOST_APP_LOG_LEVEL")
    host_app_host: str = Field(default="127.0.0.1", alias="HOST_APP_HOST")
    host_app_port: int = Field(default=18080, ge=1, le=65535, alias="HOST_APP_PORT")

    # OPT-4 第三批：运行时特性开关 / 调度参数（原散落在各 app 模块里）
    prefrontal_plan_enabled: bool = Field(
        default=False, alias="PREFRONTAL_PLAN_ENABLED"
    )
    skill_dag_parallel_window: int = Field(
        default=4, ge=1, le=16, alias="HOST_SKILL_DAG_PARALLEL_WINDOW"
    )
    sys_mode: str = Field(default="normal", alias="HOST_SYS_MODE")

    @classmethod
    def readiness_fields(cls) -> list[SettingsFieldReport]:
        """产出 readiness 报告字段（供 /health/env-readiness 使用）。"""
        s = get_core_settings()
        # domain 标识
        domain = "core"
        return [
            inspect_setting(
                domain=domain, field_name="env", env_key="Natsume_ENV",
                default_value="dev", current_value=s.env, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="data_dir", env_key="NATSUME_DATA_DIR",
                default_value="./data", current_value=str(s.data_dir), is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="log_level", env_key="HOST_APP_LOG_LEVEL",
                default_value="INFO", current_value=s.log_level, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="host_app_host", env_key="HOST_APP_HOST",
                default_value="127.0.0.1", current_value=s.host_app_host, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="host_app_port", env_key="HOST_APP_PORT",
                default_value=18080, current_value=s.host_app_port, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="prefrontal_plan_enabled",
                env_key="PREFRONTAL_PLAN_ENABLED",
                default_value=False, current_value=s.prefrontal_plan_enabled, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="skill_dag_parallel_window",
                env_key="HOST_SKILL_DAG_PARALLEL_WINDOW",
                default_value=4, current_value=s.skill_dag_parallel_window, is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="sys_mode",
                env_key="HOST_SYS_MODE",
                default_value="normal", current_value=s.sys_mode, is_required=False,
            ),
        ]


@lru_cache(maxsize=1)
def get_core_settings() -> CoreSettings:
    """惰性单例。测试可通过 `get_core_settings.cache_clear()` 重置。"""
    return CoreSettings()
