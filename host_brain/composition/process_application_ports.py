"""Frozen construction ports supplied explicitly to one Host app root."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

Factory = Callable[..., Any]
GraphHandlerBinding = tuple[str, Callable[..., Any]]


@dataclass(frozen=True, slots=True)
class ProcessApplicationPorts:
    persistence_db_path: Callable[[], str]
    model_credential_resolver: Callable[[str], str | None]
    model_base_url_resolver: Callable[[str], str | None]
    model_transport: Callable[..., str | dict[str, Any]]
    graph_handler_bindings: Callable[[], Sequence[GraphHandlerBinding]]
    graph_runtime_factory: Factory
    shared_memory_index_factory: Factory
    multimodal_observer_factory: Factory
    conversation_turn_store_factory: Factory
    conversation_history_factory: Factory
    session_topic_ledger_store_factory: Factory
    session_tactic_ledger_store_factory: Factory
    security_immune_agent_factory: Factory
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
    life_snapshot_reader: Callable[[], Any]

__all__ = [
    "GraphHandlerBinding",
    "ProcessApplicationPorts",
]
