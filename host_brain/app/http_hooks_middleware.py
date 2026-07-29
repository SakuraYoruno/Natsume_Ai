"""HTTP 中间件 Hook：应用生命周期管理（lifespan）入口。

P3-2：从 http_hooks.py 拆分，负责应用启动/关闭生命周期：
- 启动探针（模块预热、Edge RPC 健康检测）
- 调度器注册（NatsumeScheduler、DistillationEngine 等）
- Tribe 基础设施初始化（TribeEventBus、TribeStateRegistry）
- 关闭清理（ActivityMonitor、legacy Bond compatibility adapter、TribeEventBus）

注意：lifespan 主体逻辑仍在 http_hooks.py 的 runtime_lifespan() 中，
本模块作为中间件层的统一入口，提供辅助函数和调度器构建逻辑。
"""

from __future__ import annotations

import os
from typing import Any, Callable

def parse_bool_env(name: str, *, default: bool) -> bool:
    """解析布尔类型环境变量。"""
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def build_personality_tick_status(*, registry: Any, task_ids: list[str]) -> str:
    """构建人格 tick 任务注册状态字符串，供启动报告使用。"""
    registered: list[str] = []
    missing: list[str] = []
    for task_id in task_ids:
        task = registry.get(task_id) if registry is not None else None
        if task is None:
            missing.append(task_id)
            continue
        enabled = bool(getattr(task, "enabled", False))
        registered.append(f"{task_id}[{'enabled' if enabled else 'disabled'}]")
    if missing:
        registered_text = ",".join(registered) if registered else "none"
        return f"DEGRADED: missing={','.join(missing)}; registered={registered_text}"
    return f"OK (registered={len(registered)}/{len(task_ids)}; tasks={','.join(registered)})"


__all__ = [
    "parse_bool_env",
    "build_personality_tick_status",
]
