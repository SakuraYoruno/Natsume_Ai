"""调度器 Executor。

将 scheduler_registry.py 归一化层的 payload 路由到
host_brain/scheduler/ 模块执行。

支持的 action：
  - add：添加调度任务
  - cancel：取消调度任务
  - list：列出调度任务
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("Natsume.skills.scheduler_executor")


def execute_scheduler_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行调度器操作。

    Args:
        args: 归一化 payload（来自 scheduler_registry handler 的返回值）
        context: 执行上下文（含 scheduler 实例等回调）

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"scheduler::{action}")

    try:
        result = _dispatch_scheduler_action(action, payload, context)
        return {
            "status": "OK",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
        }
    except Exception as exc:
        _logger.warning("Scheduler executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_scheduler_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的调度器操作。"""
    if action == "add":
        return _execute_add(payload, context)
    if action == "cancel":
        return _execute_cancel(payload, context)
    if action == "list":
        return _execute_list(payload, context)
    return {"note": f"unknown scheduler action: {action}"}


def _execute_add(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """添加调度任务。"""
    task_name = str(payload.get("task_name") or "")
    task_type = str(payload.get("task_type") or "custom")
    trigger_type = str(payload.get("trigger_type") or "once")
    trigger_config = payload.get("trigger_config") or {}
    task_args = payload.get("task_args") or {}
    enabled = bool(payload.get("enabled", True))
    max_retries = int(payload.get("max_retries") or 0)
    priority = int(payload.get("priority") or 3)

    ctx = context or {}
    scheduler = ctx.get("scheduler")
    if scheduler is not None:
        try:
            job_id = scheduler.add_job(
                task_name=task_name,
                task_type=task_type,
                trigger_type=trigger_type,
                trigger_config=trigger_config,
                task_args=task_args,
                enabled=enabled,
                max_retries=max_retries,
                priority=priority,
            )
            _logger.info("调度任务添加: task=%s, trigger=%s, job_id=%s", task_name, trigger_type, job_id)
            return {"task_name": task_name, "job_id": str(job_id or ""), "added": True}
        except Exception as exc:
            _logger.debug("scheduler.add_job 调用失败: %s", exc)

    # 降级：通过 Host API 添加任务
    resp = _call_host_api(
        endpoint="/v1/scheduler/jobs",
        body={
            "task_name": task_name,
            "task_type": task_type,
            "trigger_type": trigger_type,
            "trigger_config": trigger_config,
            "task_args": task_args,
            "enabled": enabled,
            "max_retries": max_retries,
            "priority": priority,
        },
    )
    _logger.info("调度任务添加（Host API）: task=%s", task_name)
    return resp or {"task_name": task_name, "added": False, "note": "no scheduler available"}


def _execute_cancel(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """取消调度任务。"""
    task_name = payload.get("task_name")
    task_type = payload.get("task_type")
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        _logger.info("调度任务取消（dry_run）: task=%s, type=%s", task_name, task_type)
        return {"dry_run": True, "task_name": task_name, "task_type": task_type}

    ctx = context or {}
    scheduler = ctx.get("scheduler")
    if scheduler is not None:
        try:
            count = scheduler.cancel_job(task_name=task_name, task_type=task_type)
            _logger.info("调度任务取消: task=%s, count=%s", task_name, count)
            return {"task_name": task_name, "cancelled_count": count, "cancelled": True}
        except Exception as exc:
            _logger.debug("scheduler.cancel_job 调用失败: %s", exc)

    _logger.info("调度任务取消（无可用后端）: task=%s", task_name)
    return {"task_name": task_name, "cancelled": False, "note": "no scheduler available"}


def _execute_list(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """列出调度任务。"""
    task_type = payload.get("task_type")
    enabled_only = bool(payload.get("enabled_only", False))
    limit = int(payload.get("limit") or 50)

    ctx = context or {}
    scheduler = ctx.get("scheduler")
    if scheduler is not None:
        try:
            jobs = scheduler.list_jobs(
                task_type=task_type,
                enabled_only=enabled_only,
                limit=limit,
            )
            _logger.info("调度任务列表: count=%d", len(jobs or []))
            return {"jobs": jobs or [], "count": len(jobs or [])}
        except Exception as exc:
            _logger.debug("scheduler.list_jobs 调用失败: %s", exc)

    return {"jobs": [], "count": 0, "note": "no scheduler available"}


def _call_host_api(endpoint: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """向 Host API 发送请求（降级路径）。"""
    try:
        import os
        import urllib.request
        import json as _json

        host = str(os.environ.get("HOST_API_HOST") or "127.0.0.1").strip()
        port_raw = str(os.environ.get("HOST_API_PORT") or "17700").strip()
        try:
            port = int(port_raw)
        except ValueError:
            port = 17700

        url = f"http://{host}:{port}{endpoint}"
        data = _json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            return _json.loads(raw) if raw else None
    except Exception as exc:
        _logger.debug("Host API 调用失败（endpoint=%s）: %s", endpoint, exc)
        return None
