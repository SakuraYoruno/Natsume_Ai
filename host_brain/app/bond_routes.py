"""Bond 阶段 REST API 路由（M13-A）。

DevSpec §25：Bond 阶段自动晋级 REST API。
端点：
  GET  /bond/stage/{user_id}           — 查询迁移窗口内的历史 Bond 投影
  GET  /bond/summary/{user_id}         — 获取历史 Bond 摘要（不含数值）
  POST /bond/event                     — 已退役：拒绝旧积分写入
  GET  /bond/users                     — 列出历史 Bond 投影用户
  GET  /bond/health                    — BondStageEngine 健康状态
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from host_brain.bond.bond_stage_engine import (
    STAGE_ORDER,
    BondStageEngine,
)
from host_brain.bond.user_identity import collapse_bond_user_ids, resolve_bond_user_id
from host_brain.app.host_route_ports import (
    HostRoutePorts,
    bind_host_route_endpoint,
)
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.bond_routes")

_LEGACY_BOND_READ_RETIRED = "LEGACY_BOND_READ_RETIRED"

def _get_engine(route_ports: HostRoutePorts) -> BondStageEngine:
    provider = route_ports.bond_engine_provider
    engine = provider() if provider is not None else None
    if engine is None:
        raise RuntimeError("BondStageEngine provider not configured")
    if str(getattr(engine, "reason_code", "")).strip() == _LEGACY_BOND_READ_RETIRED:
        raise RuntimeError(_LEGACY_BOND_READ_RETIRED)
    return engine


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class BondStageResponse(BaseModel):
    """GET /bond/stage/{user_id} 响应体。"""
    user_id: str
    current_stage: str = Field(..., description="当前 Bond 阶段")
    stage_index: int = Field(..., description="阶段序号（0=FRIEND）")
    total_score: int = Field(..., description="累计积分")
    event_count: int = Field(..., description="事件总数")
    trend: str = Field(..., description="趋势: RISING/STABLE/DECLINING")
    capability_hint: str = Field(..., description="能力提示（不含数值）")
    last_event_ts_ms: int = Field(..., description="最近事件时间戳（毫秒）")
    request_id: str
    timestamp: int


class BondSummaryResponse(BaseModel):
    """GET /bond/summary/{user_id} 响应体（context-pack-safe，不含数值）。"""
    user_id: str
    stage: str
    trend: str
    capability_hint: str
    request_id: str
    timestamp: int


class BondEventRequest(BaseModel):
    """已退役 ``POST /bond/event`` 的兼容请求体。

    保留该形状仅为让旧客户端获得稳定、可诊断的 410 响应；字段绝不再
    驱动 Bond 积分、阶段或任何七海关系状态。
    """
    user_id: str = Field(..., description="用户 ID")
    update_kind: str = Field(..., description="事件类型: INTERACTION/DEEP_TALK/EMOTIONAL_SUPPORT/...")
    evidence_refs: List[str] = Field(default_factory=list, description="证据引用列表")
    request_id: Optional[str] = Field(default=None, description="关联请求 ID（可选）")
    emotion_tag: Optional[str] = Field(default=None, description="旧客户端兼容字段；不会写入自我情感")
    emotion_intensity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="旧客户端兼容字段；不会写入关系状态")


class RetiredBondWriteResponse(BaseModel):
    """旧 Bond 写入被拒绝时的稳定兼容响应。"""

    status: str = Field(default="RETIRED")
    reason_code: str = Field(default="LEGACY_BOND_WRITE_RETIRED")
    detail: str
    request_id: str
    timestamp: int


class RetiredBondReadResponse(BaseModel):
    """Stable response for historical Bond GET routes in canonical Hosts."""

    status: str = Field(default="RETIRED")
    reason_code: str = Field(default=_LEGACY_BOND_READ_RETIRED)
    detail: str
    request_id: str
    timestamp: int


class BondHealthResponse(BaseModel):
    """GET /bond/health 响应体。"""
    status: str
    user_count: int
    stage_order: List[str]
    request_id: str
    timestamp: int


def _retired_bond_read_response(*, request_id: str, ts_ms: int) -> JSONResponse:
    """Reject a GET before a legacy engine can create an INIT ledger row."""

    return JSONResponse(
        status_code=410,
        content=RetiredBondReadResponse(
            detail=(
                "Legacy Bond read projections are retired in a canonical "
                "Digital Life Host; use scope-bound Digital Life projections."
            ),
            request_id=request_id,
            timestamp=ts_ms,
        ).model_dump(),
        headers={"X-Reason-Code": _LEGACY_BOND_READ_RETIRED},
    )


# ---------------------------------------------------------------------------
# GET /bond/health
# ---------------------------------------------------------------------------

async def bond_health(
    *,
    _route_ports: HostRoutePorts,
) -> BondHealthResponse | JSONResponse:
    """BondStageEngine 健康状态检查。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine(_route_ports)
        users = collapse_bond_user_ids(engine.list_users())
        return BondHealthResponse(
            status="OK",
            user_count=len(users),
            stage_order=STAGE_ORDER,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except RuntimeError as exc:
        if str(exc) == _LEGACY_BOND_READ_RETIRED:
            return _retired_bond_read_response(request_id=request_id, ts_ms=ts_ms)
        return BondHealthResponse(
            status="DEGRADED",
            user_count=0,
            stage_order=STAGE_ORDER,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("bond_health 接口异常: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /bond/users
# ---------------------------------------------------------------------------

async def list_bond_users(
    *,
    _route_ports: HostRoutePorts,
) -> Dict[str, Any] | JSONResponse:
    """列出所有有 Bond 状态的用户 ID。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine(_route_ports)
        users = collapse_bond_user_ids(engine.list_users())
        return {
            "users": users,
            "total": len(users),
            "request_id": request_id,
            "timestamp": ts_ms,
        }
    except RuntimeError as exc:
        if str(exc) == _LEGACY_BOND_READ_RETIRED:
            return _retired_bond_read_response(request_id=request_id, ts_ms=ts_ms)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /bond/summary/{user_id}
# ---------------------------------------------------------------------------

async def get_bond_summary(
    user_id: str,
    *,
    _route_ports: HostRoutePorts,
) -> BondSummaryResponse | JSONResponse:
    """获取 context-pack-safe Bond Summary（不含数值，供语义内核消费）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine(_route_ports)
        canonical_user_id = resolve_bond_user_id(user_id)
        summary = engine.get_summary(canonical_user_id)
        return BondSummaryResponse(
            user_id=canonical_user_id,
            stage=summary["stage"],
            trend=summary["trend"],
            capability_hint=summary["capability_hint"],
            request_id=request_id,
            timestamp=ts_ms,
        )
    except RuntimeError as exc:
        if str(exc) == _LEGACY_BOND_READ_RETIRED:
            return _retired_bond_read_response(request_id=request_id, ts_ms=ts_ms)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _logger.error("get_bond_summary 接口异常: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /bond/stage/{user_id}
# ---------------------------------------------------------------------------

async def get_bond_stage(
    user_id: str,
    *,
    _route_ports: HostRoutePorts,
) -> BondStageResponse | JSONResponse:
    """查询用户当前 Bond 阶段与积分状态（含数值，仅供管理/调试使用）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine(_route_ports)
        canonical_user_id = resolve_bond_user_id(user_id)
        state = engine.get_state(canonical_user_id)
        stage_index = STAGE_ORDER.index(state.current_stage) if state.current_stage in STAGE_ORDER else 0
        return BondStageResponse(
            user_id=canonical_user_id,
            current_stage=state.current_stage,
            stage_index=stage_index,
            total_score=state.total_score,
            event_count=state.event_count,
            trend=state.trend(),
            capability_hint=state.capability_hint(),
            last_event_ts_ms=state.last_event_ts_ms,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except RuntimeError as exc:
        if str(exc) == _LEGACY_BOND_READ_RETIRED:
            return _retired_bond_read_response(request_id=request_id, ts_ms=ts_ms)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _logger.error("get_bond_stage 接口异常: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /bond/event
# ---------------------------------------------------------------------------

async def submit_bond_event(
    body: BondEventRequest,
    response: Response,
    *,
    _route_ports: HostRoutePorts,
) -> RetiredBondWriteResponse:
    """拒绝旧 Bond 分数写入，避免它重新成为关系 authority。

    ``_route_ports`` 有意不被读取：即使某个测试或旧装配仍携带 writer，
    该 HTTP 路线也不能解析、调用或间接激活它。真实关系变化只可由
    canonical Digital Life relationship episode/aftereffect 生命周期产生。
    """
    request_id = body.request_id or new_request_id()
    ts_ms = int(time.time() * 1000)
    response.headers["X-Reason-Code"] = "LEGACY_BOND_WRITE_RETIRED"
    return RetiredBondWriteResponse(
        detail=(
            "Legacy Bond score writes are retired; relationship changes are "
            "owned by canonical Digital Life lifecycle."
        ),
        request_id=request_id,
        timestamp=ts_ms,
    )


def create_bond_router(route_ports: HostRoutePorts) -> APIRouter:
    """Create Bond routes with app-local engine and writer providers."""

    router = APIRouter(prefix="/bond", tags=["bond"])

    def add(path: str, endpoint: Any, *, methods: list[str], **kwargs: Any) -> None:
        router.add_api_route(
            path,
            bind_host_route_endpoint(endpoint, route_ports),
            methods=methods,
            **kwargs,
        )

    read_retired_response = {410: {"model": RetiredBondReadResponse}}
    add(
        "/health",
        bond_health,
        methods=["GET"],
        response_model=BondHealthResponse,
        responses=read_retired_response,
    )
    add(
        "/users",
        list_bond_users,
        methods=["GET"],
        response_model=Dict[str, Any],
        responses=read_retired_response,
    )
    add(
        "/summary/{user_id}",
        get_bond_summary,
        methods=["GET"],
        response_model=BondSummaryResponse,
        responses=read_retired_response,
    )
    add(
        "/stage/{user_id}",
        get_bond_stage,
        methods=["GET"],
        response_model=BondStageResponse,
        responses=read_retired_response,
    )
    add(
        "/event",
        submit_bond_event,
        methods=["POST"],
        response_model=RetiredBondWriteResponse,
        status_code=410,
    )
    return router


__all__ = [
    "BondEventRequest",
    "RetiredBondReadResponse",
    "RetiredBondWriteResponse",
    "create_bond_router",
    "submit_bond_event",
]
