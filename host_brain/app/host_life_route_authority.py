"""Typed, app-local Digital Life authority exposed to Host route adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from host_brain.app.host_route_ports import HostRoutePorts


LIFE_ROUTE_AUTHORITY_UNAVAILABLE = "HOST_LIFE_ROUTE_AUTHORITY_UNAVAILABLE"
LIFE_ROUTE_AUTHORITY_INVALID = "HOST_LIFE_ROUTE_AUTHORITY_INVALID"


@dataclass(frozen=True, slots=True)
class HostLifeRouteAuthority:
    """The command writer and snapshot reader owned by one Host lifespan."""

    command_service: Any
    snapshot_reader: Any

    def __post_init__(self) -> None:
        if not callable(getattr(self.command_service, "submit", None)):
            raise TypeError("command_service must expose submit()")
        if not callable(getattr(self.snapshot_reader, "get_snapshot", None)):
            raise TypeError("snapshot_reader must expose get_snapshot()")
        if not callable(getattr(self.snapshot_reader, "list_events", None)):
            raise TypeError("snapshot_reader must expose list_events()")


def require_host_life_route_authority(
    route_ports: HostRoutePorts,
) -> HostLifeRouteAuthority:
    """Resolve the current app's authority without consulting process state."""

    if not isinstance(route_ports, HostRoutePorts):
        raise TypeError("route_ports must be HostRoutePorts")
    provider = route_ports.life_command_authority_provider
    if not callable(provider):
        raise HTTPException(
            status_code=503,
            detail=LIFE_ROUTE_AUTHORITY_UNAVAILABLE,
        )
    try:
        authority = provider()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=LIFE_ROUTE_AUTHORITY_UNAVAILABLE,
        ) from exc
    if not isinstance(authority, HostLifeRouteAuthority):
        raise HTTPException(
            status_code=503,
            detail=LIFE_ROUTE_AUTHORITY_INVALID,
        )
    return authority


__all__ = [
    "HostLifeRouteAuthority",
    "LIFE_ROUTE_AUTHORITY_INVALID",
    "LIFE_ROUTE_AUTHORITY_UNAVAILABLE",
    "require_host_life_route_authority",
]
