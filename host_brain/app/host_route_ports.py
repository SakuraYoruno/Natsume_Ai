"""Application-local dependencies for Host HTTP route adapters.

The providers are captured by router factories.  They are intentionally kept
out of module state so creating or closing one ASGI application cannot mutate
the dependency graph of another application in the same process.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
import inspect
from typing import Any


RouteProvider = Callable[[], Any | None]


@dataclass(frozen=True, slots=True)
class HostRoutePorts:
    """Immutable provider bundle owned by one Host ASGI application."""

    canon_service_provider: RouteProvider | None = None
    tool_broker_provider: RouteProvider | None = None
    tool_catalog_provider: RouteProvider | None = None
    bond_engine_provider: RouteProvider | None = None
    bond_writer_provider: RouteProvider | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "canon_service_provider",
            "tool_broker_provider",
            "tool_catalog_provider",
            "bond_engine_provider",
            "bond_writer_provider",
        ):
            provider = getattr(self, field_name)
            if provider is not None and not callable(provider):
                raise TypeError(f"{field_name} must be callable or None")


def bind_host_route_endpoint(
    endpoint: Callable[..., Any],
    ports: HostRoutePorts,
) -> Callable[..., Any]:
    """Bind ``_route_ports`` without exposing it as an HTTP parameter."""

    @wraps(endpoint)
    async def bound(*args: Any, **kwargs: Any) -> Any:
        return await endpoint(*args, _route_ports=ports, **kwargs)

    signature = inspect.signature(endpoint)
    bound.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[
            parameter
            for name, parameter in signature.parameters.items()
            if name != "_route_ports"
        ]
    )
    return bound


__all__ = ["HostRoutePorts", "RouteProvider", "bind_host_route_endpoint"]
