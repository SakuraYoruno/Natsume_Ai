"""Typed application bindings consumed by the process composition root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from host_brain.composition.process_service_container import get_process_service_container


Factory = Callable[..., Any]
GraphHandlerBinding = tuple[str, Callable[..., Any]]


@dataclass(frozen=True, slots=True)
class ProcessApplicationPorts:
    persistence_db_path: Callable[[], str]
    mode_transition_service: Callable[[], Any]
    model_credential_resolver: Callable[[str], str | None]
    model_base_url_resolver: Callable[[str], str | None]
    model_transport: Callable[..., str | dict[str, Any]]
    graph_handler_bindings: Callable[[], Sequence[GraphHandlerBinding]]
    multimodal_observer_factory: Factory
    legacy_runtime_task_registry_factory: Factory
    capability_gap_telemetry_factory: Factory
    capability_forge_service_factory: Factory
    routing_matrix_factory: Factory
    memory_guard_factory: Factory
    state_arbiter_factory: Factory
    state_write_arbiter_factory: Factory
    distillation_engine_factory: Factory
    semantic_recaller_factory: Factory
    episodic_store_adapter_factory: Factory
    probe_edge_rpc_health: Callable[..., Any]
    build_edge_dependency_probe_statuses: Callable[..., Any]
    collect_dependency_probes: Callable[..., Any]
    collect_dependency_probes_sync: Callable[..., Any]


def configure_process_application_ports(ports: ProcessApplicationPorts) -> None:
    if not isinstance(ports, ProcessApplicationPorts):
        raise TypeError("ports must be ProcessApplicationPorts")
    get_process_service_container().application_ports = ports


def require_process_application_ports() -> ProcessApplicationPorts:
    ports = get_process_service_container().application_ports
    if ports is None:
        raise RuntimeError("PROCESS_APPLICATION_PORTS_NOT_CONFIGURED")
    return ports


__all__ = [
    "GraphHandlerBinding",
    "ProcessApplicationPorts",
    "configure_process_application_ports",
    "require_process_application_ports",
]
