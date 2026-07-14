"""Versioned Edge service-catalog and health-query contracts.

The catalog is runtime-neutral and standard-library only.  Host code may use
these public descriptors without importing the Edge distribution, while the
Edge runtime remains the sole authority for configuration and live health.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


EDGE_SERVICE_CATALOG_SCHEMA_VERSION = "natsume.edge.service-catalog.v1"
EDGE_HEALTH_QUERY_SCHEMA_VERSION = "natsume.edge.health-query.v1"
EDGE_SERVICE_CATALOG_STAGE = "S_EDGE_SERVICE_CATALOG"


@dataclass(frozen=True, slots=True)
class EdgeServiceDescriptor:
    """Public identity and requiredness of one Edge-owned service."""

    service_id: str
    owner: str = "edge"
    required: bool = True

    def __post_init__(self) -> None:
        if not self.service_id or not isinstance(self.service_id, str):
            raise ValueError("service_id must be a non-empty string")
        if self.owner != "edge":
            raise ValueError("Edge service owner must be 'edge'")
        if not isinstance(self.required, bool):
            raise ValueError("required must be bool")

    def as_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id,
            "owner": self.owner,
            "required": self.required,
        }


EDGE_REQUIRED_SERVICES: tuple[EdgeServiceDescriptor, ...] = (
    EdgeServiceDescriptor(service_id="edge.rpc.gateway"),
    EdgeServiceDescriptor(service_id="edge.rpc.asr"),
    EdgeServiceDescriptor(service_id="edge.rpc.tts"),
    EdgeServiceDescriptor(service_id="edge.rpc.barge_in"),
    EdgeServiceDescriptor(service_id="edge.rpc.resource_gate"),
)


def edge_required_services() -> tuple[EdgeServiceDescriptor, ...]:
    """Return the immutable v1 Edge service catalog."""

    return EDGE_REQUIRED_SERVICES


@dataclass(frozen=True, slots=True)
class EdgeHealthSnapshot:
    """Sanitized result returned by an Edge health-query composition port."""

    statuses: Mapping[str, Mapping[str, Any]]
    schema_version: str = EDGE_HEALTH_QUERY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EDGE_HEALTH_QUERY_SCHEMA_VERSION:
            raise ValueError("unsupported Edge health snapshot schema")
        allowed = {item.service_id for item in EDGE_REQUIRED_SERVICES}
        unknown = sorted(set(self.statuses) - allowed)
        if unknown:
            raise ValueError(f"unknown Edge service ids: {unknown}")

    def runtime_statuses(self) -> dict[str, dict[str, Any]]:
        return {
            str(service_id): dict(status)
            for service_id, status in self.statuses.items()
        }


class EdgeHealthQuery(Protocol):
    """Host composition port; implementations query Edge over a boundary."""

    def query(self) -> EdgeHealthSnapshot: ...


def build_edge_transport_probe_statuses(
    *,
    available: bool,
    detail: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Project transport availability onto the versioned Edge service catalog."""

    if available:
        return {
            "edge.rpc.gateway": {
                "available": True,
                "detail": detail or "edge rpc transport healthy",
            }
        }
    transport_down = {
        "available": False,
        "unavailable_kind": "edge_rpc",
        "detail": detail or "edge rpc transport unavailable",
    }
    return {
        descriptor.service_id: dict(transport_down)
        for descriptor in EDGE_REQUIRED_SERVICES
    }


def build_edge_service_catalog_snapshot(
    descriptors: Sequence[EdgeServiceDescriptor] = EDGE_REQUIRED_SERVICES,
) -> dict[str, Any]:
    """Build the startup-safe public descriptor; never contains Edge config."""

    return {
        "success": True,
        "stage": EDGE_SERVICE_CATALOG_STAGE,
        "reason_code": None,
        "schema_version": EDGE_SERVICE_CATALOG_SCHEMA_VERSION,
        "authority": "edge",
        "services": [descriptor.as_dict() for descriptor in descriptors],
        "issues": [],
    }


__all__ = [
    "EDGE_HEALTH_QUERY_SCHEMA_VERSION",
    "EDGE_REQUIRED_SERVICES",
    "EDGE_SERVICE_CATALOG_SCHEMA_VERSION",
    "EDGE_SERVICE_CATALOG_STAGE",
    "EdgeHealthQuery",
    "EdgeHealthSnapshot",
    "EdgeServiceDescriptor",
    "build_edge_service_catalog_snapshot",
    "build_edge_transport_probe_statuses",
    "edge_required_services",
]
