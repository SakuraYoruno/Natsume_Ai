"""Compatibility export for the core-owned orchestration settings contract."""

from host_brain.core.orchestration_settings import (
    OrchestrationSettings,
    get_orchestration_settings,
)


__all__ = ["OrchestrationSettings", "get_orchestration_settings"]
