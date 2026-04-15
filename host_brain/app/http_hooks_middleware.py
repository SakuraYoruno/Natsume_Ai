"""HTTP 中间件 Hook：应用生命周期管理（lifespan）入口。

P3-2：从 http_hooks.py 拆分，负责应用启动/关闭生命周期：
- 启动探针（模块预热、Edge RPC 健康检测）
- 调度器注册（NatsumeScheduler、DistillationEngine 等）
- Tribe 基础设施初始化（TribeEventBus、TribeStateRegistry）
- 关闭清理（ActivityMonitor、BondUpdateQueue、TribeEventBus）

注意：lifespan 主体逻辑仍在 http_hooks.py 的 runtime_lifespan() 中，
本模块作为中间件层的统一入口，提供辅助函数和调度器构建逻辑。
"""

from __future__ import annotations

import os
from typing import Any, Callable

from host_brain.app.http_hooks_runtime_helpers import run_proactive_intent_dispatch


def parse_bool_env(name: str, *, default: bool) -> bool:
    """解析布尔类型环境变量。"""
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def build_scheduler_emotion_state() -> dict[str, Any]:
    """构建调度器门控使用的情绪/内分泌快照。

    P3-2：从 http_hooks.py 提取，供 NatsumeScheduler 门控读取。
    Phase T-06：优先通过 EndocrineAgent.resolve_vital_signs() 获取门控状态。
    """
    mood_state = "steady"
    emotion_tag = "NEUTRAL"
    emotion_intensity = 0.0
    endocrine_state: dict[str, Any] = {}
    try:
        from host_brain.app.dependencies import get_hard_state  # noqa: PLC0415
        from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415

        hard_state = get_hard_state()
        mood_state = str(getattr(hard_state, "mood_state", "steady") or "steady").strip().lower() or "steady"
        emotion_tag = str(getattr(hard_state, "emotion_tag", "NEUTRAL") or "NEUTRAL").strip().upper() or "NEUTRAL"
        emotion_intensity = float(getattr(hard_state, "emotion_intensity", 0.0) or 0.0)

        endocrine_agent = get_endocrine_agent()
        raw_endocrine = endocrine_agent.resolve_endocrine_state(
            hard_state=hard_state,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )
        vital_signs = endocrine_agent.resolve_vital_signs(endocrine_state=raw_endocrine)
        endocrine_state = dict(raw_endocrine)
        endocrine_state["gate_state"] = vital_signs.get("gate_state", "open")
        endocrine_state["stress_index"] = vital_signs.get("stress_index", 0.0)
        endocrine_state["energy_reserve"] = vital_signs.get("energy_reserve", 0.6)
        endocrine_state["homeostasis_ok"] = vital_signs.get("homeostasis_ok", True)
        endocrine_state["source"] = "endocrine_agent"
    except Exception:
        endocrine_state = {
            "dopamine_level": 0.5,
            "serotonin_level": 0.5,
            "cortisol_level": 0.25,
            "energy_reserve": 0.6,
            "gate_state": "open",
            "stress_index": 0.0,
            "homeostasis_ok": True,
            "source": "scheduler_fallback",
        }
    return {
        "mood_state": mood_state,
        "emotion_tag": emotion_tag,
        "emotion_intensity": emotion_intensity,
        "endocrine_state": endocrine_state,
    }


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
    "build_scheduler_emotion_state",
    "build_personality_tick_status",
]
