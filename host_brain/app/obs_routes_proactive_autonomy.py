"""C2-6 主动自主性可观测路由。

把数字生命主动交互的内部信号——内在天气、主动就绪度、意图项目、落地校准、对话内心态闭环——
聚合成一个可观测快照，供 Monitor UI / API 检视"她为什么找你、找得好不好"。纯读，不改行为。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_core import _now_ms
from host_brain.app.observability_runtime.live_source_projection import (
    PROACTIVE_AUTONOMY_KIND,
    PROACTIVE_AUTONOMY_MAX_AGE_MS,
)
from host_brain.app.observability_runtime.process_state_projection import (
    read_process_projection,
)

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])


def _compact_project(project: Any) -> dict[str, Any]:
    p = dict(project) if isinstance(project, dict) else {}
    return {
        "project_id": str(p.get("project_id") or ""),
        "title": str(p.get("title") or p.get("theme") or ""),
        "origin": str(p.get("origin") or "default"),
        "progress": p.get("progress"),
        "next_step": str(p.get("next_step") or "")[:120],
    }


def build_proactive_autonomy_snapshot(
    ledger: Any,
    person_id: str = "default",
    *,
    life_snapshot_reader: Any,
) -> dict[str, Any]:
    """聚合主动自主性快照（纯读 life-state，失败优雅降级）。"""
    now = _now_ms()
    resolved = str(person_id or "default")
    snapshot: dict[str, Any] = {
        "person_id": resolved,
        "available": False,
        "inner_weather": {},
        "reach_out_calibration": {},
        "active_projects": [],
        "proactive_readiness": None,
        "top_intention": None,
        "dialogue_closure": {},
        "timestamp": now,
    }
    try:
        from host_brain.persona.emotional_proactive import (
            _project_nudge_pull,
            compute_proactive_readiness,
        )
        from host_brain.digital_life.application_boundary import read_life_snapshot
        from host_brain.digital_life.policy import resolve_reach_out_calibration

        # ``ledger`` remains for the public compatibility seam; the injected
        # reader owns canonical snapshot selection and never initializes state.
        _ = ledger
        life_snapshot = read_life_snapshot(
            resolved,
            reader=life_snapshot_reader,
        )
        state = dict(life_snapshot.get("state") or {})
        if int(life_snapshot.get("version") or 0) <= 0:
            return snapshot
        weather = dict(state.get("inner_weather") or {}) if isinstance(state.get("inner_weather"), dict) else {}
        growth = dict(state.get("self_growth") or {}) if isinstance(state.get("self_growth"), dict) else {}
        active_projects = list(growth.get("active_projects") or state.get("self_projects") or [])
        calibration = resolve_reach_out_calibration(state)
        readiness = compute_proactive_readiness(
            reach_out_bias=float(weather.get("reach_out_bias") or 0.0),
            active_projects=active_projects,
            anticipation=0.0,
            now_ms=now,
            calibration=calibration,
        )
        nudge_pull, nudge_project = _project_nudge_pull(active_projects, now)
        top_intention = None
        if nudge_pull >= 0.5 and nudge_project:
            top_intention = {
                "project_id": str(nudge_project.get("project_id") or ""),
                "title": str(nudge_project.get("title") or nudge_project.get("theme") or ""),
                "pull": round(float(nudge_pull), 4),
            }
        snapshot.update(
            {
                "available": True,
                "inner_weather": weather,
                "reach_out_calibration": dict(state.get("reach_out_calibration") or {"value": calibration}),
                "active_projects": [_compact_project(p) for p in active_projects][:8],
                "proactive_readiness": round(float(readiness), 4),
                "top_intention": top_intention,
                "dialogue_closure": dict(state.get("dialogue_closure") or {}),
            }
        )
    except Exception as exc:  # pragma: no cover - defensive obs path
        logger.debug("proactive autonomy snapshot degraded: %s", exc)
        snapshot["error"] = str(exc)[:200]
    return snapshot


@router.get("/proactive-autonomy")
def get_proactive_autonomy(
    request: Request,
    person_id: str = Query("default"),
) -> dict[str, Any]:
    key = str(person_id or "default").strip() or "default"
    store = require_obs_query_projection_store(request)
    payload, projection = read_process_projection(
        store,
        PROACTIVE_AUTONOMY_KIND,
        key,
        max_age_ms=PROACTIVE_AUTONOMY_MAX_AGE_MS,
    )
    if payload is None:
        payload = {
            "person_id": key,
            "available": False,
            "inner_weather": {},
            "reach_out_calibration": {},
            "active_projects": [],
            "proactive_readiness": None,
            "top_intention": None,
            "dialogue_closure": {},
            "timestamp": _now_ms(),
        }
    return {**payload, "projection": projection}
