"""Compose lifecycle services and support the retired HTTP helper import path."""

from __future__ import annotations

from typing import Any

from . import _shared, background_projection, life_jobs, proactive_dispatch, runtime_snapshot, scheduler_handlers

_MODULES = (_shared, background_projection, runtime_snapshot, proactive_dispatch, scheduler_handlers, life_jobs)
_SYMBOLS: dict[str, Any] = {}
for _module in _MODULES:
    _SYMBOLS.update(
        (name, value)
        for name, value in vars(_module).items()
        if not name.startswith("__")
    )
for _module in _MODULES:
    vars(_module).update(_SYMBOLS)


__all__: list[str] = []
