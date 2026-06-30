"""Backward-compatible re-export hub for executive_router."""

from __future__ import annotations

# The real implementation now lives in executive_runtime_core.
from host_brain.app.executive_runtime import executive_runtime_sse_generator

# Backward-compatible alias: old callers import executive_router_sse_generator
executive_router_sse_generator = executive_runtime_sse_generator

__all__ = ["executive_router_sse_generator", "executive_runtime_sse_generator"]
