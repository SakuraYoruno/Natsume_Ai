from __future__ import annotations

from host_brain.obs.metrics import MetricsCollector as LegacyMetricsCollector
from host_brain.obs.metrics_timeseries import MetricsTimeseriesService as LegacyMetricsTimeseriesService
from host_brain.obs.routes import build_obs_metrics_response as legacy_build_obs_metrics_response
from host_brain.obs.service_health_aggregator import ServiceHealthAggregator as LegacyServiceHealthAggregator
from host_brain.obs.session_query import SessionQueryService as LegacySessionQueryService
from host_brain.obs.trace_node_builder import TraceNodeBuilder as LegacyTraceNodeBuilder
from host_brain.obs.trace_node_builder import _IO_MAX_BYTES as LEGACY_IO_MAX_BYTES
from host_brain.observability.metrics import MetricsCollector
from host_brain.observability.metrics_timeseries import MetricsTimeseriesService
from host_brain.observability.routes import build_obs_metrics_response
from host_brain.observability.service_health_aggregator import ServiceHealthAggregator
from host_brain.observability.session_query import SessionQueryService
from host_brain.observability.trace_node_builder import TraceNodeBuilder, _IO_MAX_BYTES


def test_legacy_obs_imports_alias_to_canonical_observability_modules() -> None:
    assert LegacyMetricsCollector is MetricsCollector
    assert LegacyMetricsTimeseriesService is MetricsTimeseriesService
    assert LegacyServiceHealthAggregator is ServiceHealthAggregator
    assert LegacySessionQueryService is SessionQueryService
    assert LegacyTraceNodeBuilder is TraceNodeBuilder
    assert legacy_build_obs_metrics_response is build_obs_metrics_response
    assert LEGACY_IO_MAX_BYTES == _IO_MAX_BYTES
