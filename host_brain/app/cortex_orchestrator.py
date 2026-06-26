"""Backward-compatible re-export hub for cortex_orchestrator."""

from __future__ import annotations

# The real implementation lives in agent_runtime_shared.
# The record_user_turn call was inlined into the impl.
from host_brain.app.agent_runtime_shared import (
    _cortex_orchestrator_sse_generator_impl as cortex_orchestrator_sse_generator,
)

__all__ = ["cortex_orchestrator_sse_generator"]
