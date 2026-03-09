"""OBS routes: GET /obs/metrics endpoint."""

from __future__ import annotations

from typing import Any

from host_brain.observability.metrics import MetricsCollector, ObsMode, get_obs_mode


def build_obs_metrics_response(
    *,
    collector: MetricsCollector,
    request_id: str | None = None,
    obs_mode: ObsMode | None = None,
    recent_chains_n: int = 5,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build the /obs/metrics response payload.

    In prod mode, debug_trace is always None.
    In dev mode, debug_trace contains intent_draft/rules_hit/skill_params
    for the given request_id (if provided).
    """
    mode = obs_mode if obs_mode is not None else get_obs_mode()
    payload = collector.collect(
        request_id=request_id,
        obs_mode=mode,
        recent_chains_n=recent_chains_n,
        ts_ms=ts_ms,
    )
    # prod 模式强制清除 debug_trace
    if mode == ObsMode.PROD:
        payload["debug_trace"] = None
    return payload
