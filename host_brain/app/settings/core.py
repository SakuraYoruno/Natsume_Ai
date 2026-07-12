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
TaskRuntimeMode = Literal["legacy", "shadow", "canonical"]


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
    uvicorn_access_log_enabled: bool = Field(
        default=False,
        alias="HOST_UVICORN_ACCESS_LOG_ENABLED",
    )
    startup_eager_warmup: bool = Field(
        default=False,
        alias="HOST_STARTUP_EAGER_WARMUP",
    )
    resource_manager_enabled: bool = Field(
        default=False,
        alias="HOST_RESOURCE_MANAGER_ENABLED",
    )
    resource_manager_run_on_startup: bool = Field(
        default=True,
        alias="HOST_RESOURCE_MANAGER_RUN_ON_STARTUP",
    )

    # OPT-4 第三批：运行时特性开关 / 调度参数（原散落在各 app 模块里）
    prefrontal_plan_enabled: bool = Field(
        default=False, alias="PREFRONTAL_PLAN_ENABLED"
    )
    skill_dag_parallel_window: int = Field(
        default=4, ge=1, le=16, alias="HOST_SKILL_DAG_PARALLEL_WINDOW"
    )
    canonical_planner_adapter_enabled: bool = Field(
        default=False, alias="HOST_CANONICAL_PLANNER_ADAPTER_ENABLED"
    )
    task_runtime_mode: TaskRuntimeMode = Field(
        default="legacy", alias="HOST_TASK_RUNTIME_MODE"
    )
    task_runtime_shadow_owner: str = Field(
        default="", alias="HOST_TASK_RUNTIME_SHADOW_OWNER"
    )
    task_runtime_canary_percent: int = Field(
        default=0, ge=0, le=100, alias="HOST_TASK_RUNTIME_CANARY_PERCENT"
    )
    task_runtime_kill_switch: bool = Field(
        default=False, alias="HOST_TASK_RUNTIME_KILL_SWITCH"
    )
    task_runtime_canonical_owner: str = Field(
        default="", alias="HOST_TASK_RUNTIME_CANONICAL_OWNER"
    )
    runtime_event_replay_enabled: bool = Field(
        default=False, alias="HOST_RUNTIME_EVENT_REPLAY_ENABLED"
    )
    nle_operator_views_enabled: bool = Field(
        default=False, alias="HOST_NLE_OPERATOR_VIEWS_ENABLED"
    )
    nle_life_views_enabled: bool = Field(
        default=False, alias="HOST_NLE_LIFE_VIEWS_ENABLED"
    )
    nle_governance_views_enabled: bool = Field(
        default=False, alias="HOST_NLE_GOVERNANCE_VIEWS_ENABLED"
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
                domain=domain, field_name="canonical_planner_adapter_enabled",
                env_key="HOST_CANONICAL_PLANNER_ADAPTER_ENABLED",
                default_value=False,
                current_value=s.canonical_planner_adapter_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="task_runtime_mode",
                env_key="HOST_TASK_RUNTIME_MODE",
                default_value="legacy", current_value=s.task_runtime_mode,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="task_runtime_shadow_owner",
                env_key="HOST_TASK_RUNTIME_SHADOW_OWNER",
                default_value="", current_value=s.task_runtime_shadow_owner,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="task_runtime_canary_percent",
                env_key="HOST_TASK_RUNTIME_CANARY_PERCENT",
                default_value=0,
                current_value=s.task_runtime_canary_percent,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="task_runtime_kill_switch",
                env_key="HOST_TASK_RUNTIME_KILL_SWITCH",
                default_value=False,
                current_value=s.task_runtime_kill_switch,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="task_runtime_canonical_owner",
                env_key="HOST_TASK_RUNTIME_CANONICAL_OWNER",
                default_value="",
                current_value=s.task_runtime_canonical_owner,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="runtime_event_replay_enabled",
                env_key="HOST_RUNTIME_EVENT_REPLAY_ENABLED",
                default_value=False,
                current_value=s.runtime_event_replay_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="nle_operator_views_enabled",
                env_key="HOST_NLE_OPERATOR_VIEWS_ENABLED",
                default_value=False,
                current_value=s.nle_operator_views_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="nle_life_views_enabled",
                env_key="HOST_NLE_LIFE_VIEWS_ENABLED",
                default_value=False,
                current_value=s.nle_life_views_enabled,
                is_required=False,
            ),
            inspect_setting(
                domain=domain, field_name="nle_governance_views_enabled",
                env_key="HOST_NLE_GOVERNANCE_VIEWS_ENABLED",
                default_value=False,
                current_value=s.nle_governance_views_enabled,
                is_required=False,
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
