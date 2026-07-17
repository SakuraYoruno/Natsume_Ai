"""obs_routes_sysmode.py — 系统运行模式管理（从 obs_routes.py 拆分）。

包含：GET/POST /obs/sys-mode / history / auto-rules / event
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.obs_host_proxy import fetch_host_json
from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.observability_runtime.live_source_projection import (
    SYS_MODE_HISTORY_KEY,
    SYS_MODE_HISTORY_KIND,
    SYS_MODE_MAX_AGE_MS,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
    read_operator_live_projection,
)
from host_brain.app.observability_runtime.process_state_projection import (
    RUNTIME_MAX_AGE_MS,
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
    read_process_projection,
)
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts

logger = logging.getLogger("Natsume.obs")


from typing import Literal
import threading

from .obs_routes_core import router, _get_ledger, _now_ms
from .obs_routes_core import _env_int


# ─────────────────────────────────────────────────────────────────────────────
# 系统运行模式管理 — GET/POST /obs/sys-mode
# ─────────────────────────────────────────────────────────────────────────────

import threading  # noqa: E402 — sys-mode 区域需要 threading.Lock

# 全局 ModeTransitionService 实例（延迟初始化）
_mode_transition_service = None
_mode_transition_lock = threading.Lock()
_RUNTIME_SNAPSHOT_CACHE_TTL_MS = _env_int("OBS_RUNTIME_SNAPSHOT_CACHE_TTL_MS", 2500, minimum=0)


def _sync_obs_routes_compat_handles() -> None:
    """保持 obs_routes 兼容导出的句柄与本模块单例同步。"""
    import sys

    obs_routes_mod = sys.modules.get("host_brain.app.obs_routes")
    if obs_routes_mod is None:
        return
    try:
        setattr(obs_routes_mod, "_mode_transition_lock", _mode_transition_lock)
        setattr(obs_routes_mod, "_mode_transition_service", _mode_transition_service)
    except Exception:
        pass


def _get_mode_transition_service():
    """获取或初始化 ModeTransitionService 单例。"""
    global _mode_transition_service
    if _mode_transition_service is not None:
        return _mode_transition_service

    with _mode_transition_lock:
        if _mode_transition_service is not None:
            return _mode_transition_service

        try:
            from host_brain.core.mode_transition import ModeTransitionService
            from host_brain.composition.process_services import (
                get_hard_state as _dep_get_hard_state,
                set_sys_mode as _dep_set_sys_mode,
            )
            ledger = _get_ledger()
            _mode_transition_service = ModeTransitionService(
                get_hard_state=_dep_get_hard_state,
                set_sys_mode=_dep_set_sys_mode,
                ledger=ledger,
            )
        except Exception as exc:
            logger.warning("ModeTransitionService init failed, using fallback: %s", exc)
            # 降级：返回一个最小化的 fallback 服务
            _mode_transition_service = _FallbackModeService()

    _sync_obs_routes_compat_handles()
    return _mode_transition_service


class _FallbackModeService:
    """ModeTransitionService 降级替代——当依赖不可用时提供基本功能。"""

    def get_current_status(self):
        import os
        mode = os.environ.get("HOST_SYS_MODE", "normal").strip().lower()
        return {
            "sys_mode": mode,
            "auto_rules": {
                "gaming_detect": True,
                "gaming_exit": True,
                "idle_to_sleep": True,
                "wakeup_on_msg": True,
                "morning_wakeup": True,
            },
            "idle_ms": 0,
            "idle_minutes": 0,
            "last_interaction_ts_ms": _now_ms(),
            "is_night_time": False,
            "valid_transitions": [],
            "ts_ms": _now_ms(),
            "degraded": True,
            "degraded_reason": "ModeTransitionService not available",
        }

    def switch_mode(self, target_mode, *, source=None, reason=""):
        import os
        from host_brain.core.metabolism import ALLOWED_SYS_MODES
        target = str(target_mode).strip().lower()
        if target not in ALLOWED_SYS_MODES:
            return {"success": False, "error": f"invalid mode: {target}"}
        # 优先通过 set_sys_mode 切换（保持 _hard_state 一致性）
        try:
            from host_brain.composition.process_services import set_sys_mode as _dep_set  # noqa: PLC0415
            result = _dep_set(target)
            return {
                "success": True,
                "from_mode": result.get("from_mode", "unknown"),
                "to_mode": result.get("to_mode", target),
                "source": "manual",
                "reason": reason,
                "ts_ms": _now_ms(),
                "degraded": True,
            }
        except Exception:
            pass
        # 降级：直接写环境变量（_hard_state 可能不一致）
        os.environ["HOST_SYS_MODE"] = target
        return {
            "success": True,
            "from_mode": "unknown",
            "to_mode": target,
            "source": "manual",
            "reason": reason,
            "ts_ms": _now_ms(),
            "degraded": True,
        }

    def get_history(self, limit=20):
        return []

    def update_auto_rules(self, rules):
        return rules

    def on_event(self, event_type, payload=None):
        return None


class SysModeStatusResponse(BaseModel):
    sys_mode: str
    auto_rules: Dict[str, bool]
    idle_ms: int
    idle_minutes: float
    last_interaction_ts_ms: int
    is_night_time: bool
    valid_transitions: List[str]
    ts_ms: int
    degraded: bool = False
    degraded_reason: Optional[str] = None
    projection: Dict[str, Any] = Field(default_factory=dict)


class SysModeSwitchRequest(BaseModel):
    target_mode: Literal["normal", "focus", "gaming", "sleep", "maintenance", "locked"] = Field(
        ..., description="目标模式: normal/focus/gaming/sleep/maintenance/locked"
    )
    reason: str = Field("", description="切换原因说明")
    source: str = Field("manual", description="切换来源: manual/auto_gaming/auto_idle/auto_wakeup/skill")


class SysModeSwitchResponse(BaseModel):
    success: bool
    from_mode: Optional[str] = None
    to_mode: Optional[str] = None
    source: Optional[str] = None
    reason: Optional[str] = None
    event_id: Optional[str] = None
    error: Optional[str] = None
    ts_ms: int


class SysModeHistoryItem(BaseModel):
    event_id: str
    from_mode: str
    to_mode: str
    source: str
    reason: str
    ts_ms: int
    success: bool
    error: Optional[str] = None


class SysModeHistoryResponse(BaseModel):
    items: List[SysModeHistoryItem]
    total: int
    timestamp: int
    projection: Dict[str, Any] = Field(default_factory=dict)


class AutoRulesUpdateRequest(BaseModel):
    rules: Dict[str, bool] = Field(..., description="自动切换规则开关")


class DigitalLifeRuntimeSnapshotResponse(BaseModel):
    persona_user_id: str
    current_topic: str = ""
    sys_mode: str
    sys_mode_status: Dict[str, Any] = Field(default_factory=dict)
    hard_state: Dict[str, Any] = Field(default_factory=dict)
    activity_monitor: Dict[str, Any] = Field(default_factory=dict)
    emotion_context: Dict[str, Any] = Field(default_factory=dict)
    endocrine_state: Dict[str, Any] = Field(default_factory=dict)
    immune_state: Dict[str, Any] = Field(default_factory=dict)
    bond_state: Dict[str, Any] = Field(default_factory=dict)
    bond_summary: Dict[str, Any] = Field(default_factory=dict)
    bond_summary_human: Dict[str, Any] = Field(default_factory=dict)
    inner_world_state: Dict[str, Any] = Field(default_factory=dict)
    offline_state: Dict[str, Any] = Field(default_factory=dict)
    silence_state: Dict[str, Any] = Field(default_factory=dict)
    body_sense: Dict[str, Any] = Field(default_factory=dict)
    self_model_snapshot: Dict[str, Any] = Field(default_factory=dict)
    self_reflection_pulse: Dict[str, Any] = Field(default_factory=dict)
    bond_narrative: Dict[str, Any] = Field(default_factory=dict)
    motive_state: Dict[str, Any] = Field(default_factory=dict)
    render_policy: Dict[str, Any] = Field(default_factory=dict)
    somatic_state: Dict[str, Any] = Field(default_factory=dict)
    peripheral_state: Dict[str, Any] = Field(default_factory=dict)
    circadian_state: Dict[str, Any] = Field(default_factory=dict)
    proprioception_state: Dict[str, Any] = Field(default_factory=dict)
    initiative_decision: Dict[str, Any] = Field(default_factory=dict)
    default_mode_state: Dict[str, Any] = Field(default_factory=dict)
    mirror_neuron_state: Dict[str, Any] = Field(default_factory=dict)
    memory_layers: Dict[str, Any] = Field(default_factory=dict)
    language_dual_system: Dict[str, Any] = Field(default_factory=dict)
    homeostasis_report: Dict[str, Any] = Field(default_factory=dict)
    interoception_state: Dict[str, Any] = Field(default_factory=dict)
    motor_cortex_state: Dict[str, Any] = Field(default_factory=dict)
    cerebellum_state: Dict[str, Any] = Field(default_factory=dict)
    glymphatic_state: Dict[str, Any] = Field(default_factory=dict)
    vestibular_state: Dict[str, Any] = Field(default_factory=dict)
    runtime_summary: Dict[str, Any] = Field(default_factory=dict)
    background_runtime: Dict[str, Any] = Field(default_factory=dict)
    # Projection serialization can add a nested ``_operator_privacy`` envelope
    # when a slot name is content-shaped (for example ``bond_narrative``).
    # The read model must preserve that fail-closed metadata alongside the
    # ordinary string statuses.
    slot_status: Dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    degraded_reasons: List[str] = Field(default_factory=list)
    timestamp: int
    projection: Dict[str, Any] = Field(default_factory=dict)


def _build_runtime_snapshot_response(
    current_topic: str,
    *,
    lifecycle_ports: LifecycleRuntimeApplicationPorts,
) -> DigitalLifeRuntimeSnapshotResponse:
    from host_brain.lifecycle_runtime.background_projection import _build_background_runtime_snapshot  # noqa: PLC0415
    from host_brain.lifecycle_runtime.runtime_snapshot import build_digital_life_runtime_snapshot  # noqa: PLC0415

    service = _get_mode_transition_service()
    payload = build_digital_life_runtime_snapshot(
        ports=lifecycle_ports,
        current_topic=current_topic,
    )
    host_scheduler_snapshot = fetch_host_json("/scheduler/health", timeout_s=1.5)
    if host_scheduler_snapshot is not None:
        try:
            payload["background_runtime"] = _build_background_runtime_snapshot(
                now_ms=int(payload.get("timestamp") or _now_ms()),
                persona_user_id=str(payload.get("persona_user_id") or "user_1"),
                inner_world_state=dict(payload.get("inner_world_state") or {}),
                scheduler_snapshot_override=host_scheduler_snapshot,
            )
        except Exception as exc:
            payload.setdefault("background_runtime", {})
            payload["background_runtime"]["host_scheduler_overlay_error"] = str(exc)[:160]
    try:
        payload["sys_mode_status"] = dict(service.get_current_status() or {})
    except Exception as exc:
        payload.setdefault("sys_mode_status", {})
        payload["degraded"] = True
        degraded_reasons = list(payload.get("degraded_reasons") or [])
        degraded_reasons.append(f"sys_mode_status:degraded ({str(exc)[:120]})")
        payload["degraded_reasons"] = degraded_reasons
        slot_status = dict(payload.get("slot_status") or {})
        slot_status["sys_mode_status"] = "degraded"
        payload["slot_status"] = slot_status
    else:
        slot_status = dict(payload.get("slot_status") or {})
        slot_status["sys_mode_status"] = "live"
        payload["slot_status"] = slot_status
    return DigitalLifeRuntimeSnapshotResponse(**payload)


@router.get("/sys-mode", response_model=SysModeStatusResponse)
def get_sys_mode(request: Request):
    """Read mode state without constructing the writer-capable service."""
    store = require_obs_query_projection_store(request)
    status, projection = read_operator_live_projection(
        store,
        SYS_MODE_STATUS_KIND,
        SYS_MODE_STATUS_KEY,
        max_age_ms=SYS_MODE_MAX_AGE_MS,
    )
    if status is None:
        status = {
            "sys_mode": "unknown",
            "auto_rules": {},
            "idle_ms": 0,
            "idle_minutes": 0.0,
            "last_interaction_ts_ms": 0,
            "is_night_time": False,
            "valid_transitions": [],
            "ts_ms": _now_ms(),
            "degraded": True,
            "degraded_reason": projection["reason_code"],
        }
    return SysModeStatusResponse(**status, projection=projection)


@router.get("/runtime-snapshot", response_model=DigitalLifeRuntimeSnapshotResponse)
def get_digital_life_runtime_snapshot(
    request: Request,
    current_topic: str = Query("", max_length=200, description="用于计算 silence/motive/policy 的当前话题提示"),
):
    """获取统一数字生命 runtime snapshot（只读观测面）。"""
    payload, projection = read_process_projection(
        require_obs_query_projection_store(request),
        RUNTIME_SNAPSHOT_KIND,
        RUNTIME_SNAPSHOT_KEY,
        max_age_ms=RUNTIME_MAX_AGE_MS,
    )
    if payload is None:
        payload = {
            "persona_user_id": "user_1",
            "current_topic": "",
            "sys_mode": "unknown",
            "degraded": True,
            "degraded_reasons": [projection["reason_code"]],
            "timestamp": _now_ms(),
        }
    payload = {**payload, "projection": projection}
    return DigitalLifeRuntimeSnapshotResponse(**payload)


def switch_sys_mode(body: SysModeSwitchRequest):
    """切换系统运行模式（手动 / API 触发）。"""
    service = _get_mode_transition_service()

    # 映射 source 字符串到枚举
    try:
        from host_brain.core.mode_transition import TransitionSource
        source_enum = TransitionSource(body.source)
    except (ValueError, ImportError):
        source_enum = None

    if source_enum:
        result = service.switch_mode(
            body.target_mode,
            source=source_enum,
            reason=body.reason,
        )
    else:
        result = service.switch_mode(
            body.target_mode,
            reason=body.reason,
        )

    return SysModeSwitchResponse(
        success=result.get("success", False),
        from_mode=result.get("from_mode"),
        to_mode=result.get("to_mode"),
        source=result.get("source"),
        reason=result.get("reason"),
        event_id=result.get("event_id"),
        error=result.get("error"),
        ts_ms=result.get("ts_ms", _now_ms()),
    )


@router.get("/sys-mode/history", response_model=SysModeHistoryResponse)
def get_sys_mode_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
):
    """Read projected mode transitions; slicing is request-local and pure."""
    store = require_obs_query_projection_store(request)
    payload, projection = read_operator_live_projection(
        store,
        SYS_MODE_HISTORY_KIND,
        SYS_MODE_HISTORY_KEY,
        max_age_ms=SYS_MODE_MAX_AGE_MS,
    )
    history = list((payload or {}).get("items") or [])[:limit]
    items = [SysModeHistoryItem(**h) for h in history]
    return SysModeHistoryResponse(
        items=items,
        total=len(items),
        timestamp=int((payload or {}).get("timestamp") or _now_ms()),
        projection=projection,
    )


def update_auto_rules(body: AutoRulesUpdateRequest):
    """更新自动切换规则开关。"""
    service = _get_mode_transition_service()
    updated = service.update_auto_rules(body.rules)
    return {
        "auto_rules": updated,
        "timestamp": _now_ms(),
    }


def post_sys_mode_event(
    event_type: str = Query(..., description="事件类型"),
):
    """发送模式切换事件（供 Edge 端调用）。

    支持的 event_type：
    - gaming_process_detected: 检测到游戏进程
    - gaming_process_exited: 游戏进程退出
    - user_message_received: 收到用户消息
    - idle_check: 空闲检查
    - morning_check: 早间唤醒检查
    """
    service = _get_mode_transition_service()
    result = service.on_event(event_type)
    if result is None:
        return {
            "triggered": False,
            "event_type": event_type,
            "timestamp": _now_ms(),
        }
    return {
        "triggered": True,
        "event_type": event_type,
        "result": result,
        "timestamp": _now_ms(),
    }
