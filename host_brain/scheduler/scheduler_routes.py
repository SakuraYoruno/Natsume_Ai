"""HTTP adapters for the single governed scheduler command service."""

from __future__ import annotations

import inspect
import time
from typing import Any, Mapping

from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=255)
    trigger_mode: str
    trigger_config: dict[str, Any]
    intent_template: dict[str, Any]
    priority: str = "MEDIUM"
    mode_allowlist: list[str] | None = None
    enabled: bool = True
    max_consecutive_failures: int = Field(default=3, ge=1)
    description: str = ""
    resource_profile: dict[str, Any] = Field(default_factory=dict)
    worker_key: str | None = None
    expected_head_version: int = Field(default=0, ge=0)
    next_scheduled_at_ms: int | None = Field(default=None, ge=0)


class TaskPatchRequest(BaseModel):
    trigger_mode: str | None = None
    trigger_config: dict[str, Any] | None = None
    intent_template: dict[str, Any] | None = None
    priority: str | None = None
    mode_allowlist: list[str] | None = None
    enabled: bool | None = None
    max_consecutive_failures: int | None = Field(default=None, ge=1)
    description: str | None = None
    resource_profile: dict[str, Any] | None = None
    worker_key: str | None = None
    expected_head_version: int | None = Field(default=None, ge=1)
    next_scheduled_at_ms: int | None = Field(default=None, ge=0)


def build_scheduler_router(*, prefix: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["scheduler"])

    @router.post("/tasks", status_code=status.HTTP_201_CREATED)
    async def create_task(request: Request, body: TaskCreateRequest) -> dict[str, Any]:
        definition = body.model_dump(
            exclude={"expected_head_version", "next_scheduled_at_ms"}
        )
        definition["worker_key"] = body.worker_key or "scheduler_production"
        definition["retry_policy"] = {
            "max_attempts": body.max_consecutive_failures,
            "retry_backoff_ms": 0,
        }
        return await _execute_admin_control(
            request=request,
            command_id="scheduler.task.publish",
            payload={
                "task_id": body.task_id,
                "definition": definition,
                "expected_head_version": body.expected_head_version,
                "next_scheduled_at_ms": body.next_scheduled_at_ms,
            },
        )

    @router.get("/tasks")
    async def list_tasks(request: Request) -> list[dict[str, Any]]:
        service, _authority, _received_at_ms = _request_context(request)
        value = await _invoke(service.list_definitions())
        return [_as_dict(item) for item in list(value or ())]

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> dict[str, Any]:
        service, _authority, _received_at_ms = _request_context(request)
        value = await _invoke(service.get_definition(task_id))
        if value is None:
            raise HTTPException(status_code=404, detail="SCHEDULER_DEFINITION_NOT_FOUND")
        return _as_dict(value)

    @router.patch("/tasks/{task_id}")
    async def update_task(
        task_id: str,
        request: Request,
        body: TaskPatchRequest,
    ) -> dict[str, Any]:
        service, _authority, _received_at_ms = _request_context(request)
        current_raw = await _invoke(service.get_definition(task_id))
        if current_raw is None:
            raise HTTPException(status_code=404, detail="SCHEDULER_DEFINITION_NOT_FOUND")
        current = _as_dict(current_raw)
        definition = dict(current.get("definition") or current)
        patch = body.model_dump(
            exclude_none=True,
            exclude={"expected_head_version", "next_scheduled_at_ms"},
        )
        definition.update(patch)
        if body.worker_key is not None:
            definition["worker_key"] = body.worker_key
        if body.max_consecutive_failures is not None:
            retry_policy = dict(definition.get("retry_policy") or {})
            retry_policy["max_attempts"] = body.max_consecutive_failures
            retry_policy.setdefault("retry_backoff_ms", 0)
            definition["retry_policy"] = retry_policy
        expected = body.expected_head_version or int(current.get("head_version") or 0)
        return await _execute_admin_control(
            request=request,
            command_id="scheduler.task.publish",
            payload={
                "task_id": task_id,
                "definition": definition,
                "expected_head_version": expected,
                "next_scheduled_at_ms": (
                    body.next_scheduled_at_ms
                    if body.next_scheduled_at_ms is not None
                    else current.get("next_scheduled_at_ms")
                ),
            },
        )

    @router.delete("/tasks/{task_id}")
    async def delete_task(
        task_id: str,
        request: Request,
        expected_head_version: int | None = None,
    ) -> dict[str, Any]:
        service, _authority, _received_at_ms = _request_context(request)
        expected = await _expected_head_version(
            service,
            task_id=task_id,
            supplied=expected_head_version,
        )
        return await _execute_admin_control(
            request=request,
            command_id="scheduler.task.delete",
            payload={
                "task_id": task_id,
                "expected_head_version": expected,
            },
        )

    @router.post("/tasks/{task_id}/enable")
    async def enable_task(
        task_id: str,
        request: Request,
        expected_head_version: int | None = None,
    ) -> dict[str, Any]:
        return await _set_enabled(
            request=request,
            task_id=task_id,
            enabled=True,
            expected_head_version=expected_head_version,
        )

    @router.post("/tasks/{task_id}/disable")
    async def disable_task(
        task_id: str,
        request: Request,
        expected_head_version: int | None = None,
    ) -> dict[str, Any]:
        return await _set_enabled(
            request=request,
            task_id=task_id,
            enabled=False,
            expected_head_version=expected_head_version,
        )

    @router.post("/tasks/{task_id}/trigger")
    async def trigger_task(
        task_id: str,
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        service, authority, received_at_ms = _request_context(request)
        return _as_dict(
            await _invoke(
                service.trigger_manual(
                    task_id=task_id,
                    authority=authority,
                    payload=dict(payload or {}),
                    received_at_ms=received_at_ms,
                )
            )
        )

    @router.get("/health")
    async def scheduler_health(request: Request) -> dict[str, Any]:
        service, _authority, _received_at_ms = _request_context(request)
        return _as_dict(await _invoke(service.health()))

    return router


async def _set_enabled(
    *,
    request: Request,
    task_id: str,
    enabled: bool,
    expected_head_version: int | None,
) -> dict[str, Any]:
    service, _authority, _received_at_ms = _request_context(request)
    expected = await _expected_head_version(
        service,
        task_id=task_id,
        supplied=expected_head_version,
    )
    return await _execute_admin_control(
        request=request,
        command_id=(
            "scheduler.task.enable"
            if enabled
            else "scheduler.task.disable"
        ),
        payload={
            "task_id": task_id,
            "expected_head_version": expected,
        },
    )


async def _execute_admin_control(
    *,
    request: Request,
    command_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    _service, authority, _received_at_ms = _request_context(request)
    runtime = getattr(request.state, "scheduler_admin_runtime", None)
    execute = getattr(runtime, "execute_scheduler_admin_control", None)
    if not callable(execute):
        raise HTTPException(
            status_code=503,
            detail="SCHEDULER_ADMIN_RUNTIME_UNAVAILABLE",
        )
    request_id = str(
        getattr(authority, "request_id", None)
        or (
            authority.get("request_id")
            if isinstance(authority, Mapping)
            else ""
        )
        or ""
    ).strip()
    if not request_id:
        raise HTTPException(
            status_code=403,
            detail="SCHEDULER_COMMAND_AUTHORITY_REQUIRED",
        )
    return _as_dict(
        await _invoke(
            execute(
                command_id=command_id,
                payload=dict(payload),
                authority=authority,
                request_id=request_id,
                method=str(request.method).upper(),
                normalized_path=str(request.url.path),
            )
        )
    )


def _request_context(request: Request) -> tuple[Any, Any, int]:
    service = getattr(request.state, "scheduler_command_service", None)
    authority = getattr(request.state, "scheduler_command_authority", None)
    if service is None:
        raise HTTPException(status_code=503, detail="SCHEDULER_COMMAND_SERVICE_UNAVAILABLE")
    if authority is None:
        raise HTTPException(status_code=403, detail="SCHEDULER_COMMAND_AUTHORITY_REQUIRED")
    received_at_ms = int(
        getattr(request.state, "request_started_ms", 0) or time.time() * 1000
    )
    return service, authority, received_at_ms


async def _invoke(value: Any) -> Any:
    try:
        return await value if inspect.isawaitable(value) else value
    except HTTPException:
        raise
    except Exception as exc:
        reason = str(getattr(exc, "reason_code", "") or "SCHEDULER_COMMAND_FAILED")
        detail: Any = {"reason_code": reason, "detail": str(getattr(exc, "detail", exc))[:500]}
        if "NOT_FOUND" in reason:
            code = 404
        elif any(token in reason for token in ("CONFLICT", "VERSION", "REPLAY", "DUPLICATE")):
            code = 409
        elif any(token in reason for token in ("INVALID", "REQUIRED")):
            code = 422
        else:
            code = 503
        raise HTTPException(status_code=code, detail=detail) from exc


async def _expected_head_version(
    service: Any,
    *,
    task_id: str,
    supplied: int | None,
) -> int:
    if supplied is not None:
        return int(supplied)
    current = await _invoke(service.get_definition(task_id))
    if current is None:
        raise HTTPException(status_code=404, detail="SCHEDULER_DEFINITION_NOT_FOUND")
    version = int(_as_dict(current).get("head_version") or 0)
    if version < 1:
        raise HTTPException(status_code=503, detail="SCHEDULER_HEAD_VERSION_INVALID")
    return version


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    for method_name in ("to_dict", "as_dict", "model_dump"):
        method = getattr(value, method_name, None)
        if callable(method):
            mapped = method()
            if isinstance(mapped, Mapping):
                return dict(mapped)
    raise HTTPException(status_code=503, detail="SCHEDULER_SERVICE_RESULT_INVALID")


router = build_scheduler_router(prefix="/scheduler")


__all__ = [
    "TaskCreateRequest",
    "TaskPatchRequest",
    "build_scheduler_router",
    "router",
]
