"""记忆的只读观测路由与受 Host Life authority 绑定的维护操作。"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from host_brain.app.context.memory_context_orchestrator import (
    run_memory_sanitize,
)
from host_brain.app.host_life_route_authority import (
    require_host_life_route_authority,
)
from host_brain.app.host_route_ports import (
    HostRoutePorts,
    bind_host_route_endpoint,
)
from host_brain.conversation.memory_spine import backfill_conversation_turn_memory_spine
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.sqlite_backup import SQLiteBackupManager
from host_brain.observability.operator_query_gateway import OperatorQueryGateway
from host_brain.app.observability_runtime.remaining_query_projection import (
    MEMORY_CONTEXT_KIND,
    MEMORY_EVENTS_KIND,
    MEMORY_HEALTH_KIND,
    read_from_store,
    response_meta,
)
from host_brain.app.obs_route_ports import require_obs_query_projection_store


_read_router = APIRouter(prefix="/v1/memory", tags=["memory"])


def _require_ledger(route_ports: HostRoutePorts) -> Any:
    """取得当前应用账本；缺失/失效时失败关闭，维护路径不得退回到临时存储。"""
    provider = route_ports.ledger_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail="HOST_MEMORY_LEDGER_PROVIDER_UNAVAILABLE",
        )
    try:
        ledger = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="HOST_MEMORY_LEDGER_PROVIDER_UNAVAILABLE",
        ) from exc
    if ledger is None:
        raise HTTPException(
            status_code=503,
            detail="HOST_MEMORY_LEDGER_PROVIDER_INVALID",
        )
    return ledger


def _rebuild_memory_system(*, ledger: Any, person_id: str) -> dict[str, Any]:
    """重建记忆事件源。

    非内存数据库先生成可验证备份，再清空并写入新的 epoch 事件以留下恢复锚点；此函数
    本身不作授权判断，路由绑定必须在调用前要求 Host Life authority，避免诊断端点
    获得破坏性写权。
    """
    repository = MemoryEventRepository.from_ledger(ledger)
    ts_ms = int(time.time() * 1000)
    backup_receipt: dict[str, Any] | None = None
    if repository.db_path != ":memory:":
        backup_dir = Path("artifacts") / "memory_rebuild_backup" / f"{ts_ms}-{uuid.uuid4().hex[:8]}"
        backup_receipt = SQLiteBackupManager().create_verified_backup(
            source_path=repository.db_path,
            backup_path=backup_dir / "audit.sqlite3",
            created_at_ms=ts_ms,
        ).as_dict()
    cleared = repository.reset()
    event_id = f"memevt_{uuid.uuid4().hex}"
    repository.append_event(
        {
            "event_id": event_id,
            "person_id": str(person_id or "default"),
            "session_id": "",
            "conversation_id": "",
            "source_kind": "memory_rebuild",
            "event_kind": "memory_system_rebuilt",
            "visibility": "system",
            "role": "",
            "content": "",
            "content_summary": "memory event source rebuilt; fragmented runtime memory cleared",
            "causality": {},
            "refs": {
                "backup_path": (backup_receipt or {}).get("backup_path", ""),
                "backup_sha256": (backup_receipt or {}).get("sha256", ""),
                "cleared_tables": cleared,
            },
            "salience": 0.4,
            "valence": 0.0,
            "uncertainty": 0.02,
            "confidence": 1.0,
            "projection_status": {},
            "created_at_ms": ts_ms,
            "active": True,
        }
    )
    return {
        "backup_path": (backup_receipt or {}).get("backup_path", ""),
        "backup_receipt": backup_receipt,
        "cleared_tables": cleared,
        "initialized_tables": ["memory_event"],
        "epoch_event_id": event_id,
    }


async def _require_memory_debug_scope(request: Request) -> None:
    """以 operator scope 保护结构化记忆诊断，且诊断响应不返回原始内容。"""

    gateway = getattr(request.app.state, "operator_query_gateway", None)
    if not isinstance(gateway, OperatorQueryGateway):
        gateway = OperatorQueryGateway.from_environment()
    context = gateway.open_asgi_context(request.scope)
    context.require_scope("obs:memory:read")


@_read_router.get("/context/debug")
async def memory_context_debug(
    session_id: str = Query(""),
    person_id: str = Query("default"),
    user_message: str = Query(""),
    current_user_message: str = Query(""),
    limit: int = Query(8, ge=1, le=30),
    _authority: None = Depends(_require_memory_debug_scope),
    projection_store: Any = Depends(require_obs_query_projection_store),
) -> dict[str, Any]:
    payload, meta = read_from_store(
        projection_store,
        MEMORY_CONTEXT_KIND,
        str(person_id or "default"),
    )
    return {
        "person_id": str(person_id or "default"),
        "session_id": str(session_id or ""),
        "layer_order": list((payload or {}).get("layer_order") or []),
        "reason_codes": list((payload or {}).get("reason_codes") or []),
        "binding": dict((payload or {}).get("binding") or {}),
        "counts": dict((payload or {}).get("counts") or {}),
        "projection_status": dict((payload or {}).get("projection_status") or {}),
        "content_withheld": True,
        "query_content_accepted": bool(user_message or current_user_message),
        "limit": int(limit),
        **response_meta(meta),
    }


@_read_router.get("/health")
async def memory_health_route(
    person_id: str = Query("default"),
    projection_store: Any = Depends(require_obs_query_projection_store),
) -> dict[str, Any]:
    payload, meta = read_from_store(
        projection_store,
        MEMORY_HEALTH_KIND,
        str(person_id or "default"),
    )
    return {
        **dict(payload or {}),
        "person_id": str(person_id or "default"),
        **response_meta(meta),
    }


@_read_router.get("/events/recent")
async def memory_events_recent(
    person_id: str = Query("default"),
    session_id: str = Query(""),
    visibility: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    projection_store: Any = Depends(require_obs_query_projection_store),
) -> dict[str, Any]:
    payload, meta = read_from_store(
        projection_store,
        MEMORY_EVENTS_KIND,
        str(person_id or "default"),
    )
    items = []
    for raw in list((payload or {}).get("items") or []):
        if not isinstance(raw, dict):
            continue
        if session_id and str(raw.get("session_id") or "") != str(session_id):
            continue
        if visibility and str(raw.get("visibility") or "") != str(visibility):
            continue
        items.append(dict(raw))
        if len(items) >= int(limit):
            break
    return {
        "person_id": str(person_id or "default"),
        "items": items,
        "content_withheld": True,
        **response_meta(meta),
    }


async def memory_rebuild_run(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    return _rebuild_memory_system(
        ledger=_require_ledger(_route_ports),
        person_id=str(payload.get("person_id") or "default"),
    )


async def memory_sanitize_run(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    return run_memory_sanitize(
        ledger=_require_ledger(_route_ports),
        person_id=str(payload.get("person_id") or "default"),
        dry_run=bool(payload.get("dry_run", False)),
    )


async def memory_spine_backfill(
    payload: dict[str, Any] = Body(default_factory=dict),
    *,
    _route_ports: HostRoutePorts,
) -> dict[str, Any]:
    life = require_host_life_route_authority(_route_ports)
    return backfill_conversation_turn_memory_spine(
        ledger=_require_ledger(_route_ports),
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        person_id=str(payload.get("person_id") or "default"),
        session_id=str(payload.get("session_id") or ""),
        limit=int(payload.get("limit") or 500),
        dry_run=bool(payload.get("dry_run", True)),
        include_asr_sessions=bool(payload.get("include_asr_sessions", False)),
    )


def create_memory_router(route_ports: HostRoutePorts) -> APIRouter:
    """Create memory routes bound to one Host application's authorities."""

    if not isinstance(route_ports, HostRoutePorts):
        raise TypeError("route_ports must be HostRoutePorts")
    router = APIRouter()
    router.include_router(_read_router)

    def add(path: str, endpoint: Any) -> None:
        router.add_api_route(
            path,
            bind_host_route_endpoint(endpoint, route_ports),
            methods=["POST"],
            tags=["memory"],
        )

    add("/v1/memory/rebuild/run", memory_rebuild_run)
    add("/v1/memory/sanitize/run", memory_sanitize_run)
    add("/v1/memory/spine/backfill", memory_spine_backfill)
    return router


# Compatibility-only read router; mutation routes fail closed without ports.
router = create_memory_router(HostRoutePorts())


__all__ = ["create_memory_router", "router"]
