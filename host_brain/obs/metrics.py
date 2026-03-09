"""Compatibility wrapper for migrated observability.metrics module."""

from host_brain.observability.metrics import MetricsCollector, ObsMode, get_obs_mode

__all__ = ["MetricsCollector", "ObsMode", "get_obs_mode"]
