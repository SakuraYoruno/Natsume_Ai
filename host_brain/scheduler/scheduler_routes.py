"""Scheduler REST API 路由——定时调度器任务管理与健康端点。

DevSpec §21.6：
  POST   /scheduler/tasks              注册新任务
  GET    /scheduler/tasks              列出所有任务（含健康状态）
  GET    /scheduler/tasks/{task_id}    获取单个任务详情
  PATCH  /scheduler/tasks/{task_id}    更新任务配置
  DELETE /scheduler/tasks/{task_id}    注销任务
  POST   /scheduler/tasks/{task_id}/enable   启用任务
  POST   /scheduler/tasks/{task_id}/disable  禁用任务
  GET    /scheduler/health             所有任务健康快照
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    SchedulerTaskRegistry,
    TriggerMode,
    TaskPriority,
)
from host_brain.scheduler.health import SchedulerHealthReporter
from host_brain.core.errors import SCHED_TASK_NOT_FOUND, SCHED_TASK_DUPLICATE, SCHED_CONFIG_INVALID
from host_brain.core.ids import now_ms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scheduler", tags=["scheduler"])

# ── 全局注册表与健康报告器（由 fastapi_app.py 注入） ──
_registry: SchedulerTaskRegistry | None = None
_health_reporter: SchedulerHealthReporter | None = None


def set_scheduler_providers(
    registry: SchedulerTaskRegistry,
    health_reporter: SchedulerHealthReporter,
) -> None:
    """由 fastapi_app.py 在启动时注入调度器依赖。"""
    global _registry, _health_reporter
    _registry = registry
    _health_reporter = health_reporter


def _get_registry() -> SchedulerTaskRegistry:
    if _registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason_code": "SCHEDULER_NOT_INITIALIZED", "detail": "scheduler registry not initialized"},
        )
    return _registry


def _get_health() -> SchedulerHealthReporter:
    if _health_reporter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason_code": "SCHEDULER_NOT_INITIALIZED", "detail": "scheduler health reporter not initialized"},
        )
    return _health_reporter


# ── Pydantic 模型 ──

def get_scheduler_health_snapshot_safe() -> Dict[str, Any]:
    registry = _registry
    health = _health_reporter

    if registry is None and health is None:
        return {
            "scheduler_status": "UNAVAILABLE",
            "total_tasks": 0,
            "enabled_tasks": 0,
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": [],
            "ts_ms": now_ms(),
        }

    if registry is not None and health is None:
        tasks = [
            {
                "task_id": task.task_id,
                "trigger_mode": task.trigger_mode.value,
                "trigger_config": dict(task.trigger_config or {}),
                "priority": task.priority.value,
                "enabled": task.enabled,
                "status": "UNKNOWN",
                "last_run_ts": None,
                "next_run_ts": None,
                "error_count": 0,
                "suspended_reason": None,
                "last_reason_code": None,
            }
            for task in registry.all_tasks()
        ]
        return {
            "scheduler_status": "UNAVAILABLE",
            "total_tasks": len(tasks),
            "enabled_tasks": sum(1 for item in tasks if item["enabled"]),
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": tasks,
            "ts_ms": now_ms(),
        }

    try:
        snapshot = health.build_health_snapshot() if health is not None else {}
    except Exception as exc:
        logger.warning("Failed to build scheduler health snapshot", exc_info=True)
        tasks = []
        if registry is not None:
            tasks = [
                {
                    "task_id": task.task_id,
                    "trigger_mode": task.trigger_mode.value,
                    "trigger_config": dict(task.trigger_config or {}),
                    "priority": task.priority.value,
                    "enabled": task.enabled,
                    "status": "ERROR",
                    "last_run_ts": None,
                    "next_run_ts": None,
                    "error_count": 0,
                    "suspended_reason": None,
                    "last_reason_code": str(exc)[:120],
                }
                for task in registry.all_tasks()
            ]
        return {
            "scheduler_status": "ERROR",
            "total_tasks": len(tasks),
            "enabled_tasks": sum(1 for item in tasks if item["enabled"]),
            "suspended_tasks": 0,
            "error_tasks": len(tasks) if tasks else 1,
            "tasks": tasks,
            "ts_ms": now_ms(),
        }

    normalized = dict(snapshot or {})
    task_items: list[dict[str, Any]] = []
    for item in list(normalized.get("tasks") or []):
        if not isinstance(item, dict):
            continue
        task_copy = dict(item)
        task_id = str(task_copy.get("task_id") or "").strip()
        if registry is not None and task_id:
            task_config = registry.get(task_id)
            if task_config is not None:
                task_copy.setdefault("trigger_config", dict(task_config.trigger_config or {}))
                task_copy.setdefault("enabled", task_config.enabled)
                task_copy.setdefault("priority", task_config.priority.value)
                task_copy.setdefault("trigger_mode", task_config.trigger_mode.value)
        task_items.append(task_copy)
    normalized["tasks"] = task_items
    normalized.setdefault("ts_ms", now_ms())
    normalized.setdefault("scheduler_status", "UNAVAILABLE")
    normalized.setdefault("total_tasks", len(task_items))
    normalized.setdefault("enabled_tasks", sum(1 for item in task_items if item.get("enabled")))
    normalized.setdefault("suspended_tasks", 0)
    normalized.setdefault("error_tasks", 0)
    return normalized


class TaskCreateRequest(BaseModel):
    task_id: str = Field(..., description="任务唯一 ID")
    trigger_mode: str = Field(..., description="触发模式：cron / interval / once")
    trigger_config: Dict[str, Any] = Field(..., description="触发配置")
    intent_template: Dict[str, Any] = Field(..., description="Intent 模板")
    priority: str = Field(default="MEDIUM", description="优先级：HIGH / MEDIUM / LOW")
    mode_allowlist: Optional[List[str]] = Field(None, description="允许触发的 sys_mode 列表")
    enabled: bool = Field(default=True, description="是否启用")
    max_consecutive_failures: int = Field(default=3, description="最大连续失败次数")
    description: str = Field(default="", description="任务描述")


class TaskPatchRequest(BaseModel):
    trigger_config: Optional[Dict[str, Any]] = None
    intent_template: Optional[Dict[str, Any]] = None
    priority: Optional[str] = None
    mode_allowlist: Optional[List[str]] = None
    enabled: Optional[bool] = None
    max_consecutive_failures: Optional[int] = None
    description: Optional[str] = None


class TaskResponse(BaseModel):
    task_id: str
    trigger_mode: str
    trigger_config: Dict[str, Any]
    intent_template: Dict[str, Any]
    priority: str
    mode_allowlist: Optional[List[str]]
    enabled: bool
    max_consecutive_failures: int
    description: str


class TaskWithHealthResponse(BaseModel):
    task: TaskResponse
    health: Dict[str, Any]


# ── 辅助函数 ──

def _task_to_response(task: SchedulerTaskConfig) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        trigger_mode=task.trigger_mode.value,
        trigger_config=task.trigger_config,
        intent_template=task.intent_template,
        priority=task.priority.value,
        mode_allowlist=task.mode_allowlist,
        enabled=task.enabled,
        max_consecutive_failures=task.max_consecutive_failures,
        description=task.description,
    )


def _parse_trigger_mode(mode_str: str) -> TriggerMode:
    try:
        return TriggerMode(mode_str.lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"reason_code": SCHED_CONFIG_INVALID, "detail": f"invalid trigger_mode: {mode_str!r}"},
        )


def _parse_priority(priority_str: str) -> TaskPriority:
    try:
        return TaskPriority(priority_str.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"reason_code": SCHED_CONFIG_INVALID, "detail": f"invalid priority: {priority_str!r}"},
        )


# ── 端点 ──

@router.post("/tasks", status_code=status.HTTP_201_CREATED, response_model=TaskResponse)
async def create_task(req: TaskCreateRequest) -> TaskResponse:
    """POST /scheduler/tasks — 注册新调度任务。"""
    registry = _get_registry()

    if registry.get(req.task_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason_code": SCHED_TASK_DUPLICATE, "detail": f"task already exists: {req.task_id!r}"},
        )

    trigger_mode = _parse_trigger_mode(req.trigger_mode)
    priority = _parse_priority(req.priority)

    task = SchedulerTaskConfig(
        task_id=req.task_id,
        trigger_mode=trigger_mode,
        trigger_config=req.trigger_config,
        intent_template=req.intent_template,
        priority=priority,
        mode_allowlist=req.mode_allowlist,
        enabled=req.enabled,
        max_consecutive_failures=req.max_consecutive_failures,
        description=req.description,
    )

    try:
        task.validate()
        registry.register(task)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"reason_code": SCHED_CONFIG_INVALID, "detail": str(exc)},
        )

    return _task_to_response(task)


@router.get("/tasks", response_model=List[TaskWithHealthResponse])
async def list_tasks() -> List[TaskWithHealthResponse]:
    """GET /scheduler/tasks — 列出所有任务（含健康状态）。"""
    registry = _get_registry()
    health = _get_health()
    snapshot = health.build_health_snapshot()
    health_by_id = {t["task_id"]: t for t in snapshot["tasks"]}

    result = []
    for task in registry.all_tasks():
        result.append(TaskWithHealthResponse(
            task=_task_to_response(task),
            health=health_by_id.get(task.task_id, {}),
        ))
    return result


@router.get("/tasks/{task_id}", response_model=TaskWithHealthResponse)
async def get_task(task_id: str) -> TaskWithHealthResponse:
    """GET /scheduler/tasks/{task_id} — 获取单个任务详情。"""
    registry = _get_registry()
    health = _get_health()

    task = registry.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": SCHED_TASK_NOT_FOUND, "detail": f"task not found: {task_id!r}"},
        )

    snapshot = health.build_health_snapshot()
    health_by_id = {t["task_id"]: t for t in snapshot["tasks"]}

    return TaskWithHealthResponse(
        task=_task_to_response(task),
        health=health_by_id.get(task_id, {}),
    )


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: str, req: TaskPatchRequest) -> TaskResponse:
    """PATCH /scheduler/tasks/{task_id} — 更新任务配置。"""
    registry = _get_registry()

    task = registry.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": SCHED_TASK_NOT_FOUND, "detail": f"task not found: {task_id!r}"},
        )

    # 应用更新
    if req.trigger_config is not None:
        task.trigger_config = req.trigger_config
    if req.intent_template is not None:
        task.intent_template = req.intent_template
    if req.priority is not None:
        task.priority = _parse_priority(req.priority)
    if req.mode_allowlist is not None:
        task.mode_allowlist = req.mode_allowlist
    if req.enabled is not None:
        task.enabled = req.enabled
    if req.max_consecutive_failures is not None:
        task.max_consecutive_failures = req.max_consecutive_failures
    if req.description is not None:
        task.description = req.description

    return _task_to_response(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str) -> None:
    """DELETE /scheduler/tasks/{task_id} — 注销任务。"""
    registry = _get_registry()

    removed = registry.unregister(task_id)
    if removed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": SCHED_TASK_NOT_FOUND, "detail": f"task not found: {task_id!r}"},
        )


@router.post("/tasks/{task_id}/enable", response_model=TaskResponse)
async def enable_task(task_id: str) -> TaskResponse:
    """POST /scheduler/tasks/{task_id}/enable — 启用任务。"""
    registry = _get_registry()

    task = registry.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": SCHED_TASK_NOT_FOUND, "detail": f"task not found: {task_id!r}"},
        )

    task.enabled = True
    return _task_to_response(task)


@router.post("/tasks/{task_id}/disable", response_model=TaskResponse)
async def disable_task(task_id: str) -> TaskResponse:
    """POST /scheduler/tasks/{task_id}/disable — 禁用任务。"""
    registry = _get_registry()

    task = registry.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": SCHED_TASK_NOT_FOUND, "detail": f"task not found: {task_id!r}"},
        )

    task.enabled = False
    return _task_to_response(task)


@router.get("/health")
async def scheduler_health() -> Dict[str, Any]:
    """GET /scheduler/health — 所有任务健康快照（T-SCHED-05）。"""
    health = _get_health()
    return health.build_health_snapshot()
