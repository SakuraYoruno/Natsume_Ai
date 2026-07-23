"""Application ports consumed by the canonical Surface runtime.

The Surface domain/application modules never import the HTTP/app composition
package.  The app adapter binds these callables once at composition time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class SurfaceRuntimePorts:
    get_ledger: Callable[[], Any]
    get_life_command_service: Callable[[], Any]
    get_life_snapshot_reader: Callable[[], Any]
    get_persistence_settings: Callable[[], Any]
    build_surface_context_packet: Callable[..., dict[str, Any]]
    build_user_preference_pack: Callable[..., dict[str, Any]]
    get_conversation_history: Callable[[], Any]
    build_conversation_experience_read_pack: Callable[..., dict[str, Any]]
    build_conversation_understanding_pack: Callable[..., dict[str, Any]]
    build_memory_context_debug: Callable[..., dict[str, Any]]
    model_attempt_service: Callable[[], Any]


__all__ = ["SurfaceRuntimePorts"]
