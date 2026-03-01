"""Edge RPC required service declarations for host health registry."""

from __future__ import annotations

from dataclasses import dataclass


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
