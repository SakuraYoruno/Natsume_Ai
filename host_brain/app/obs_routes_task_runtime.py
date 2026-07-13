"""Read-only Task Runtime projections for the Monitor operator console."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException, Query

from host_brain.agent_runtime.operator_read_model import OperatorReadModelService
from host_brain.persistence.review_repository import ReviewStore, ReviewStoreError
from host_brain.persistence.task_repository import DurableTaskStore, TaskStoreError
from host_brain.app.obs_routes_core import router
from host_brain.app.settings.core import get_core_settings
from host_brain.integrations.runtime_events import RuntimeEventError, RuntimeEventStore
from host_brain.app.observability_runtime.projection_runtime import (
    ProjectionOperatorReadModelService,
    get_query_projection_worker,
    refresh_task_projections,
)
from host_brain.app.observability_runtime.projection_daemon import register_projection_job
from host_brain.observability.projection_store import QueryProjectionError


_service_provider: Callable[[], OperatorReadModelService] | None = None
_projection_service: ProjectionOperatorReadModelService | None = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3"


def _db_path(env_name: str) -> Path:
    raw = _text(os.environ.get(env_name))
    return Path(raw).expanduser() if raw else _default_db_path()


def set_operator_read_model_service_provider(
    provider: Callable[[], OperatorReadModelService] | None,
) -> None:
    """Install a test/composition provider without changing the read contract."""

    global _projection_service, _service_provider
    _service_provider = provider
    _projection_service = None


def _get_operator_read_model_service() -> OperatorReadModelService:
    global _projection_service
    _require_operator_views_enabled()
    if _service_provider is not None:
        return _service_provider()
    if _projection_service is None:
        from host_brain.composition.process_services import get_query_projection_store

        _projection_service = ProjectionOperatorReadModelService(
            store=get_query_projection_store(),
        )
    return _projection_service  # type: ignore[return-value]


def refresh_task_runtime_query_projections() -> dict[str, int]:
    """Background-only Task/Review projection entry."""

    source = OperatorReadModelService(
        task_store=DurableTaskStore(_db_path("HOST_TASK_RUNTIME_DB_PATH")),
        review_store=ReviewStore(_db_path("HOST_AGENT_REVIEW_DB_PATH")),
        event_store=RuntimeEventStore(_db_path("HOST_RUNTIME_EVENT_DB_PATH")),
    )
    return refresh_task_projections(source, get_query_projection_worker())


register_projection_job("task-runtime", refresh_task_runtime_query_projections)


def _require_operator_views_enabled() -> None:
    if not get_core_settings().nle_operator_views_enabled:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "NLE_OPERATOR_VIEWS_DISABLED",
                "detail": "set HOST_NLE_OPERATOR_VIEWS_ENABLED=true to expose operator projections",
            },
        )


def _raise_read_model_error(exc: Exception) -> None:
    reason = _text(getattr(exc, "reason_code", "")) or "OPERATOR_READ_MODEL_UNAVAILABLE"
    detail = _text(getattr(exc, "detail", "")) or str(exc)
    if reason in {"TASK_STORE_NOT_FOUND", "REVIEW_STORE_NOT_FOUND"}:
        status = 404
    elif reason.endswith("SCHEMA_NOT_READY"):
        status = 503
    else:
        status = 409
    raise HTTPException(
        status_code=status,
        detail={"reason_code": reason, "detail": detail},
    ) from exc


@router.get("/task-runtime/executions")
async def list_task_runtime_executions(
    limit: int = Query(100, ge=1, le=500),
    service: OperatorReadModelService = Depends(_get_operator_read_model_service),
) -> dict[str, Any]:
    _require_operator_views_enabled()
    try:
        return service.list_executions(limit=limit)
    except (TaskStoreError, ReviewStoreError, RuntimeEventError, QueryProjectionError) as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/review-locator")
async def get_task_runtime_review_locator(
    review_id: str = Query(..., min_length=1, max_length=512),
    service: OperatorReadModelService = Depends(_get_operator_read_model_service),
) -> dict[str, Any]:
    _require_operator_views_enabled()
    try:
        return service.get_review(review_id)
    except (TaskStoreError, ReviewStoreError, RuntimeEventError, QueryProjectionError) as exc:
        _raise_read_model_error(exc)


@router.get("/task-runtime/executions/{graph_id}")
async def get_task_runtime_execution(
    graph_id: str,
    revision: int = Query(..., ge=1),
    after_cursor: int = Query(0, ge=0),
    event_limit: int = Query(100, ge=1, le=1000),
    service: OperatorReadModelService = Depends(_get_operator_read_model_service),
) -> dict[str, Any]:
    _require_operator_views_enabled()
    try:
        return service.get_execution(
            graph_id,
            revision,
            after_cursor=after_cursor,
            event_limit=event_limit,
        )
    except (TaskStoreError, ReviewStoreError, RuntimeEventError, QueryProjectionError) as exc:
        _raise_read_model_error(exc)


__all__ = [
    "get_task_runtime_review_locator",
    "get_task_runtime_execution",
    "list_task_runtime_executions",
    "set_operator_read_model_service_provider",
]
