"""obs_routes.py — Observability router aggregation and compatibility layer.

Phase XIV split modules:
  - obs_routes_core.py: core endpoints/models/helpers
  - obs_routes_stats_*.py: subsystem stats endpoints
  - obs_routes_platform.py: platform/ws endpoints
  - obs_routes_sysmode.py: system mode endpoints
"""

from __future__ import annotations

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
)

import host_brain.app.obs_routes_stats_governor  # noqa: F401
import host_brain.app.obs_routes_stats_agent  # noqa: F401
import host_brain.app.obs_routes_stats_system  # noqa: F401
import host_brain.app.obs_routes_stats_input_dispatch  # noqa: F401
import host_brain.app.obs_routes_stats_input_humanoid  # noqa: F401
import host_brain.app.obs_routes_stats_capability_skill  # noqa: F401
import host_brain.app.obs_routes_stats_capability_agent  # noqa: F401
import host_brain.app.obs_routes_platform  # noqa: F401
import host_brain.app.obs_routes_sysmode  # noqa: F401
import host_brain.app.obs_routes_agent_bio_ops  # noqa: F401
import host_brain.app.obs_routes_security_immune  # noqa: F401
import host_brain.app.obs_routes_task_runtime  # noqa: F401
import host_brain.app.obs_routes_life_evolution  # noqa: F401
import host_brain.app.obs_routes_governance  # noqa: F401
import host_brain.app.obs_routes_evolution_lab  # noqa: F401
