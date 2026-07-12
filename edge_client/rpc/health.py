"""Compatibility exports for the public Edge service-catalog contract."""

from __future__ import annotations

from natsume_contracts.edge.service_catalog_v1 import (
    EDGE_REQUIRED_SERVICES,
    EdgeServiceDescriptor,
    build_edge_transport_probe_statuses,
    edge_required_services,
)

# Historical name retained for Edge-local consumers.  The implementation and
# authority now live in the neutral, versioned contract package.
EdgeServiceSpec = EdgeServiceDescriptor


__all__ = [
    "EDGE_REQUIRED_SERVICES",
    "EdgeServiceSpec",
    "build_edge_transport_probe_statuses",
    "edge_required_services",
]
