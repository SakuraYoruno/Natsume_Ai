"""Compatibility wrapper for migrated observability.service_health_aggregator module."""

from host_brain.observability.service_health_aggregator import (
    ALL_SERVICES,
    ServiceHealthAggregator,
    ServiceHealthResult,
    validate_health_response,
)

__all__ = [
    "ALL_SERVICES",
    "ServiceHealthAggregator",
    "ServiceHealthResult",
    "validate_health_response",
]
