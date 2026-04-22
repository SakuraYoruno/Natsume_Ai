"""obs_routes_sysmode.py — 系统运行模式管理（从 obs_routes.py 拆分）。

包含：GET/POST /obs/sys-mode / history / auto-rules / event
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger

logger = logging.getLogger("Natsume.obs")


from typing import Literal
import threading

from .obs_routes_core import router, _get_ledger, _now_ms
from .obs_routes_core import _env_int, _get_obs_route_cached_response


# ─────────────────────────────────────────────────────────────────────────────
# 系统运行模式管理 — GET/POST /obs/sys-mode
# ─────────────────────────────────────────────────────────────────────────────

import threading  # noqa: E402 — sys-mode 区域需要 threading.Lock

# 全局 ModeTransitionService 实例（延迟初始化）
_mode_transition_service = None
_mode_transition_lock = threading.Lock()
_RUNTIME_SNAPSHOT_CACHE_TTL_MS = _env_int("OBS_RUNTIME_SNAPSHOT_CACHE_TTL_MS", 2500, minimum=0)
_RUNTIME_SNAPSHOT_WARM_ENABLED = bool(
    # OPT-4 首批迁移：从 settings.observability 读取
    __import__("host_brain.app.settings.observability", fromlist=["get_observability_settings"])
    .get_observability_settings()
    .obs_runtime_snapshot_warm_enabled
)
_RUNTIME_SNAPSHOT_WARM_INTERVAL_MS = _env_int(
    "HOST_OBS_RUNTIME_SNAPSHOT_WARM_INTERVAL_MS",
    4000,
    minimum=1000,
)
_RUNTIME_SNAPSHOT_WARM_STALE_MS = _env_int(
    "HOST_OBS_RUNTIME_SNAPSHOT_WARM_STALE_MS",
    max(_RUNTIME_SNAPSHOT_WARM_INTERVAL_MS * 3, 12000),
    minimum=2000,
)
_runtime_snapshot_warm_thread: threading.Thread | None = None
_runtime_snapshot_warm_stop = threading.Event()
_runtime_snapshot_warm_lock = threading.Lock()
_runtime_snapshot_warm_state: dict[str, Any] = {
    "value": None,
    "updated_ts_ms": 0,
    "last_duration_ms": 0,
    "last_error": None,
}


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
            from host_brain.app.dependencies import (
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
            from host_brain.app.dependencies import set_sys_mode as _dep_set  # noqa: PLC0415
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
    slot_status: Dict[str, str] = Field(default_factory=dict)
    degraded: bool = False
    degraded_reasons: List[str] = Field(default_factory=list)
    timestamp: int


def _clone_runtime_snapshot_response(
    value: "DigitalLifeRuntimeSnapshotResponse",
) -> "DigitalLifeRuntimeSnapshotResponse":
    if hasattr(value, "model_copy"):
        return value.model_copy(deep=True)
    return value.copy(deep=True)


def _build_runtime_snapshot_response(current_topic: str) -> DigitalLifeRuntimeSnapshotResponse:
    from host_brain.app.http_hooks_runtime_helpers import build_digital_life_runtime_snapshot  # noqa: PLC0415

    service = _get_mode_transition_service()
    payload = build_digital_life_runtime_snapshot(current_topic=current_topic)
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


def _refresh_runtime_snapshot_warm_once() -> None:
    started_at = _now_ms()
    try:
        payload = _build_runtime_snapshot_response("")
    except Exception as exc:
        with _runtime_snapshot_warm_lock:
            _runtime_snapshot_warm_state["last_error"] = str(exc)[:200]
            _runtime_snapshot_warm_state["last_duration_ms"] = max(0, _now_ms() - started_at)
        logger.warning("runtime snapshot warm refresh degraded: %s", exc)
        return

    with _runtime_snapshot_warm_lock:
        _runtime_snapshot_warm_state["value"] = _clone_runtime_snapshot_response(payload)
        _runtime_snapshot_warm_state["updated_ts_ms"] = _now_ms()
        _runtime_snapshot_warm_state["last_duration_ms"] = max(0, _now_ms() - started_at)
        _runtime_snapshot_warm_state["last_error"] = None


def _runtime_snapshot_warm_loop() -> None:
    while not _runtime_snapshot_warm_stop.is_set():
        _refresh_runtime_snapshot_warm_once()
        wait_seconds = max(float(_RUNTIME_SNAPSHOT_WARM_INTERVAL_MS) / 1000.0, 0.25)
        _runtime_snapshot_warm_stop.wait(wait_seconds)


def start_runtime_snapshot_warm_cache() -> dict[str, Any]:
    if not _RUNTIME_SNAPSHOT_WARM_ENABLED:
        return {"enabled": False, "status": "disabled"}

    global _runtime_snapshot_warm_thread
    with _runtime_snapshot_warm_lock:
        if _runtime_snapshot_warm_thread is not None and _runtime_snapshot_warm_thread.is_alive():
            return get_runtime_snapshot_warm_status()
        _runtime_snapshot_warm_stop.clear()
        _runtime_snapshot_warm_thread = threading.Thread(
            target=_runtime_snapshot_warm_loop,
            name="obs-runtime-snapshot-warm",
            daemon=True,
        )
        _runtime_snapshot_warm_thread.start()
    return get_runtime_snapshot_warm_status()


def stop_runtime_snapshot_warm_cache() -> dict[str, Any]:
    global _runtime_snapshot_warm_thread
    _runtime_snapshot_warm_stop.set()
    thread = _runtime_snapshot_warm_thread
    if thread is not None:
        thread.join(timeout=2.0)
    _runtime_snapshot_warm_thread = None
    return get_runtime_snapshot_warm_status()


def get_runtime_snapshot_warm_status() -> dict[str, Any]:
    with _runtime_snapshot_warm_lock:
        updated_ts_ms = int(_runtime_snapshot_warm_state.get("updated_ts_ms") or 0)
        age_ms = max(0, _now_ms() - updated_ts_ms) if updated_ts_ms > 0 else None
        has_value = _runtime_snapshot_warm_state.get("value") is not None
        last_duration_ms = int(_runtime_snapshot_warm_state.get("last_duration_ms") or 0)
        last_error = str(_runtime_snapshot_warm_state.get("last_error") or "").strip() or None
    running = _runtime_snapshot_warm_thread is not None and _runtime_snapshot_warm_thread.is_alive()
    return {
        "enabled": bool(_RUNTIME_SNAPSHOT_WARM_ENABLED),
        "running": bool(running),
        "has_value": bool(has_value),
        "updated_ts_ms": updated_ts_ms or None,
        "age_ms": age_ms,
        "interval_ms": int(_RUNTIME_SNAPSHOT_WARM_INTERVAL_MS),
        "stale_after_ms": int(_RUNTIME_SNAPSHOT_WARM_STALE_MS),
        "last_duration_ms": last_duration_ms,
        "last_error": last_error,
    }


def _get_warmed_runtime_snapshot(current_topic: str) -> DigitalLifeRuntimeSnapshotResponse | None:
    if str(current_topic or "").strip():
        return None
    if not _RUNTIME_SNAPSHOT_WARM_ENABLED:
        return None

    with _runtime_snapshot_warm_lock:
        value = _runtime_snapshot_warm_state.get("value")
        updated_ts_ms = int(_runtime_snapshot_warm_state.get("updated_ts_ms") or 0)

    if value is None or updated_ts_ms <= 0:
        return None
    if max(0, _now_ms() - updated_ts_ms) > int(_RUNTIME_SNAPSHOT_WARM_STALE_MS):
        return None
    return _clone_runtime_snapshot_response(value)


@router.get("/sys-mode", response_model=SysModeStatusResponse)
def get_sys_mode():
    """获取当前系统运行模式 + 自动切换规则状态 + 空闲信息。"""
    service = _get_mode_transition_service()
    status = service.get_current_status()
    return SysModeStatusResponse(**status)


@router.get("/runtime-snapshot", response_model=DigitalLifeRuntimeSnapshotResponse)
def get_digital_life_runtime_snapshot(
    current_topic: str = Query("", max_length=200, description="用于计算 silence/motive/policy 的当前话题提示"),
):
    """获取统一数字生命 runtime snapshot（只读观测面）。"""
    warmed = _get_warmed_runtime_snapshot(current_topic)
    if warmed is not None:
        return warmed

    return _get_obs_route_cached_response(
        cache_name="runtime_snapshot",
        cache_key={"current_topic": str(current_topic or "").strip()},
        ttl_ms=_RUNTIME_SNAPSHOT_CACHE_TTL_MS,
        builder=lambda: _build_runtime_snapshot_response(current_topic),
    )


@router.post("/sys-mode", response_model=SysModeSwitchResponse)
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
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
):
    """获取模式切换历史记录。"""
    service = _get_mode_transition_service()
    history = service.get_history(limit=limit)
    items = [SysModeHistoryItem(**h) for h in history]
    return SysModeHistoryResponse(
        items=items,
        total=len(items),
        timestamp=_now_ms(),
    )


@router.put("/sys-mode/auto-rules")
def update_auto_rules(body: AutoRulesUpdateRequest):
    """更新自动切换规则开关。"""
    service = _get_mode_transition_service()
    updated = service.update_auto_rules(body.rules)
    return {
        "auto_rules": updated,
        "timestamp": _now_ms(),
    }


@router.post("/sys-mode/event")
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

