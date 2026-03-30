"""调度器管理路由（Ref #107）。

提供 HostTaskScheduler 的 REST 管理接口：
  GET  /scheduler/tasks          — 列出所有任务（含状态）
  GET  /scheduler/health         — 调度器健康快照
  POST /scheduler/tasks/{id}/enable   — 启用任务
  POST /scheduler/tasks/{id}/disable  — 禁用任务
  POST /scheduler/tasks/{id}/trigger  — 立即触发任务
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from host_brain.app.automation.task_scheduler import get_scheduler

_logger = logging.getLogger("Natsume.host.automation.scheduler_routes")

router = APIRouter(tags=["host-scheduler"])


# ---------------------------------------------------------------------------
# GET /scheduler/tasks — 列出所有任务
# ---------------------------------------------------------------------------

@router.get("/tasks")
async def list_scheduler_tasks() -> dict[str, Any]:
    """列出所有调度任务（含运行时状态），按优先级排序。"""
    scheduler = get_scheduler()
    tasks = scheduler.get_all_tasks_status()
    return {
        "total": len(tasks),
        "running": scheduler._running,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# GET /scheduler/health — 调度器健康快照
# ---------------------------------------------------------------------------

@router.get("/health")
async def scheduler_health() -> dict[str, Any]:
    """返回调度器健康快照（running/task_count/tasks）。"""
    scheduler = get_scheduler()
    return scheduler.health()


# ---------------------------------------------------------------------------
# POST /scheduler/tasks/{task_id}/enable — 启用任务
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/enable")
async def enable_scheduler_task(task_id: str) -> dict[str, Any]:
    """启用指定调度任务。"""
    scheduler = get_scheduler()
    ok = scheduler.enable(task_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务 {task_id!r} 不存在",
        )
    _logger.info("调度任务已启用: task_id=%s", task_id)
    return {"task_id": task_id, "enabled": True, "status": "OK"}


# ---------------------------------------------------------------------------
# POST /scheduler/tasks/{task_id}/disable — 禁用任务
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/disable")
async def disable_scheduler_task(task_id: str) -> dict[str, Any]:
    """禁用指定调度任务（不取消正在运行的，下次不再触发）。"""
    scheduler = get_scheduler()
    ok = scheduler.disable(task_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务 {task_id!r} 不存在",
        )
    _logger.info("调度任务已禁用: task_id=%s", task_id)
    return {"task_id": task_id, "enabled": False, "status": "OK"}


# ---------------------------------------------------------------------------
# POST /scheduler/tasks/{task_id}/trigger — 立即触发任务
# ---------------------------------------------------------------------------

@router.post("/tasks/{task_id}/trigger")
async def trigger_scheduler_task(task_id: str) -> dict[str, Any]:
    """立即触发指定调度任务（重置 last_run_ts，下次调度循环即触发）。"""
    scheduler = get_scheduler()
    ok = scheduler.trigger_now(task_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务 {task_id!r} 不存在或已禁用",
        )
    _logger.info("调度任务已触发: task_id=%s", task_id)
    return {"task_id": task_id, "triggered": True, "status": "OK"}


# ---------------------------------------------------------------------------
# PATCH /scheduler/tasks/{task_id} — 更新任务配置（兼容前端 trigger_config 写法）
# ---------------------------------------------------------------------------

@router.patch("/tasks/{task_id}")
async def patch_scheduler_task(task_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """
    更新任务配置（Ref #107）。

    前端通过 PATCH + { trigger_config: { next_run_ts: 0 } } 实现立即触发。
    """
    scheduler = get_scheduler()
    # 使用公开方法检查任务是否存在（避免直接访问私有属性 _tasks）
    all_tasks = {t["task_id"]: t for t in scheduler.get_all_tasks_status()}
    if task_id not in all_tasks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"任务 {task_id!r} 不存在",
        )
    trigger_config = body.get("trigger_config") or {}
    if isinstance(trigger_config, dict) and trigger_config.get("next_run_ts") == 0:
        # 立即触发：通过公开的 trigger_now 方法重置 last_run_ts
        ok = scheduler.trigger_now(task_id)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"任务 {task_id!r} 已禁用，无法触发",
            )
        _logger.info("调度任务通过 PATCH 触发: task_id=%s", task_id)
    return {"task_id": task_id, "patched": True, "status": "OK"}
