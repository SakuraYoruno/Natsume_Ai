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


# ─────────────────────────────────────────────────────────────────────────────
# 系统运行模式管理 — GET/POST /obs/sys-mode
# ─────────────────────────────────────────────────────────────────────────────

import threading  # noqa: E402 — sys-mode 区域需要 threading.Lock

# 全局 ModeTransitionService 实例（延迟初始化）
_mode_transition_service = None
_mode_transition_lock = threading.Lock()


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
            ledger = _get_ledger_instance()
            _mode_transition_service = ModeTransitionService(
                get_hard_state=_dep_get_hard_state,
                set_sys_mode=_dep_set_sys_mode,
                ledger=ledger,
            )
        except Exception as exc:
            logger.warning("ModeTransitionService init failed, using fallback: %s", exc)
            # 降级：返回一个最小化的 fallback 服务
            _mode_transition_service = _FallbackModeService()

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


@router.get("/sys-mode", response_model=SysModeStatusResponse)
async def get_sys_mode():
    """获取当前系统运行模式 + 自动切换规则状态 + 空闲信息。"""
    service = _get_mode_transition_service()
    status = service.get_current_status()
    return SysModeStatusResponse(**status)


@router.post("/sys-mode", response_model=SysModeSwitchResponse)
async def switch_sys_mode(body: SysModeSwitchRequest):
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
async def get_sys_mode_history(
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
async def update_auto_rules(body: AutoRulesUpdateRequest):
    """更新自动切换规则开关。"""
    service = _get_mode_transition_service()
    updated = service.update_auto_rules(body.rules)
    return {
        "auto_rules": updated,
        "timestamp": _now_ms(),
    }


@router.post("/sys-mode/event")
async def post_sys_mode_event(
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
