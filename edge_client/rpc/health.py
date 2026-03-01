"""Edge RPC required service declarations for host health registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EdgeServiceSpec:
    service_id: str
    owner: str = "edge"
    required: bool = True


EDGE_REQUIRED_SERVICES: tuple[EdgeServiceSpec, ...] = (
    EdgeServiceSpec(service_id="edge.rpc.gateway"),
    EdgeServiceSpec(service_id="edge.rpc.asr"),
    EdgeServiceSpec(service_id="edge.rpc.tts"),
    EdgeServiceSpec(service_id="edge.rpc.barge_in"),
    EdgeServiceSpec(service_id="edge.rpc.fallback_llm"),
    EdgeServiceSpec(service_id="edge.rpc.resource_gate"),
)


def edge_required_services() -> tuple[EdgeServiceSpec, ...]:
    return EDGE_REQUIRED_SERVICES


def build_edge_transport_probe_statuses(
    *,
    available: bool,
    detail: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build deterministic probe statuses for edge rpc transport during boot."""

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
        "edge.rpc.gateway": dict(transport_down),
        "edge.rpc.asr": dict(transport_down),
        "edge.rpc.tts": dict(transport_down),
        "edge.rpc.barge_in": dict(transport_down),
        "edge.rpc.fallback_llm": dict(transport_down),
        "edge.rpc.resource_gate": dict(transport_down),
    }
