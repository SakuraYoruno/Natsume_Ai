"""Observability summary snapshot endpoints for monitor-side first paint.

These endpoints aggregate multiple heavy /obs stats calls into a single
prewarmed snapshot so the monitor UI can open summary pages quickly without
fanning out a burst of requests against the obs sidecar.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_stats_agent import (
    build_agent_loop_stats_from_store,
    get_memory_stats,
)
from host_brain.app.obs_routes_stats_capability_agent import (
    build_capability_gaps_stats_from_projection,
)
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
from host_brain.app.obs_routes_health import build_services_health_response
from host_brain.app.observability_runtime.capability_stats_projection import (
    read_capability_forge_projection_from_store,
)
from host_brain.app.observability_runtime.health_device_projection import (
    HEALTH_MAX_AGE_MS,
    SERVICE_HEALTH_KEY,
    SERVICE_HEALTH_KIND,
    read_projection_payload,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    AGENT_LOOP_INDEX_KEY,
    AGENT_LOOP_KIND,
    read_from_store,
)
from host_brain.app.observability_runtime.process_state_projection import (
    DIGITAL_LIFE_MAX_AGE_MS,
    DIGITAL_LIFE_OVERVIEW_KIND,
    INSIGHTS_SUMMARY_KIND,
    SUMMARY_MAX_AGE_MS,
    read_process_projection,
    window_key,
)

router = APIRouter(prefix="/obs", tags=["observability"])


def _now_ms() -> int:
    return int(time.time() * 1000)


def _model_dump_compat(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


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
    projection: Dict[str, Any] = Field(default_factory=dict)


class DigitalLifeOverviewSnapshotResponse(BaseModel):
    window_minutes: int
    timestamp: int
    vtsData: Optional[Dict[str, Any]] = None
    memoryData: Optional[Dict[str, Any]] = None
    servicesData: Optional[Dict[str, Any]] = None
    subsystemsData: Optional[Dict[str, Any]] = None
    capabilityGapData: Optional[Dict[str, Any]] = None
    capabilityGapProjection: Dict[str, Any] = Field(default_factory=dict)
    humanoidLoopData: Optional[Dict[str, Any]] = None
    humanoidLoopTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopAlertsData: Optional[Dict[str, Any]] = None
    humanoidLoopRemediationData: Optional[Dict[str, Any]] = None
    humanoidLoopDiagnosticsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopSourceCorrelationData: Optional[Dict[str, Any]] = None
    projection: Dict[str, Any] = Field(default_factory=dict)


INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES = 60
DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES = 360

AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE = "OBS_AGGREGATE_SOURCE_UNAVAILABLE"
AGGREGATE_WINDOW_UNSUPPORTED_REASON_CODE = "OBS_AGGREGATE_WINDOW_UNSUPPORTED"
CAPABILITY_FORGE_FIELD_UNAVAILABLE_REASON_CODE = (
    "OBS_CAPABILITY_FORGE_PROJECTION_UNAVAILABLE"
)

_SUBSYSTEM_LEDGER_TABLES = (
    "memory_ops_log",
    "retrieval_event_log",
    "skill_exec_log",
    "agent_loop_log",
    "airlock_log",
    "bond_update_log",
    "vision_event_log",
    "capability_execution_receipt",
    "channel_io_log",
    "intent_log",
    "qa_violation_log",
)
_MEMORY_LEDGER_TABLES = (
    "memory_ops_log",
    "life_memory_admission_receipt",
    "error_log",
    "scheduler_event_log",
)
_VTS_LEDGER_TABLES = ("avatar_action_log",)
_INSIGHTS_LEDGER_TABLES = tuple(
    dict.fromkeys(
        (
            "governor_log",
            "error_log",
            *_SUBSYSTEM_LEDGER_TABLES,
            *_MEMORY_LEDGER_TABLES,
            *_VTS_LEDGER_TABLES,
            "qa_violation_log",
            "model_attempt_log",
            "llm_token_usage_log",
            "llm_pool_call_log",
        )
    )
)
_DIGITAL_LIFE_LEDGER_TABLES = tuple(
    dict.fromkeys(
        (
            *_SUBSYSTEM_LEDGER_TABLES,
            *_MEMORY_LEDGER_TABLES,
            *_VTS_LEDGER_TABLES,
            "governor_log",
            "error_log",
            "model_attempt_log",
        )
    )
)


class AggregateProjectionSourceUnavailable(RuntimeError):
    """Stable typed failure for a missing aggregate projection dependency."""

    def __init__(self, *, source: str, reason_code: str = AGGREGATE_SOURCE_UNAVAILABLE_REASON_CODE) -> None:
        self.reason_code = str(reason_code)
        self.source = str(source)
        super().__init__(f"{self.reason_code}:{self.source}")


def _require_window(actual: int, expected: int, *, source: str) -> None:
    if int(actual) != int(expected):
        raise AggregateProjectionSourceUnavailable(
            source=source,
            reason_code=AGGREGATE_WINDOW_UNSUPPORTED_REASON_CODE,
        )


def _require_ledger_tables(
    projection_view: Any,
    tables: tuple[str, ...],
    *,
    window_minutes: int,
) -> None:
    now = _now_ms()
    since = now - max(1, int(window_minutes)) * 60_000
    for table in tables:
        try:
            projection_view.fetch_by_time_range(
                table,
                start_ms=since,
                end_ms=now,
                limit=1,
            )
        except Exception:
            raise AggregateProjectionSourceUnavailable(source=f"ledger:{table}") from None
        if getattr(projection_view, "last_degraded_reason", None):
            raise AggregateProjectionSourceUnavailable(source=f"ledger:{table}")


def _require_query_payload(payload: Any, *, source: str) -> None:
    if payload is None:
        raise AggregateProjectionSourceUnavailable(source=source)


def _capability_gap_projection_meta(
    meta: Dict[str, Any],
    *,
    available: bool,
) -> dict[str, Any]:
    upstream_reason = str(meta.get("reason_code") or "").strip() or None
    return {
        "available": bool(available),
        "degraded_state": (
            "HEALTHY"
            if available
            else str(meta.get("degraded_state") or "SOURCE_UNAVAILABLE")
        ),
        "reason_code": (
            None if available else CAPABILITY_FORGE_FIELD_UNAVAILABLE_REASON_CODE
        ),
        "upstream_reason_code": (None if available else upstream_reason),
        "projection_version": int(meta.get("projection_version") or 0),
        "cursor": int(meta.get("source_cursor") or 0),
        "freshness_ts_ms": int(meta.get("freshness_ts_ms") or 0),
    }


def _build_insights_summary_snapshot_sync(
    window_minutes: int,
    *,
    projection_view: Any,
    projection_store: Any,
) -> dict[str, Any]:
    _require_window(
        window_minutes,
        INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES,
        source="insights-summary",
    )
    _require_ledger_tables(
        projection_view,
        _INSIGHTS_LEDGER_TABLES,
        window_minutes=window_minutes,
    )
    try:
        agent_loop_payload, _agent_loop_meta = read_from_store(
            projection_store,
            AGENT_LOOP_KIND,
            AGENT_LOOP_INDEX_KEY,
        )
    except Exception:
        raise AggregateProjectionSourceUnavailable(source="query:agent-loop") from None
    _require_query_payload(agent_loop_payload, source="query:agent-loop")
    interval_minutes = _interval_minutes(window_minutes)
    payload = InsightsSummarySnapshotResponse(
        window_minutes=window_minutes,
        timestamp=_now_ms(),
        govData=_model_dump_compat(get_governor_stats(window_minutes=window_minutes, interval_minutes=interval_minutes, projection_view=projection_view)),
        errData=_model_dump_compat(get_errors(window_minutes=window_minutes, interval_minutes=interval_minutes, limit=15, projection_view=projection_view)),
        subsysData=_model_dump_compat(get_subsystems_stats(window_minutes=window_minutes, projection_view=projection_view)),
        agentData=_model_dump_compat(build_agent_loop_stats_from_store(projection_store, window_minutes=window_minutes, limit=8)),
        memoryData=_model_dump_compat(get_memory_stats(window_minutes=window_minutes, projection_view=projection_view)),
        vtsData=_model_dump_compat(get_vts_stats(window_minutes=window_minutes, projection_view=projection_view)),
        qaData=_model_dump_compat(get_qa_stats(window_minutes=window_minutes, interval_minutes=interval_minutes, limit=15, projection_view=projection_view)),
        llmCallData=_model_dump_compat(get_llm_call_stats(window_minutes=window_minutes, interval_minutes=interval_minutes, projection_view=projection_view)),
    )
    return payload.model_dump()


async def _build_digital_life_overview_snapshot_async(
    window_minutes: int,
    *,
    projection_view: Any,
    projection_store: Any,
) -> dict[str, Any]:
    _require_window(
        window_minutes,
        DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES,
        source="digital-life-overview",
    )
    _require_ledger_tables(
        projection_view,
        _DIGITAL_LIFE_LEDGER_TABLES,
        window_minutes=window_minutes,
    )
    try:
        service_health_payload, _service_health_meta = read_projection_payload(
            projection_store,
            kind=SERVICE_HEALTH_KIND,
            key=SERVICE_HEALTH_KEY,
            max_age_ms=HEALTH_MAX_AGE_MS,
        )
    except Exception:
        raise AggregateProjectionSourceUnavailable(source="query:service-health") from None
    _require_query_payload(service_health_payload, source="query:service-health")
    try:
        capability_payload, capability_meta = read_capability_forge_projection_from_store(
            projection_store
        )
    except Exception:
        capability_payload = None
        capability_meta = {
            "available": False,
            "degraded_state": "SOURCE_UNAVAILABLE",
            "reason_code": "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE",
        }
    capability_gap_data: dict[str, Any] | None = None
    if capability_payload is not None:
        try:
            capability_gap_data = _model_dump_compat(
                build_capability_gaps_stats_from_projection(
                    capability_payload,
                    limit_recent=30,
                    limit_hotspots=8,
                    limit_candidates=8,
                    min_candidate_detected=3,
                    min_candidate_sessions=2,
                    min_candidate_failure_rate=0.35,
                    min_candidate_failed=2,
                )
            )
        except Exception:
            capability_gap_data = None
            capability_meta = {
                "available": False,
                "degraded_state": "SOURCE_UNAVAILABLE",
                "reason_code": "OPERATOR_PROJECTION_DATA_INVALID",
            }
    capability_gap_projection = _capability_gap_projection_meta(
        capability_meta,
        available=capability_gap_data is not None,
    )
    trend_interval_minutes = _interval_minutes(window_minutes)
    services_data = build_services_health_response(
        projection_store,
        window_minutes=window_minutes,
        side=None,
    )
    source_correlation = await get_input_humanoid_loop_source_correlation(
        window_minutes=window_minutes,
        interval_minutes=trend_interval_minutes,
        min_bucket_requests=3,
        projection_view=projection_view,
    )
    payload = DigitalLifeOverviewSnapshotResponse(
        window_minutes=window_minutes,
        timestamp=_now_ms(),
        vtsData=_model_dump_compat(get_vts_stats(window_minutes=window_minutes, projection_view=projection_view)),
        memoryData=_model_dump_compat(get_memory_stats(window_minutes=window_minutes, projection_view=projection_view)),
        servicesData=_model_dump_compat(services_data),
        subsystemsData=_model_dump_compat(get_subsystems_stats(window_minutes=window_minutes, projection_view=projection_view)),
        capabilityGapData=capability_gap_data,
        capabilityGapProjection=capability_gap_projection,
        humanoidLoopData=_model_dump_compat(get_input_humanoid_loop_stats(window_minutes=window_minutes, projection_view=projection_view)),
        humanoidLoopTrendData=_model_dump_compat(
            get_input_humanoid_loop_trend(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                projection_view=projection_view,
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
                projection_view=projection_view,
            )
        ),
        humanoidLoopRemediationData=_model_dump_compat(
            get_input_humanoid_loop_remediation(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
                projection_view=projection_view,
            )
        ),
        humanoidLoopDiagnosticsData=_model_dump_compat(
            get_input_humanoid_loop_diagnostics(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
                projection_view=projection_view,
            )
        ),
        humanoidLoopHotspotsData=_model_dump_compat(
            get_input_humanoid_loop_hotspots(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                min_bucket_requests=3,
                projection_view=projection_view,
            )
        ),
        humanoidLoopHotspotTrendData=_model_dump_compat(
            get_input_humanoid_loop_hotspot_trend(
                window_minutes=window_minutes,
                interval_minutes=trend_interval_minutes,
                projection_view=projection_view,
            )
        ),
        humanoidLoopSourceCorrelationData=_model_dump_compat(source_correlation),
    )
    return payload.model_dump()


@router.get("/insights/summary-snapshot", response_model=InsightsSummarySnapshotResponse)
def get_insights_summary_snapshot(
    request: Request,
    window_minutes: int = Query(60, ge=1, le=10080),
):
    payload, projection = read_process_projection(
        require_obs_query_projection_store(request),
        INSIGHTS_SUMMARY_KIND,
        window_key(window_minutes),
        max_age_ms=SUMMARY_MAX_AGE_MS,
    )
    response = {**(payload or {"window_minutes": window_minutes, "timestamp": _now_ms()}), "projection": projection}
    return InsightsSummarySnapshotResponse(**response).model_dump()


@router.get("/digital-life/overview-snapshot", response_model=DigitalLifeOverviewSnapshotResponse)
async def get_digital_life_overview_snapshot(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080),
):
    payload, projection = read_process_projection(
        require_obs_query_projection_store(request),
        DIGITAL_LIFE_OVERVIEW_KIND,
        window_key(window_minutes),
        max_age_ms=DIGITAL_LIFE_MAX_AGE_MS,
    )
    response = {**(payload or {"window_minutes": window_minutes, "timestamp": _now_ms()}), "projection": projection}
    return DigitalLifeOverviewSnapshotResponse(**response).model_dump()
