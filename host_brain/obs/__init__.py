"""Compatibility package for legacy `host_brain.obs.*` imports.

Canonical observability implementations now live in `host_brain.observability.*`.
"""

from host_brain.observability.metrics import MetricsCollector, ObsMode, get_obs_mode
from host_brain.observability.metrics_timeseries import MetricsTimeseriesService
from host_brain.observability.service_health_aggregator import (
    ServiceHealthAggregator,
    ServiceHealthResult,
)
from host_brain.observability.session_query import SessionQueryService
from host_brain.observability.trace_node_builder import TraceNodeBuilder

__all__ = [
    "MetricsCollector",
    "ObsMode",
    "get_obs_mode",
    "ServiceHealthAggregator",
    "ServiceHealthResult",
    "SessionQueryService",
    "TraceNodeBuilder",
    "MetricsTimeseriesService",
]
