"""obs_routes.py — Observability router aggregation and compatibility layer.

Phase XIV split modules:
  - obs_routes_core.py: core endpoints/models/helpers
  - obs_routes_stats.py: subsystem stats endpoints
  - obs_routes_platform.py: platform/ws endpoints
  - obs_routes_sysmode.py: system mode endpoints
"""

from __future__ import annotations

from typing import Optional

import host_brain.app.obs_routes_core as _obs_routes_core
from host_brain.app.obs_routes_core import (  # noqa: F401
    LiveTraceFeedItem,
    SessionItem,
    SessionsResponse,
    TraceLiveFeedSnapshot,
    TraceNode,
    TraceResponse,
    _build_sessions_response,
    _build_trace_live_feed_delta,
    _build_trace_response,
    _now_ms,
    _safe_json,
    router,
    set_ledger_provider,
)

import host_brain.app.obs_routes_stats  # noqa: F401
from host_brain.app.obs_routes_platform import _fetch_remote_gateway_health  # noqa: F401
import host_brain.app.obs_routes_platform  # noqa: F401
import host_brain.app.obs_routes_sysmode as _obs_routes_sysmode  # noqa: F401
import host_brain.app.obs_routes_agent_bio_ops  # noqa: F401
import host_brain.app.obs_routes_security_immune  # noqa: F401
import host_brain.app.obs_routes_task_runtime  # noqa: F401
import host_brain.app.obs_routes_life_evolution  # noqa: F401
import host_brain.app.obs_routes_governance  # noqa: F401

_mode_transition_lock = _obs_routes_sysmode._mode_transition_lock
_mode_transition_service = _obs_routes_sysmode._mode_transition_service


def _build_trace_live_feed_snapshot(
    ledger,
    *,
    limit: int,
    window_minutes: int,
    keyword: Optional[str],
    active_within_ms: int,
):
    """Compatibility wrapper for tests/legacy patches on obs_routes module."""
    original_build_sessions = _obs_routes_core._build_sessions_response
    original_build_trace = _obs_routes_core._build_trace_response
    original_now_ms = _obs_routes_core._now_ms
    try:
        _obs_routes_core._build_sessions_response = _build_sessions_response
        _obs_routes_core._build_trace_response = _build_trace_response
        _obs_routes_core._now_ms = _now_ms
        return _obs_routes_core._build_trace_live_feed_snapshot(
            ledger,
            limit=limit,
            window_minutes=window_minutes,
            keyword=keyword,
            active_within_ms=active_within_ms,
        )
    finally:
        _obs_routes_core._build_sessions_response = original_build_sessions
        _obs_routes_core._build_trace_response = original_build_trace
        _obs_routes_core._now_ms = original_now_ms
