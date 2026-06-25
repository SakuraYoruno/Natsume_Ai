"""Executive runtime implementation entrypoint.

Backward-compatible re-export hub. The real helpers and proxy now live in
``executive_runtime_core``.
"""

from __future__ import annotations

import asyncio  # kept for test monkeypatch compatibility

# Re-export helpers from executive_runtime_core (where they now live)
from host_brain.app.executive_runtime_core import (
    _extract_result_code,
    record_executive_handoff,
    build_runtime_capability_handoff_extra,
    record_runtime_capability_execution,
    try_handle_recent_turn_direct_route,
    try_handle_task_progress_direct_route,
    stream_chat_route,
    _legacy_direct_text_routes_enabled,
)

# Re-export the core impl as the old entry point name for backward compat
from host_brain.app.executive_runtime_core import (
    _executive_runtime_core_impl as executive_runtime_sse_generator,
)

__all__ = [
    "executive_runtime_sse_generator",
    "record_executive_handoff",
    "build_runtime_capability_handoff_extra",
    "record_runtime_capability_execution",
    "try_handle_recent_turn_direct_route",
    "try_handle_task_progress_direct_route",
    "stream_chat_route",
]
