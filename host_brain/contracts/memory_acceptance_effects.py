"""Contracts for explicit user-requested memory writes after Task Acceptance."""

from __future__ import annotations


EXPLICIT_MEMORY_STORE_EFFECT_TYPE = "memory.explicit_store"
EXPLICIT_MEMORY_STORE_SCHEMA_V1 = "Natsume.memory.explicit_store.v1"


__all__ = [
    "EXPLICIT_MEMORY_STORE_EFFECT_TYPE",
    "EXPLICIT_MEMORY_STORE_SCHEMA_V1",
]
