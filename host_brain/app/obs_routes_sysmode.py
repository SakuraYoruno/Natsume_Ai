"""obs_routes_sysmode.py — 系统运行模式管理（从 obs_routes.py 拆分）。

只包含系统模式只读投影；全部写命令由 Host Secure Operator ingress
进入 Governed TaskGraph。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Query, Request
from pydantic import BaseModel, Field

from host_brain.app.obs_host_proxy import fetch_host_json
from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.observability_runtime.live_source_projection import (
    SYS_MODE_HISTORY_KEY,
    SYS_MODE_HISTORY_KIND,
    SYS_MODE_MAX_AGE_MS,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
)
from host_brain.app.observability_runtime.process_state_projection import (
    RUNTIME_MAX_AGE_MS,
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
    read_process_projection,
)
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts

from .obs_routes_core import router, _now_ms


# ─────────────────────────────────────────────────────────────────────────────
# 系统运行模式管理 — GET/POST /obs/sys-mode
# ─────────────────────────────────────────────────────────────────────────────

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


class DigitalLifeRuntimeSnapshotResponse(BaseModel):
    self_id: str = "natsume_ai"
    relationship_subject_id: str = ""
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
    mode_transition_service: Any | None = None,
) -> DigitalLifeRuntimeSnapshotResponse:
    from host_brain.lifecycle_runtime.background_projection import _build_background_runtime_snapshot  # noqa: PLC0415
    from host_brain.lifecycle_runtime.runtime_snapshot import build_digital_life_runtime_snapshot  # noqa: PLC0415

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
        if mode_transition_service is None:
            raise RuntimeError("MODE_TRANSITION_SERVICE_NOT_INJECTED")
        payload["sys_mode_status"] = dict(
            mode_transition_service.get_current_status() or {}
        )
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
    status, projection = read_process_projection(
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
    current_topic: str = Query(
        "",
        max_length=200,
        deprecated=True,
        description=(
            "已弃用；接口只返回后台预制的 durable snapshot，"
            "该参数不会触发重算或改变持久投影。"
        ),
    ),
):
    """获取预制的数字生命 runtime snapshot；忽略已弃用的话题参数。"""
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


@router.get("/sys-mode/history", response_model=SysModeHistoryResponse)
def get_sys_mode_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
):
    """Read projected mode transitions; slicing is request-local and pure."""
    store = require_obs_query_projection_store(request)
    payload, projection = read_process_projection(
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
