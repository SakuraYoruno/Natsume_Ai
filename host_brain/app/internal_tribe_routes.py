from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from host_brain.tribe.event_bus import InMemoryTribeEventBus, new_tribe_event

router = APIRouter(prefix="/internal", tags=["internal-tribe"])

_event_bus: Optional[InMemoryTribeEventBus] = None


def get_tribe_event_bus() -> InMemoryTribeEventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = InMemoryTribeEventBus(max_events=2048)
    return _event_bus


def set_tribe_event_bus(bus: InMemoryTribeEventBus | None) -> None:
    global _event_bus
    _event_bus = bus


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
async def ingest_tribe_event(
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
async def list_tribe_events(
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
async def list_tribe_state_all(
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
async def list_tribe_state_by_family(
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
async def list_tribe_event_subscribers(
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
async def post_immune_reset(
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
async def list_tribe_state_namespaces(
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
async def get_tribe_state_summary(
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

class TribeStateDeleteResponse(BaseModel):
    namespace: str
    deleted: bool
    ts_ms: int


@router.delete("/tribe_state/{namespace}", response_model=TribeStateDeleteResponse)
async def delete_tribe_state_namespace(
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
    ts_ms: int


class EdgeCommandPushRequest(BaseModel):
    command_type: str = Field(..., min_length=1, max_length=80)
    target_agent: str = Field(..., min_length=1, max_length=120)
    source_agent: str = Field(default="host_brain", max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class EdgeCommandPushResponse(BaseModel):
    ok: bool
    command_id: Optional[str] = None
    ts_ms: int
    reason: Optional[str] = None
    status_code: Optional[int] = None


@router.get("/edge_command/status", response_model=EdgeCommandPusherStatusResponse)
async def get_edge_command_pusher_status(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-131：返回 EdgeCommandPusher 当前状态（是否启用、目标地址、超时配置）。

    供开发调试和运维诊断使用，确认下行指令链路是否正常初始化。
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.edge_command_pusher import get_edge_command_pusher  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415

        pusher = get_edge_command_pusher()
        if pusher is None:
            raise HTTPException(status_code=503, detail="EDGE_COMMAND_PUSHER_NOT_INITIALIZED")
        return EdgeCommandPusherStatusResponse(
            enabled=pusher.enabled,
            edge_base_url=pusher.edge_base_url,
            timeout_ms=pusher.timeout_ms,
            ts_ms=now_ms(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"EDGE_COMMAND_STATUS_FAILED: {exc}") from exc


@router.post("/edge_command/push", response_model=EdgeCommandPushResponse, status_code=202)
async def post_edge_command_push(
    body: EdgeCommandPushRequest,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
):
    """T-131：手动向 edge_client 推送下行指令（运维/调试用途）。

    通过 EdgeCommandPusher 向 edge_client /internal/tribe_command 发送指令，
    记录推送结果日志，供运维验证下行链路是否畅通。

    示例请求体：
        {"command_type": "emotion_state_command", "target_agent": "edge.somatic",
         "payload": {"emotion_tag": "JOY", "intensity": 0.8}}
        {"command_type": "gate_control_command", "target_agent": "edge.channel",
         "payload": {"gate_open": true, "reason": "manual_test"}}
    """
    _validate_internal_token(x_internal_token)
    try:
        from host_brain.tribe.edge_command_pusher import get_edge_command_pusher  # noqa: PLC0415
        from host_brain.core.ids import now_ms  # noqa: PLC0415
        import logging  # noqa: PLC0415

        _logger = logging.getLogger("Natsume.tribe.internal_tribe_routes")
        pusher = get_edge_command_pusher()
        if pusher is None:
            raise HTTPException(status_code=503, detail="EDGE_COMMAND_PUSHER_NOT_INITIALIZED")

        result = pusher.push_command(
            command_type=body.command_type,
            target_agent=body.target_agent,
            source_agent=body.source_agent,
            payload=body.payload,
        )
        _logger.info(
            "手动下行指令推送: command_type=%s target=%s ok=%s",
            body.command_type,
            body.target_agent,
            result.get("ok"),
        )
        return EdgeCommandPushResponse(
            ok=bool(result.get("ok", False)),
            command_id=result.get("command_id"),
            ts_ms=int(result.get("ts_ms") or now_ms()),
            reason=result.get("reason"),
            status_code=result.get("status_code"),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"EDGE_COMMAND_PUSH_FAILED: {exc}") from exc
