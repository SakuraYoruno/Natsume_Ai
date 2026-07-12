"""Natsume AI 分层 Settings 包（OPT-4）

本包通过 `pydantic-settings` 将原先散落在 `host_brain/app/config.py`
和 `edge_client/config.py` 中的 500+ `os.environ.get(...)` 统一收敛。

分层原则：
- CoreSettings：最小必需集合（数据目录 / 日志级别 / 环境标识）
- LLMSettings：LLM provider、超时、重试、模型覆盖
- TribeSettings：Tribe / Edge RPC / Inner World 等行为开关
- ObservabilitySettings：obs 后端、告警阈值、心跳间隔

调用方式（惰性单例，启动时不会立即加载全部环境变量）：
    from host_brain.app.settings import get_core_settings, get_llm_settings
    core = get_core_settings()
    llm = get_llm_settings()

设计要点：
1. 不破坏现有 `host_brain/app/config.py` 对外契约——config.py 内部
   可逐步改为 `core.data_dir` 等调用，但签名保持不变。
2. 所有 Settings 都绑定到根目录 `.env`（由进程 CWD 决定），允许
   通过 `Natsume_SETTINGS_ENV_FILE` 覆盖以供测试使用。
3. 必需字段若缺失，必须在启动时（而非运行到某路径时）报错——
   由 `verify_readiness()` 在 `/health/env-readiness` 汇总。
"""

from __future__ import annotations

from .base import SettingsReadinessReport, verify_readiness
from .capability_forge import CapabilityForgeSettings, get_capability_forge_settings
from .core import CoreSettings, get_core_settings
from .llm import LLMSettings, get_llm_settings
from .input import InputSettings, get_input_settings, input_settings_from_mapping
from .internal_agent import InternalAgentSettings, get_internal_agent_settings
from .observability import ObservabilitySettings, get_observability_settings
from .orchestration import OrchestrationSettings, get_orchestration_settings
from .persistence import PersistenceSettings, get_persistence_settings
from .scheduler import SchedulerSettings, get_scheduler_settings
from .startup import StartupSettings, get_startup_settings
from .tribe import TribeSettings, get_tribe_settings

__all__ = [
    "CapabilityForgeSettings",
    "CoreSettings",
    "LLMSettings",
    "InputSettings",
    "InternalAgentSettings",
    "ObservabilitySettings",
    "OrchestrationSettings",
    "PersistenceSettings",
    "SchedulerSettings",
    "StartupSettings",
    "SettingsReadinessReport",
    "TribeSettings",
    "get_capability_forge_settings",
    "get_core_settings",
    "get_llm_settings",
    "get_input_settings",
    "get_internal_agent_settings",
    "input_settings_from_mapping",
    "get_observability_settings",
    "get_orchestration_settings",
    "get_persistence_settings",
    "get_scheduler_settings",
    "get_startup_settings",
    "get_tribe_settings",
    "verify_readiness",
]
