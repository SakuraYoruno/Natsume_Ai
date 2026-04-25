"""Bond 阶段 REST API 路由（M13-A）。

DevSpec §25：Bond 阶段自动晋级 REST API。
端点：
  GET  /bond/stage/{user_id}           — 查询用户当前 Bond 阶段与积分状态
  GET  /bond/summary/{user_id}         — 获取 context-pack-safe Bond Summary（不含数值）
  POST /bond/event                     — 提交 Bond 事件（触发积分计算与晋级判断）
  GET  /bond/users                     — 列出所有有 Bond 状态的用户
  GET  /bond/health                    — BondStageEngine 健康状态
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from host_brain.bond.bond_stage_engine import (
    STAGE_ORDER,
    BondStageEngine,
    StageTransitionResult,
    TRANSITION_STATUS_PROMOTED,
    TRANSITION_STATUS_DEMOTED,
    TRANSITION_STATUS_STABLE,
)
from host_brain.bond.user_identity import collapse_bond_user_ids, resolve_bond_user_id
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.bond_routes")
_EVIDENCE_REFS_REQUIRED_DETAIL = "evidence_refs 不能为空（FR-BOND-002）"

# ---------------------------------------------------------------------------
# 路由实例
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/bond", tags=["bond"])

# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------
_engine_provider: Optional[Callable[[], BondStageEngine]] = None


def set_bond_engine_provider(provider: Optional[Callable[[], BondStageEngine]]) -> None:
    """由 router registry 注入 BondStageEngine 获取回调。"""
    global _engine_provider
    _engine_provider = provider


def _get_engine() -> BondStageEngine:
    if _engine_provider is not None:
        return _engine_provider()
    raise RuntimeError("BondStageEngine provider not configured")


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class BondStageResponse(BaseModel):
    """GET /bond/stage/{user_id} 响应体。"""
    user_id: str
    current_stage: str = Field(..., description="当前 Bond 阶段")
    stage_index: int = Field(..., description="阶段序号（0=STRANGER）")
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
    """POST /bond/event 请求体。"""
    user_id: str = Field(..., description="用户 ID")
    update_kind: str = Field(..., description="事件类型: INTERACTION/DEEP_TALK/EMOTIONAL_SUPPORT/...")
    evidence_refs: List[str] = Field(default_factory=list, description="证据引用列表")
    request_id: Optional[str] = Field(default=None, description="关联请求 ID（可选）")
    # 修复审计问题 #9：添加情感参数，支持外部提交 Bond 事件时传入情感信息
    emotion_tag: Optional[str] = Field(default=None, description="情感标签（可选，如 JOY/SADNESS/NEUTRAL）")
    emotion_intensity: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="情感强度（可选，0.0-1.0，默认 0.5）")


class BondEventResponse(BaseModel):
    """POST /bond/event 响应体。"""
    user_id: str
    from_stage: str
    to_stage: str
    transition_status: str = Field(..., description="PROMOTED/DEMOTED/STABLE")
    score_delta: int
    total_score: int
    transition_id: str
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


# ---------------------------------------------------------------------------
# GET /bond/health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=BondHealthResponse)
async def bond_health() -> BondHealthResponse:
    """BondStageEngine 健康状态检查。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine()
        users = collapse_bond_user_ids(engine.list_users())
        return BondHealthResponse(
            status="OK",
            user_count=len(users),
            stage_order=STAGE_ORDER,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except RuntimeError as exc:
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

@router.get("/users", response_model=Dict[str, Any])
async def list_bond_users() -> Dict[str, Any]:
    """列出所有有 Bond 状态的用户 ID。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine()
        users = collapse_bond_user_ids(engine.list_users())
        return {
            "users": users,
            "total": len(users),
            "request_id": request_id,
            "timestamp": ts_ms,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /bond/summary/{user_id}
# ---------------------------------------------------------------------------

@router.get("/summary/{user_id}", response_model=BondSummaryResponse)
async def get_bond_summary(user_id: str) -> BondSummaryResponse:
    """获取 context-pack-safe Bond Summary（不含数值，供语义内核消费）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine()
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
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _logger.error("get_bond_summary 接口异常: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# GET /bond/stage/{user_id}
# ---------------------------------------------------------------------------

@router.get("/stage/{user_id}", response_model=BondStageResponse)
async def get_bond_stage(user_id: str) -> BondStageResponse:
    """查询用户当前 Bond 阶段与积分状态（含数值，仅供管理/调试使用）。"""
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        engine = _get_engine()
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
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _logger.error("get_bond_stage 接口异常: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# POST /bond/event
# ---------------------------------------------------------------------------

@router.post("/event", response_model=BondEventResponse, status_code=200)
async def submit_bond_event(body: BondEventRequest) -> BondEventResponse:
    """提交 Bond 事件，触发积分计算与阶段晋级判断。

    - evidence_refs 不能为空（FR-BOND-002）
    - update_kind 决定积分增量
    - 晋级/降级写入审计链
    """
    request_id = body.request_id or new_request_id()
    ts_ms = int(time.time() * 1000)

    # FR-BOND-002：evidence_refs 不能为空
    refs = [r.strip() for r in (body.evidence_refs or []) if r.strip()]
    if not refs:
        raise HTTPException(
            status_code=422,
            detail=_EVIDENCE_REFS_REQUIRED_DETAIL,
            headers={"X-Reason-Code": "BOND_EVIDENCE_REQUIRED"},
        )

    try:
        engine = _get_engine()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        result: StageTransitionResult = engine.apply_event(
            user_id=resolve_bond_user_id(body.user_id),
            update_kind=body.update_kind,
            evidence_refs=refs,
            request_id=request_id,
            ts_ms=ts_ms,
            # 修复审计问题 #9：使用请求体中的情感参数，默认 0.5
            emotion_intensity=float(body.emotion_intensity if body.emotion_intensity is not None else 0.5),
        )
        return BondEventResponse(
            user_id=result.user_id,
            from_stage=result.from_stage,
            to_stage=result.to_stage,
            transition_status=result.transition_status,
            score_delta=result.score_delta,
            total_score=result.total_score,
            transition_id=result.transition_id,
            detail=result.detail,
            request_id=request_id,
            timestamp=ts_ms,
        )
    except Exception as exc:
        _logger.error("submit_bond_event 接口异常: %s", exc, extra={"request_id": request_id})
        raise HTTPException(status_code=500, detail=str(exc))
