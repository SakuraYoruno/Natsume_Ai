"""Compatibility proxy for the canonical governed skill execution path.

The implementation lives in :mod:`host_brain.executive_runtime.skill_execution` so
callers cannot bypass its route, authority-binding, ActionArbiter, and token
checks through an older duplicate helper.
"""

from __future__ import annotations

from host_brain.executive_runtime.skill_execution import (
    _execute_skill as _execute_skill_core,
    _extract_skill_args,
    _log_skill_exec_row,
)


def _execute_skill(*, ports=None, **kwargs):  # noqa: ANN001, ANN003
    """Compatibility adapter that composes explicit runtime ports at the app edge."""

    if ports is None:
        from host_brain.app.runtime_port_wiring import (  # noqa: PLC0415
            build_executive_runtime_application_ports,
        )

        ports = build_executive_runtime_application_ports()
    return _execute_skill_core(ports=ports, **kwargs)


__all__ = ["_execute_skill", "_extract_skill_args", "_log_skill_exec_row"]
