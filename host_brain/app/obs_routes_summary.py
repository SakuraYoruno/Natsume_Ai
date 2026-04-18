"""Observability summary snapshot endpoints for monitor-side first paint.

These endpoints aggregate multiple heavy /obs stats calls into a single
prewarmed snapshot so the monitor UI can open summary pages quickly without
fanning out a burst of requests against the obs sidecar.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from host_brain.app.obs_routes_stats_agent import get_agent_loop_stats, get_memory_stats
from host_brain.app.obs_routes_stats_capability_agent import get_capability_gaps_stats
from host_brain.app.obs_routes_stats_governor import get_errors, get_governor_stats, get_subsystems_stats
from host_brain.app.obs_routes_stats_input_humanoid import (
    get_input_humanoid_loop_alerts,
    get_input_humanoid_loop_diagnostics,
    get_input_humanoid_loop_hotspot_trend,
    get_input_humanoid_loop_hotspots,
    get_input_humanoid_loop_remediation,
    get_input_humanoid_loop_source_correlation,
    get_input_humanoid_loop_stats,
    get_input_humanoid_loop_trend,
)
from host_brain.app.obs_routes_stats_system import get_llm_call_stats, get_qa_stats, get_vts_stats
from host_brain.app.obs_routes_health import get_services_health

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return max(default, minimum)
    try:
        return max(float(raw), minimum)
    except (TypeError, ValueError):
        return max(default, minimum)


def _model_dump_compat(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _clone_payload(payload: Any) -> Any:
    return copy.deepcopy(payload)


def _interval_minutes(window_minutes: int) -> int:
    if window_minutes <= 60:
        return 5
    if window_minutes <= 360:
        return 30
    if window_minutes <= 1440:
        return 60
    return 360


class InsightsSummarySnapshotResponse(BaseModel):
    window_minutes: int
    timestamp: int
    govData: Optional[Dict[str, Any]] = None
    errData: Optional[Dict[str, Any]] = None
    subsysData: Optional[Dict[str, Any]] = None
    agentData: Optional[Dict[str, Any]] = None
    memoryData: Optional[Dict[str, Any]] = None
    vtsData: Optional[Dict[str, Any]] = None
    qaData: Optional[Dict[str, Any]] = None
    llmCallData: Optional[Dict[str, Any]] = None


class DigitalLifeOverviewSnapshotResponse(BaseModel):
    window_minutes: int
    timestamp: int
    vtsData: Optional[Dict[str, Any]] = None
    memoryData: Optional[Dict[str, Any]] = None
    servicesData: Optional[Dict[str, Any]] = None
    subsystemsData: Optional[Dict[str, Any]] = None
    capabilityGapData: Optional[Dict[str, Any]] = None
    humanoidLoopData: Optional[Dict[str, Any]] = None
    humanoidLoopTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopAlertsData: Optional[Dict[str, Any]] = None
    humanoidLoopRemediationData: Optional[Dict[str, Any]] = None
    humanoidLoopDiagnosticsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopSourceCorrelationData: Optional[Dict[str, Any]] = None


_INSIGHTS_SUMMARY_WARM_ENABLED = str(
    os.getenv("HOST_OBS_INSIGHTS_SUMMARY_WARM_ENABLED", "true")
).strip().lower() not in {"0", "false", "no", "off"}
_INSIGHTS_SUMMARY_WARM_INTERVAL_MS = max(
    int(_env_float("HOST_OBS_INSIGHTS_SUMMARY_WARM_INTERVAL_MS", 5000.0, minimum=1000.0)),
    1000,
)
_INSIGHTS_SUMMARY_WARM_STALE_MS = max(
    int(_env_float("HOST_OBS_INSIGHTS_SUMMARY_WARM_STALE_MS", 15000.0, minimum=1000.0)),
    1000,
)
_INSIGHTS_SUMMARY_WARM_WINDOW_MINUTES = max(
    int(_env_float("HOST_OBS_INSIGHTS_SUMMARY_WARM_WINDOW_MINUTES", 60.0, minimum=1.0)),
    1,
)

_DIGITAL_LIFE_OVERVIEW_WARM_ENABLED = str(
    os.getenv("HOST_OBS_DIGITAL_LIFE_OVERVIEW_WARM_ENABLED", "true")
).strip().lower() not in {"0", "false", "no", "off"}
_DIGITAL_LIFE_OVERVIEW_WARM_INTERVAL_MS = max(
    int(_env_float("HOST_OBS_DIGITAL_LIFE_OVERVIEW_WARM_INTERVAL_MS", 5000.0, minimum=1000.0)),
    1000,
)
_DIGITAL_LIFE_OVERVIEW_WARM_STALE_MS = max(
    int(_env_float("HOST_OBS_DIGITAL_LIFE_OVERVIEW_WARM_STALE_MS", 15000.0, minimum=1000.0)),
    1000,
)
_DIGITAL_LIFE_OVERVIEW_WARM_WINDOW_MINUTES = max(
    int(_env_float("HOST_OBS_DIGITAL_LIFE_OVERVIEW_WARM_WINDOW_MINUTES", 360.0, minimum=1.0)),
    1,
)

_insights_summary_warm_thread: threading.Thread | None = None
_insights_summary_warm_stop = threading.Event()
_insights_summary_warm_lock = threading.Lock()
_insights_summary_warm_state: dict[str, Any] = {
    "value": None,
    "updated_ts_ms": 0,
    "last_duration_ms": 0,
    "last_error": None,
}

_digital_life_overview_warm_thread: threading.Thread | None = None
_digital_life_overview_warm_stop = threading.Event()
_digital_life_overview_warm_lock = threading.Lock()
_digital_life_overview_warm_state: dict[str, Any] = {
    "value": None,
    "updated_ts_ms": 0,
    "last_duration_ms": 0,
    "last_error": None,
}


def _build_insights_summary_snapshot_sync(window_minutes: int) -> dict[str, Any]:
    interval_minutes = _interval_minutes(window_minutes)
    payload = InsightsSummarySnapshotResponse(
        window_minutes=window_minutes,
        timestamp=_now_ms(),
        govData=_model_dump_compat(get_governor_stats(window_minutes=window_minutes, interval_minutes=interval_minutes)),
        errData=_model_dump_compat(get_errors(window_minutes=window_minutes, interval_minutes=interval_minutes, limit=15)),
        subsysData=_model_dump_compat(get_subsystems_stats(window_minutes=window_minutes)),
        agentData=_model_dump_compat(get_agent_loop_stats(window_minutes=window_minutes, limit=8)),
        memoryData=_model_dump_compat(get_memory_stats(window_minutes=window_minutes)),
        vtsData=_model_dump_compat(get_vts_stats(window_minutes=window_minutes)),
        qaData=_model_dump_compat(get_qa_stats(window_minutes=window_minutes, interval_minutes=interval_minutes, limit=15)),
        llmCallData=_model_dump_compat(get_llm_call_stats(window_minutes=window_minutes, interval_minutes=interval_minutes)),
    )
    return payload.model_dump()


async def _build_digital_life_overview_snapshot_async(window_minutes: int) -> dict[str, Any]:
    trend_interval_minutes = _interval_minutes(window_minutes)
    services_data = await get_services_health(window_minutes=window_minutes)
    source_correlation = await get_input_humanoid_loop_source_correlation(
        window_minutes=window_minutes,
        interval_minutes=trend_interval_minutes,
        min_bucket_requests=3,
    )
    payload = DigitalLifeOverviewSnapshotResponse(
        window_minutes=window_minutes,
        timestamp=_now_ms(),
        vtsData=_model_dump_compat(get_vts_stats(window_minutes=window_minutes)),
        memoryData=_model_dump_compat(get_memory_stats(window_minutes=window_minutes)),
        servicesData=_model_dump_compat(services_data),
        subsystemsData=_model_dump_compat(get_subsystems_stats(window_minutes=window_minutes)),
        capabilityGapData=_model_dump_compat(
            get_capability_gaps_stats(
                limit_recent=30,
                limit_hotspots=8,
                limit_candidates=8,
                min_candidate_detected=3,
                min_candidate_sessions=2,
                min_candidate_failure_rate=0.35,
                min_candidate_failed=2,
            )
        ),
        humanoidLoopData=_model_dump_compat(get_input_humanoid_loop_stats(window_minutes=window_minutes)),
        humanoidLoopTrendData=_model_dump_compat(
            get_input_humanoid_loop_trend(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
            )
        ),
        humanoidLoopAlertsData=_model_dump_compat(
            get_input_humanoid_loop_alerts(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
                completion_rate_warn=85.0,
                closed_loop_rate_warn=70.0,
                ack_touch_rate_warn=75.0,
                progress_touch_rate_warn=50.0,
                failed_rate_warn=20.0,
                deviation_recovery_rate_warn=60.0,
                closed_loop_drop_warn=25.0,
                baseline_buckets=3,
            )
        ),
        humanoidLoopRemediationData=_model_dump_compat(
            get_input_humanoid_loop_remediation(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
            )
        ),
        humanoidLoopDiagnosticsData=_model_dump_compat(
            get_input_humanoid_loop_diagnostics(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
            )
        ),
        humanoidLoopHotspotsData=_model_dump_compat(
            get_input_humanoid_loop_hotspots(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
            )
        ),
        humanoidLoopHotspotTrendData=_model_dump_compat(
            get_input_humanoid_loop_hotspot_trend(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
            )
        ),
        humanoidLoopSourceCorrelationData=_model_dump_compat(source_correlation),
    )
    return payload.model_dump()


def _refresh_insights_summary_warm_once() -> dict[str, Any] | None:
    if not _INSIGHTS_SUMMARY_WARM_ENABLED:
        return None
    started = _now_ms()
    try:
        payload = _build_insights_summary_snapshot_sync(_INSIGHTS_SUMMARY_WARM_WINDOW_MINUTES)
        with _insights_summary_warm_lock:
            _insights_summary_warm_state["value"] = payload
            _insights_summary_warm_state["updated_ts_ms"] = _now_ms()
            _insights_summary_warm_state["last_duration_ms"] = max(0, _now_ms() - started)
            _insights_summary_warm_state["last_error"] = None
        return payload
    except Exception as exc:
        logger.warning("obs insights summary warm refresh failed: %s", exc)
        with _insights_summary_warm_lock:
            _insights_summary_warm_state["last_duration_ms"] = max(0, _now_ms() - started)
            _insights_summary_warm_state["last_error"] = str(exc)
        return None


def _refresh_digital_life_overview_warm_once() -> dict[str, Any] | None:
    if not _DIGITAL_LIFE_OVERVIEW_WARM_ENABLED:
        return None
    started = _now_ms()
    try:
        payload = asyncio.run(_build_digital_life_overview_snapshot_async(_DIGITAL_LIFE_OVERVIEW_WARM_WINDOW_MINUTES))
        with _digital_life_overview_warm_lock:
            _digital_life_overview_warm_state["value"] = payload
            _digital_life_overview_warm_state["updated_ts_ms"] = _now_ms()
            _digital_life_overview_warm_state["last_duration_ms"] = max(0, _now_ms() - started)
            _digital_life_overview_warm_state["last_error"] = None
        return payload
    except Exception as exc:
        logger.warning("obs digital life overview warm refresh failed: %s", exc)
        with _digital_life_overview_warm_lock:
            _digital_life_overview_warm_state["last_duration_ms"] = max(0, _now_ms() - started)
            _digital_life_overview_warm_state["last_error"] = str(exc)
        return None


def _insights_summary_warm_loop() -> None:
    while not _insights_summary_warm_stop.is_set():
        _refresh_insights_summary_warm_once()
        if _insights_summary_warm_stop.wait(_INSIGHTS_SUMMARY_WARM_INTERVAL_MS / 1000.0):
            break


def _digital_life_overview_warm_loop() -> None:
    while not _digital_life_overview_warm_stop.is_set():
        _refresh_digital_life_overview_warm_once()
        if _digital_life_overview_warm_stop.wait(_DIGITAL_LIFE_OVERVIEW_WARM_INTERVAL_MS / 1000.0):
            break


def start_insights_summary_warm_cache() -> dict[str, Any]:
    global _insights_summary_warm_thread
    if not _INSIGHTS_SUMMARY_WARM_ENABLED:
        return get_insights_summary_warm_status()
    with _insights_summary_warm_lock:
        if _insights_summary_warm_thread and _insights_summary_warm_thread.is_alive():
            return get_insights_summary_warm_status()
        _insights_summary_warm_stop.clear()
        _insights_summary_warm_thread = threading.Thread(
            target=_insights_summary_warm_loop,
            name="obs-insights-summary-warm",
            daemon=True,
        )
        _insights_summary_warm_thread.start()
    return get_insights_summary_warm_status()


def stop_insights_summary_warm_cache() -> dict[str, Any]:
    global _insights_summary_warm_thread
    thread = _insights_summary_warm_thread
    _insights_summary_warm_stop.set()
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
    _insights_summary_warm_thread = None
    return get_insights_summary_warm_status()


def start_digital_life_overview_warm_cache() -> dict[str, Any]:
    global _digital_life_overview_warm_thread
    if not _DIGITAL_LIFE_OVERVIEW_WARM_ENABLED:
        return get_digital_life_overview_warm_status()
    with _digital_life_overview_warm_lock:
        if _digital_life_overview_warm_thread and _digital_life_overview_warm_thread.is_alive():
            return get_digital_life_overview_warm_status()
        _digital_life_overview_warm_stop.clear()
        _digital_life_overview_warm_thread = threading.Thread(
            target=_digital_life_overview_warm_loop,
            name="obs-digital-life-overview-warm",
            daemon=True,
        )
        _digital_life_overview_warm_thread.start()
    return get_digital_life_overview_warm_status()


def stop_digital_life_overview_warm_cache() -> dict[str, Any]:
    global _digital_life_overview_warm_thread
    thread = _digital_life_overview_warm_thread
    _digital_life_overview_warm_stop.set()
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
    _digital_life_overview_warm_thread = None
    return get_digital_life_overview_warm_status()


def get_insights_summary_warm_status() -> dict[str, Any]:
    with _insights_summary_warm_lock:
        updated_ts_ms = int(_insights_summary_warm_state.get("updated_ts_ms") or 0)
        return {
            "enabled": _INSIGHTS_SUMMARY_WARM_ENABLED,
            "running": bool(_insights_summary_warm_thread and _insights_summary_warm_thread.is_alive()),
            "has_value": _insights_summary_warm_state.get("value") is not None,
            "updated_ts_ms": updated_ts_ms or None,
            "age_ms": max(0, _now_ms() - updated_ts_ms) if updated_ts_ms else None,
            "last_duration_ms": int(_insights_summary_warm_state.get("last_duration_ms") or 0),
            "last_error": _insights_summary_warm_state.get("last_error"),
            "interval_ms": _INSIGHTS_SUMMARY_WARM_INTERVAL_MS,
            "stale_after_ms": _INSIGHTS_SUMMARY_WARM_STALE_MS,
            "window_minutes": _INSIGHTS_SUMMARY_WARM_WINDOW_MINUTES,
        }


def get_digital_life_overview_warm_status() -> dict[str, Any]:
    with _digital_life_overview_warm_lock:
        updated_ts_ms = int(_digital_life_overview_warm_state.get("updated_ts_ms") or 0)
        return {
            "enabled": _DIGITAL_LIFE_OVERVIEW_WARM_ENABLED,
            "running": bool(_digital_life_overview_warm_thread and _digital_life_overview_warm_thread.is_alive()),
            "has_value": _digital_life_overview_warm_state.get("value") is not None,
            "updated_ts_ms": updated_ts_ms or None,
            "age_ms": max(0, _now_ms() - updated_ts_ms) if updated_ts_ms else None,
            "last_duration_ms": int(_digital_life_overview_warm_state.get("last_duration_ms") or 0),
            "last_error": _digital_life_overview_warm_state.get("last_error"),
            "interval_ms": _DIGITAL_LIFE_OVERVIEW_WARM_INTERVAL_MS,
            "stale_after_ms": _DIGITAL_LIFE_OVERVIEW_WARM_STALE_MS,
            "window_minutes": _DIGITAL_LIFE_OVERVIEW_WARM_WINDOW_MINUTES,
        }


def _get_warmed_insights_summary_snapshot(window_minutes: int) -> dict[str, Any] | None:
    if not _INSIGHTS_SUMMARY_WARM_ENABLED or window_minutes != _INSIGHTS_SUMMARY_WARM_WINDOW_MINUTES:
        return None
    with _insights_summary_warm_lock:
        payload = _insights_summary_warm_state.get("value")
        updated_ts_ms = int(_insights_summary_warm_state.get("updated_ts_ms") or 0)
        if payload is None or updated_ts_ms <= 0:
            return None
        if _now_ms() - updated_ts_ms > _INSIGHTS_SUMMARY_WARM_STALE_MS:
            return None
        return _clone_payload(payload)


def _get_warmed_digital_life_overview_snapshot(window_minutes: int) -> dict[str, Any] | None:
    if not _DIGITAL_LIFE_OVERVIEW_WARM_ENABLED or window_minutes != _DIGITAL_LIFE_OVERVIEW_WARM_WINDOW_MINUTES:
        return None
    with _digital_life_overview_warm_lock:
        payload = _digital_life_overview_warm_state.get("value")
        updated_ts_ms = int(_digital_life_overview_warm_state.get("updated_ts_ms") or 0)
        if payload is None or updated_ts_ms <= 0:
            return None
        if _now_ms() - updated_ts_ms > _DIGITAL_LIFE_OVERVIEW_WARM_STALE_MS:
            return None
        return _clone_payload(payload)


@router.get("/insights/summary-snapshot", response_model=InsightsSummarySnapshotResponse)
def get_insights_summary_snapshot(
    window_minutes: int = Query(60, ge=1, le=10080),
):
    warmed = _get_warmed_insights_summary_snapshot(window_minutes)
    if warmed is not None:
        return warmed
    return _build_insights_summary_snapshot_sync(window_minutes)


@router.get("/digital-life/overview-snapshot", response_model=DigitalLifeOverviewSnapshotResponse)
async def get_digital_life_overview_snapshot(
    window_minutes: int = Query(360, ge=1, le=10080),
):
    warmed = _get_warmed_digital_life_overview_snapshot(window_minutes)
    if warmed is not None:
        return warmed
    return await _build_digital_life_overview_snapshot_async(window_minutes)
