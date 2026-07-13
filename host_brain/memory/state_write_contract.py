"""Stable state-write scope contract shared by domain and app adapters."""

from __future__ import annotations

from enum import Enum


class StateScope(str, Enum):
    SOUL_STATE = "soul_state"
    RUNTIME_STATE = "runtime_state"
    MEMORY_STATE = "memory_state"
    EXECUTION_STATE = "execution_state"


__all__ = ["StateScope"]
