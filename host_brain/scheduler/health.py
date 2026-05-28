"""调度器健康报告——聚合所有调度任务的运行状态与健康快照。"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from host_brain.core.ids import now_ms

if TYPE_CHECKING:
    from host_brain.scheduler.task_registry import SchedulerTaskRegistry

logger = logging.getLogger(__name__)

# 任务运行状态
STATUS_OK = "OK"
STATUS_QUEUED = "QUEUED"
STATUS_RETRYING = "RETRYING"
STATUS_DEFERRED = "DEFERRED"
STATUS_SUSPENDED = "SUSPENDED"
STATUS_DISABLED = "DISABLED"
STATUS_ERROR = "ERROR"
STATUS_PENDING = "PENDING"


class SchedulerHealthReporter:
    """聚合调度任务的运行状态快照。"""

    def __init__(self, registry: SchedulerTaskRegistry) -> None:
        self._registry = registry
        # task_id -> 运行时状态
        self._task_states: dict[str, dict[str, Any]] = {}

    def update_task_state(
        self,
        task_id: str,
        *,
        status: str = STATUS_OK,
        last_run_ts: int | None = None,
        next_run_ts: int | None = None,
        error_count: int = 0,
        retry_count: int = 0,
        suspended_reason: str | None = None,
        last_reason_code: str | None = None,
        last_retry_reason_code: str | None = None,
        last_gate_state: str | None = None,
        last_mood_state: str | None = None,
        last_endocrine_cortisol: float | None = None,
        last_endocrine_energy: float | None = None,
        last_immune_decision: str | None = None,
        last_immune_risk_level: str | None = None,
        last_immune_reason_codes: list[str] | None = None,
    ) -> None:
        """更新单个任务的运行状态。"""
        self._task_states[task_id] = {
            "task_id": task_id,
            "status": status,
            "last_run_ts": last_run_ts,
            "next_run_ts": next_run_ts,
            "error_count": error_count,
            "retry_count": retry_count,
            "suspended_reason": suspended_reason,
            "last_reason_code": last_reason_code,
            "last_retry_reason_code": last_retry_reason_code,
            "last_gate_state": last_gate_state,
            "last_mood_state": last_mood_state,
            "last_endocrine_cortisol": last_endocrine_cortisol,
            "last_endocrine_energy": last_endocrine_energy,
            "last_immune_decision": last_immune_decision,
            "last_immune_risk_level": last_immune_risk_level,
            "last_immune_reason_codes": list(last_immune_reason_codes or []),
            "updated_ts": now_ms(),
        }

    def get_task_state(self, task_id: str) -> dict[str, Any]:
        """获取单个任务的运行状态（供 list_health 使用）。"""
        return dict(self._task_states.get(task_id, {}))

    def build_health_snapshot(self) -> dict[str, Any]:
        """构建调度器整体健康快照。"""
        tasks_snapshot: list[dict[str, Any]] = []
        total = 0
        enabled = 0
        suspended = 0
        error = 0

        for task_config in self._registry.all_tasks():
            total += 1
            state = self._task_states.get(task_config.task_id, {})
            task_status = state.get("status", STATUS_PENDING if task_config.enabled else STATUS_DISABLED)

            if not task_config.enabled:
                task_status = STATUS_DISABLED
            if task_status == STATUS_SUSPENDED:
                suspended += 1
            elif task_status == STATUS_ERROR:
                error += 1

            if task_config.enabled:
                enabled += 1

            tasks_snapshot.append({
                "task_id": task_config.task_id,
                "trigger_mode": task_config.trigger_mode.value,
                "priority": task_config.priority.value,
                "enabled": task_config.enabled,
                "status": task_status,
                "last_run_ts": state.get("last_run_ts"),
                "next_run_ts": state.get("next_run_ts"),
                "error_count": state.get("error_count", 0),
                "retry_count": state.get("retry_count", 0),
                "suspended_reason": state.get("suspended_reason"),
                "last_reason_code": state.get("last_reason_code"),
                "last_retry_reason_code": state.get("last_retry_reason_code"),
                "last_gate_state": state.get("last_gate_state"),
                "last_mood_state": state.get("last_mood_state"),
                "last_endocrine_cortisol": state.get("last_endocrine_cortisol"),
                "last_endocrine_energy": state.get("last_endocrine_energy"),
                "last_immune_decision": state.get("last_immune_decision"),
                "last_immune_risk_level": state.get("last_immune_risk_level"),
                "last_immune_reason_codes": list(state.get("last_immune_reason_codes") or []),
            })

        return {
            "scheduler_status": "HEALTHY" if error == 0 and suspended == 0 else "DEGRADED",
            "total_tasks": total,
            "enabled_tasks": enabled,
            "suspended_tasks": suspended,
            "error_tasks": error,
            "tasks": tasks_snapshot,
            "ts_ms": now_ms(),
        }
