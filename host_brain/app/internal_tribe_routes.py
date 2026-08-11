from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from host_brain.tribe.event_bus import (
    InMemoryTribeEventBus,
    get_tribe_event_bus,
    new_tribe_event,
)

router = APIRouter(prefix="/internal", tags=["internal-tribe"])

def _validate_internal_token(x_internal_token: str | None) -> None:
    expected = str(os.environ.get("HOST_INTERNAL_TRIBE_TOKEN") or "").strip()
    if not expected:
        return
    if str(x_internal_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED_INTERNAL_TRIBE_EVENT")


class TribeEventIn(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=80)
    source_agent: str = Field(..., min_length=1, max_length=120)
    target_agent: Optional[str] = Field(default=None, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = Field(default="tribe.event.v1", max_length=40)


class TribeEventOut(BaseModel):
    accepted: bool
    event_id: str
    ts_ms: int
    event_type: str
    source_agent: str
    target_agent: Optional[str] = None


class TribeRecentResponse(BaseModel):
    total: int
    items: list[dict[str, Any]]
    timestamp: int


@router.post("/tribe_event", response_model=TribeEventOut, status_code=202)
def ingest_tribe_event(
    body: TribeEventIn,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    _validate_internal_token(x_internal_token)
    bus = get_tribe_event_bus()
    event = new_tribe_event(
        event_type=body.event_type,
        source_agent=body.source_agent,
        target_agent=body.target_agent,
        payload=body.payload,
        schema_version=body.schema_version,
    )
    bus.publish(event)
    return TribeEventOut(
        accepted=True,
        event_id=event.event_id,
        ts_ms=event.ts_ms,
        event_type=event.event_type,
        source_agent=event.source_agent,
        target_agent=event.target_agent,
    )


@router.get("/tribe_event/recent", response_model=TribeRecentResponse)
def list_tribe_events(
    limit: int = Query(20, ge=1, le=200),
    event_type: str | None = Query(default=None, max_length=80),
    source_agent: str | None = Query(default=None, max_length=120),
    target_agent: str | None = Query(default=None, max_length=120),
    request_id: str | None = Query(default=None, max_length=120),
    session_id: str | None = Query(default=None, max_length=120),
    conversation_id: str | None = Query(default=None, max_length=120),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    _validate_internal_token(x_internal_token)
    bus = get_tribe_event_bus()
    items = [
        event.to_dict()
        for event in bus.recent(
            limit=limit,
            event_type=event_type,
            source_agent=source_agent,
            target_agent=target_agent,
            request_id=request_id,
            session_id=session_id,
            conversation_id=conversation_id,
        )
    ]
    timestamp = items[0]["ts_ms"] if items else 0
    return TribeRecentResponse(total=len(items), items=items, timestamp=int(timestamp))


# ── T-39：TribeStateRegistry 全量快照诊断端点 ─────────────────────────────────

class TribeStateAllResponse(BaseModel):
    total: int
    namespaces: list[dict[str, Any]]
    ts_ms: int


@router.get("/tribe_state/all", response_model=TribeStateAllResponse)
def list_tribe_state_all(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-39：返回 TribeStateRegistry 中所有命名空间的完整快照。

    供开发调试和运维诊断使用，按 updated_at_ms 降序排列。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        namespaces = registry.get_all_namespaces()
        return TribeStateAllResponse(
            total=len(namespaces),
            namespaces=namespaces,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_ALL_FAILED: {exc}") from exc


# ── T-55：TribeStateRegistry 按族快照诊断端点 ─────────────────────────────────

class TribeStateFamilyResponse(BaseModel):
    family: str
    total: int
    namespaces: list[dict[str, Any]]
    ts_ms: int


@router.get("/tribe_state/family/{family}", response_model=TribeStateFamilyResponse)
def list_tribe_state_by_family(
    family: str,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-55：返回指定族（family）的所有命名空间快照。

    利用 TribeStateRegistry.get_state_by_family() 按族批量查询，
    供开发调试和运维诊断使用，按 updated_at_ms 降序排列。

    示例：
        GET /internal/tribe_state/family/limbic
        GET /internal/tribe_state/family/social_brain
        GET /internal/tribe_state/family/immune
    """
    _validate_internal_token(x_internal_token)
    family_clean = str(family or "").strip().lower()
    if not family_clean:
        raise HTTPException(status_code=400, detail="FAMILY_REQUIRED")
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        namespaces = registry.get_state_by_family(family_clean)
        return TribeStateFamilyResponse(
            family=family_clean,
            total=len(namespaces),
            namespaces=namespaces,
            ts_ms=now_ms(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_FAMILY_FAILED: {exc}") from exc


# ── T-67：TribeEventBus 订阅数量诊断端点 ─────────────────────────────────────

class TribeSubscribersResponse(BaseModel):
    total_event_types: int
    total_handlers: int
    subscribers: dict[str, int]
    ts_ms: int


@router.get("/tribe_event/subscribers", response_model=TribeSubscribersResponse)
def list_tribe_event_subscribers(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-67：返回 TribeEventBus 各事件类型的当前订阅数量。

    供开发调试和运维诊断使用，可快速确认各 Agent 的订阅是否正确注册。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        bus = get_tribe_event_bus()
        subscribers = bus.get_subscriber_count()
        total_handlers = sum(subscribers.values())
        return TribeSubscribersResponse(
            total_event_types=len(subscribers),
            total_handlers=total_handlers,
            subscribers=subscribers,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_SUBSCRIBERS_FAILED: {exc}") from exc


# ── T-73：ImmuneAgent 计数器重置运维端点 ─────────────────────────────────────

class ImmuneResetResponse(BaseModel):
    counters_before_reset: dict[str, Any]
    block_rate_before_reset: float
    reset_at_ms: int


@router.post("/immune/reset", response_model=ImmuneResetResponse)
def post_immune_reset(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-73：重置 ImmuneAgent 计数器，供运维手动清零使用。

    返回重置前的计数器快照，供审计记录。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

        result = get_immune_agent().reset_counters()
        return ImmuneResetResponse(
            counters_before_reset=result["counters_before_reset"],
            block_rate_before_reset=result["block_rate_before_reset"],
            reset_at_ms=result["reset_at_ms"],
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"IMMUNE_RESET_FAILED: {exc}") from exc


# ── T-83：TribeStateRegistry 命名空间列表端点 ─────────────────────────────────

class TribeStateNamespacesResponse(BaseModel):
    total: int
    namespaces: list[str]
    ts_ms: int


@router.get("/tribe_state/namespaces", response_model=TribeStateNamespacesResponse)
def list_tribe_state_namespaces(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-83：返回 TribeStateRegistry 中所有已注册命名空间的名称列表。

    仅返回命名空间名称，不含状态数据，供快速枚举当前活跃 Agent 命名空间使用。
    按字母顺序排列。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        namespaces = registry.list_namespaces()
        return TribeStateNamespacesResponse(
            total=len(namespaces),
            namespaces=namespaces,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_NAMESPACES_FAILED: {exc}") from exc


# ── T-101：TribeStateRegistry 轻量摘要端点 ───────────────────────────────────

class TribeStateSummaryResponse(BaseModel):
    total: int
    summaries: list[dict[str, Any]]
    ts_ms: int


@router.get("/tribe_state/summary", response_model=TribeStateSummaryResponse)
def get_tribe_state_summary(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-101：返回 TribeStateRegistry 所有命名空间的轻量摘要。

    每个命名空间仅返回 namespace/writer/updated_at_ms/state_keys，
    不含完整状态数据，适合快速巡检和监控大盘使用。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        summaries = registry.get_state_summary()
        return TribeStateSummaryResponse(
            total=len(summaries),
            summaries=summaries,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_SUMMARY_FAILED: {exc}") from exc


# ── T-74：TribeStateRegistry 命名空间清除运维端点 ─────────────────────────────

# —— Gemini 单 Key 安全诊断（仅返回状态，不返回密钥明文） ————————————————

class GeminiKeyStatusResponse(BaseModel):
    provider_name: str
    scope: str
    key_configured: bool
    source: str
    runtime_profile_available: bool
    runtime_secret_ref: str | None = None
    runtime_error: str | None = None
    env_key_configured: bool
    ts_ms: int


@router.get("/llm/gemini_key", response_model=GeminiKeyStatusResponse)
def get_gemini_key_status(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.app.model_router import inspect_gemini_key_status  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        snapshot = dict(inspect_gemini_key_status() or {})
        return GeminiKeyStatusResponse(
            provider_name=str(snapshot.get("provider_name") or "gemini"),
            scope=str(snapshot.get("scope") or "host.llm"),
            key_configured=bool(snapshot.get("key_configured", False)),
            source=str(snapshot.get("source") or "empty"),
            runtime_profile_available=bool(snapshot.get("runtime_profile_available", False)),
            runtime_secret_ref=(str(snapshot.get("runtime_secret_ref")) if snapshot.get("runtime_secret_ref") else None),
            runtime_error=(str(snapshot.get("runtime_error")) if snapshot.get("runtime_error") else None),
            env_key_configured=bool(snapshot.get("env_key_configured", False)),
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GEMINI_KEY_STATUS_FAILED: {exc}") from exc


class TribeStateDeleteResponse(BaseModel):
    namespace: str
    deleted: bool
    ts_ms: int


@router.delete("/tribe_state/{namespace}", response_model=TribeStateDeleteResponse)
def delete_tribe_state_namespace(
    namespace: str,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-74：清除 TribeStateRegistry 中指定命名空间的状态快照。

    供运维手动清零单个 Agent 的状态缓存，不影响其他命名空间。
    返回 deleted=True 表示成功清除，deleted=False 表示命名空间不存在。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        registry = get_global_registry()
        deleted = registry.clear_namespace(namespace)
        return TribeStateDeleteResponse(
            namespace=namespace,
            deleted=deleted,
            ts_ms=now_ms(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TRIBE_STATE_DELETE_FAILED: {exc}") from exc


# ── T-131：EdgeCommandPusher 下行指令状态诊断端点 ─────────────────────────────

class EdgeCommandPusherStatusResponse(BaseModel):
    enabled: bool
    edge_base_url: str
    timeout_ms: int
    authority_state: str
    reason_code: str
    ts_ms: int


class EdgeCommandPushRequest(BaseModel):
    command_type: str = Field(..., min_length=1, max_length=80)
    target_agent: str = Field(..., min_length=1, max_length=120)
    source_agent: str = Field(default="host_brain", max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


@router.get("/edge_command/status", response_model=EdgeCommandPusherStatusResponse)
def get_edge_command_pusher_status(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """Return the retired compatibility status without resolving a service."""
    _validate_internal_token(x_internal_token)
    from host_brain.core.ids import now_ms  # noqa: PLC0415

    return EdgeCommandPusherStatusResponse(
        enabled=False,
        edge_base_url="",
        timeout_ms=0,
        authority_state="GOVERNED_TASKGRAPH_ONLY",
        reason_code="ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
        ts_ms=now_ms(),
    )


@router.post("/edge_command/push", status_code=409)
def post_edge_command_push(
    body: EdgeCommandPushRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """Retired direct actuator; callers must use governed operator admission."""
    _validate_internal_token(x_internal_token)
    _ = body
    raise HTTPException(
        status_code=409,
        detail="ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
    )
