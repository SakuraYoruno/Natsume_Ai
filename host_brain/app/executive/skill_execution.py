"""Compatibility proxy for the canonical governed skill execution path.

The implementation lives in :mod:`host_brain.app.executive_runtime_core` so
callers cannot bypass its route, authority-binding, ActionArbiter, and token
checks through an older duplicate helper.
"""

from __future__ import annotations

from host_brain.app.executive_runtime_core import (
    _execute_skill,
    _extract_skill_args,
    _log_skill_exec_row,
)

__all__ = ["_execute_skill", "_extract_skill_args", "_log_skill_exec_row"]
