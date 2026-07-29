"""Build the explicit construction ports for one Host application root."""

from __future__ import annotations

from pathlib import Path

from host_brain.composition.process_application_ports import ProcessApplicationPorts


def build_process_application_ports(
    *,
    persistence_db_path: str | Path | None = None,
) -> ProcessApplicationPorts:
    persistence_db_path_override = (
        None if persistence_db_path is None else str(persistence_db_path)
    )

    from host_brain.app.capability_forge import CapabilityForgeService
    from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
    from host_brain.app.graph_config_builder import (
        INTENT_AGENT_ENTRY_HANDLER_ID,
        INTENT_AGENT_HANDLER_ID,
        JUDGE_AGENT_HANDLER_ID,
        PENTEST_AGENT_HANDLER_ID,
        REPAIR_AGENT_HANDLER_ID,
        RETRIEVAL_AGENT_HANDLER_ID,
        SUPERVISOR_DISPATCH_HANDLER_ID,
        SURFACE_REPLY_HANDLER_ID,
        TRANSLATE_AGENT_HANDLER_ID,
    )
    from host_brain.app.handlers.intent_agent_entry_handler import intent_agent_entry_handler
    from host_brain.app.handlers.intent_agent_handler import intent_agent_handler
    from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
    from host_brain.app.handlers.pentest_agent_handler import pentest_agent_handler
    from host_brain.app.handlers.repair_agent_handler import repair_agent_handler
    from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler
    from host_brain.app.handlers.surface_reply_handler import surface_reply_handler
    from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
    from host_brain.app.llm.routing_matrix import RoutingMatrix
    from host_brain.app.memory.distillation_engine import DistillationEngine
    from host_brain.app.memory.episodic_store import EpisodicStore
    from host_brain.app.memory.memory_guard import MemoryGuard
    from host_brain.app.memory.state_arbiter import StateArbiter
    from host_brain.app.memory.state_write_arbiter import StateWriteArbiter
    from host_brain.app.model_provider_composition import (
        invoke_model_provider_transport,
        resolve_model_provider_base_url,
        resolve_model_provider_credential,
    )
    from host_brain.app.multimodal_observer import MultimodalObserver
    from host_brain.bond.bond_stage_engine import BondStageEngine
    from host_brain.bond.shared_memory_index import SharedMemoryIndex
    from host_brain.app.recall.semantic_recall import SemanticRecaller
    from host_brain.app.settings import get_persistence_settings
    from host_brain.app.startup import (
        build_edge_dependency_probe_statuses,
        collect_dependency_probes,
        collect_dependency_probes_sync,
        probe_edge_rpc_health,
    )
    from host_brain.conversation.history import ConversationHistory
    from host_brain.integrations.graph_runtime import GraphRuntimeContract
    from host_brain.persistence.context_repository import ContextRepository
    from host_brain.persona.context.tactic_ledger import SessionTacticLedgerStore
    from host_brain.persona.context.topic_ledger import SessionTopicLedgerStore
    from host_brain.security_immune.agent import SecurityImmuneAgent

    def conversation_turn_store_factory(*, ledger):
        return ContextRepository.from_ledger(ledger)

    def graph_handler_bindings():
        return (
            (SUPERVISOR_DISPATCH_HANDLER_ID, supervisor_dispatch_handler),
            (INTENT_AGENT_ENTRY_HANDLER_ID, intent_agent_entry_handler),
            (SURFACE_REPLY_HANDLER_ID, surface_reply_handler),
            (TRANSLATE_AGENT_HANDLER_ID, translate_agent_handler),
            (INTENT_AGENT_HANDLER_ID, intent_agent_handler),
            (JUDGE_AGENT_HANDLER_ID, judge_agent_handler),
            (RETRIEVAL_AGENT_HANDLER_ID, retrieval_agent_handler),
            (REPAIR_AGENT_HANDLER_ID, repair_agent_handler),
            (PENTEST_AGENT_HANDLER_ID, pentest_agent_handler),
        )

    def life_snapshot_reader():
        from host_brain.composition.life import PROCESS_LIFE_PROVIDER

        return PROCESS_LIFE_PROVIDER.require().snapshot_reader

    return ProcessApplicationPorts(
        persistence_db_path=lambda: (
            persistence_db_path_override
            if persistence_db_path_override is not None
            else str(get_persistence_settings().canonical_db_path)
        ),
        model_credential_resolver=resolve_model_provider_credential,
        model_base_url_resolver=resolve_model_provider_base_url,
        model_transport=invoke_model_provider_transport,
        graph_handler_bindings=graph_handler_bindings,
        graph_runtime_factory=GraphRuntimeContract,
        bond_stage_engine_factory=BondStageEngine,
        shared_memory_index_factory=SharedMemoryIndex,
        multimodal_observer_factory=MultimodalObserver,
        conversation_turn_store_factory=conversation_turn_store_factory,
        conversation_history_factory=ConversationHistory,
        session_topic_ledger_store_factory=SessionTopicLedgerStore,
        session_tactic_ledger_store_factory=SessionTacticLedgerStore,
        security_immune_agent_factory=SecurityImmuneAgent,
        capability_gap_telemetry_factory=CapabilityGapTelemetry,
        capability_forge_service_factory=CapabilityForgeService,
        routing_matrix_factory=RoutingMatrix,
        memory_guard_factory=MemoryGuard,
        state_arbiter_factory=StateArbiter,
        state_write_arbiter_factory=StateWriteArbiter,
        distillation_engine_factory=DistillationEngine,
        semantic_recaller_factory=SemanticRecaller,
        episodic_store_adapter_factory=EpisodicStore,
        probe_edge_rpc_health=probe_edge_rpc_health,
        build_edge_dependency_probe_statuses=build_edge_dependency_probe_statuses,
        collect_dependency_probes=collect_dependency_probes,
        collect_dependency_probes_sync=collect_dependency_probes_sync,
        life_snapshot_reader=life_snapshot_reader,
    )
__all__ = ["build_process_application_ports"]
