"""Bind executive runtime collaborators and install the retired app facade."""

from __future__ import annotations

from typing import Any

from . import _shared, conversation_execution, route_planning, runtime, skill_execution

_MODULES = (_shared, route_planning, skill_execution, conversation_execution, runtime)
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
