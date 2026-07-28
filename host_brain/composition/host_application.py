"""Explicit, frozen Host application ports for one ASGI composition root."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Any

from host_brain.bond.milestone_tracker import MilestoneTracker
from host_brain.channels.multimodal_processor import MultimodalProcessor
from host_brain.channels.tts_context_router import TtsContextRouter
from host_brain.composition.process_application_ports import ProcessApplicationPorts
from host_brain.composition.host_authority_runtime import (
    HostAuthorityRuntimeOwner,
)
from host_brain.composition.host_owned_runtime import (
    HostMemoryServicePublication,
    build_host_graph_runtime,
    build_host_routing_matrix,
)
from host_brain.composition.host_bond_runtime import (
    HostBondRuntimeOwner,
    build_host_bond_client,
)
from host_brain.composition.host_governance_runtime import (
    HostGovernanceRuntimeOwner,
)
from host_brain.composition.host_memory_runtime import HostMemoryRuntimeOwner
from host_brain.contracts.host_lifecycle import HostLifecycleApplicationPorts
from host_brain.core.meta_learning_loop import MetaLearningLoop
from host_brain.memory.memory_worth_evaluator import MemoryWorthEvaluator
from host_brain.persistence.skill_lifecycle_v2_repository import (
    SQLiteSkillLifecycleV2Repository,
)
from host_brain.persona.emotional_silence import EmotionalSilenceEngine
from host_brain.skills.lifecycle_registry import LifecycleBoundSkillRegistry


ServiceProvider = Callable[[], Any]
MemoryContainerAction = Callable[[Any], None]


def _app_local_provider(instance: Any) -> ServiceProvider:
    """Return one provider bound to the instance owned by this Host app."""

    def provide() -> Any:
        return instance

    return provide


def _app_local_lazy_provider(factory: Callable[[], Any]) -> ServiceProvider:
    """Construct one dependency lazily inside this Host app object graph."""

    lock = Lock()
    missing = object()
    instance: Any = missing

    def provide() -> Any:
        nonlocal instance
        if instance is missing:
            with lock:
                if instance is missing:
                    instance = factory()
        return instance

    return provide


@dataclass(frozen=True, slots=True)
class HostApplicationPorts:
    """Dependencies captured by one Host ASGI application factory invocation."""

    lifecycle: HostLifecycleApplicationPorts
    memory_worth_evaluator: ServiceProvider
    security_immune_agent: ServiceProvider
    token_service: ServiceProvider
    bond_dynamics_writer: ServiceProvider
    capability_forge_service: ServiceProvider
    capability_gap_telemetry: ServiceProvider
    capability_scorecard_provider: ServiceProvider
    begin_authority_runtime: Callable[[], None]
    close_authority_runtime: Callable[[], None]
    begin_bond_runtime: Callable[[], None]
    close_bond_runtime: Callable[[], None]
    begin_governance_runtime: Callable[[], None]
    close_governance_runtime: Callable[[], None]
    publish_memory_service_container: MemoryContainerAction
    withdraw_memory_service_container: MemoryContainerAction

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, HostLifecycleApplicationPorts):
            raise TypeError("lifecycle must be HostLifecycleApplicationPorts")
        for field_name in (
            "memory_worth_evaluator",
            "security_immune_agent",
            "token_service",
            "bond_dynamics_writer",
            "capability_forge_service",
            "capability_gap_telemetry",
            "capability_scorecard_provider",
            "begin_authority_runtime",
            "close_authority_runtime",
            "begin_bond_runtime",
            "close_bond_runtime",
            "begin_governance_runtime",
            "close_governance_runtime",
            "publish_memory_service_container",
            "withdraw_memory_service_container",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


def build_production_host_application_ports(
    *,
    process_application_ports: ProcessApplicationPorts,
    authority_runtime_owner: HostAuthorityRuntimeOwner | None = None,
) -> HostApplicationPorts:
    """Build one Host-owned object graph from explicit construction ports."""

    from host_brain.core.activity_monitor import ActivityMonitor  # noqa: PLC0415
    from host_brain.core.mode_transition import ModeTransitionService  # noqa: PLC0415
    from host_brain.core.resource_manager import SystemResourceManager  # noqa: PLC0415
    if not isinstance(process_application_ports, ProcessApplicationPorts):
        raise TypeError(
            "process_application_ports must be ProcessApplicationPorts"
        )
    authority_runtime_owner = (
        authority_runtime_owner
        if authority_runtime_owner is not None
        else HostAuthorityRuntimeOwner(
            persistence_db_path_provider=(
                process_application_ports.persistence_db_path
            ),
            model_credential_resolver=(
                process_application_ports.model_credential_resolver
            ),
            model_base_url_resolver=(
                process_application_ports.model_base_url_resolver
            ),
            model_transport=process_application_ports.model_transport,
        )
    )
    ledger_provider = authority_runtime_owner.require_ledger
    memory_service_publication = HostMemoryServicePublication()
    silence_engine = EmotionalSilenceEngine()
    milestone_tracker = MilestoneTracker()
    multimodal_processor = MultimodalProcessor()
    tts_context_router = TtsContextRouter()
    meta_learning_loop = MetaLearningLoop()
    memory_worth_evaluator = MemoryWorthEvaluator()
    capability_gap_telemetry = _app_local_lazy_provider(
        process_application_ports.capability_gap_telemetry_factory
    )
    capability_forge_service = _app_local_lazy_provider(
        lambda: process_application_ports.capability_forge_service_factory(
            telemetry=capability_gap_telemetry(),
            ledger=ledger_provider(),
        )
    )
    security_immune_agent = _app_local_lazy_provider(
        lambda: process_application_ports.security_immune_agent_factory(
            ledger_provider=ledger_provider,
        )
    )
    conversation_turn_store = _app_local_lazy_provider(
        lambda: process_application_ports.conversation_turn_store_factory(
            ledger=ledger_provider(),
        )
    )
    conversation_history = _app_local_lazy_provider(
        lambda: process_application_ports.conversation_history_factory(
            repository=conversation_turn_store(),
        )
    )
    session_topic_ledger_store = _app_local_lazy_provider(
        lambda: process_application_ports.session_topic_ledger_store_factory(
            ledger=ledger_provider(),
        )
    )
    session_tactic_ledger_store = _app_local_lazy_provider(
        lambda: process_application_ports.session_tactic_ledger_store_factory(
            ledger=ledger_provider(),
        )
    )
    shared_memory_index = _app_local_lazy_provider(
        lambda: process_application_ports.shared_memory_index_factory(
            store=authority_runtime_owner.require_episodic_store(),
        )
    )
    multimodal_observer = _app_local_lazy_provider(
        lambda: process_application_ports.multimodal_observer_factory(
            model_attempt_service_provider=(
                authority_runtime_owner.require_model_attempt_service
            ),
        )
    )
    graph_runtime = _app_local_lazy_provider(
        lambda: build_host_graph_runtime(
            graph_runtime_factory=process_application_ports.graph_runtime_factory,
            graph_handler_bindings=process_application_ports.graph_handler_bindings,
            ledger_provider=ledger_provider,
        )
    )
    routing_matrix = _app_local_lazy_provider(
        lambda: build_host_routing_matrix(
            routing_matrix_factory=process_application_ports.routing_matrix_factory,
            ledger_provider=ledger_provider,
        )
    )
    bond_runtime_owner = HostBondRuntimeOwner(
        ledger_provider=ledger_provider,
        stage_engine_factory=process_application_ports.bond_stage_engine_factory,
        dynamics_writer_factory=(
            process_application_ports.bond_dynamics_writer_factory
        ),
        update_queue_factory=process_application_ports.bond_update_queue_factory,
        client_factory=build_host_bond_client,
    )
    memory_runtime_owner = HostMemoryRuntimeOwner(
        ledger_provider=ledger_provider,
        memory_guard_factory=process_application_ports.memory_guard_factory,
        state_arbiter_factory=process_application_ports.state_arbiter_factory,
        state_write_arbiter_factory=(
            process_application_ports.state_write_arbiter_factory
        ),
        episodic_store_adapter_factory=(
            process_application_ports.episodic_store_adapter_factory
        ),
        distillation_engine_factory=(
            process_application_ports.distillation_engine_factory
        ),
        semantic_recaller_factory=(
            process_application_ports.semantic_recaller_factory
        ),
    )
    emotion_context_runtime = _app_local_lazy_provider(
        lambda: process_application_ports.emotion_runtime_factory(
            timeline_provider=memory_runtime_owner.require_emotion_timeline,
        )
    )
    governance_runtime_owner = HostGovernanceRuntimeOwner(
        ledger_provider=ledger_provider,
        persistence_db_path_provider=(
            process_application_ports.persistence_db_path
        ),
        skill_lifecycle_repository_factory=(
            SQLiteSkillLifecycleV2Repository
        ),
        lifecycle_skill_registry_factory=LifecycleBoundSkillRegistry,
    )

    return HostApplicationPorts(
        lifecycle=HostLifecycleApplicationPorts(
            memory_guard=memory_runtime_owner.require_memory_guard,
            state_arbiter=memory_runtime_owner.require_state_arbiter,
            distillation_engine=(
                memory_runtime_owner.require_distillation_engine
            ),
            semantic_recaller=(
                memory_runtime_owner.require_semantic_recaller
            ),
            episodic_store=authority_runtime_owner.require_episodic_store,
            skill_registry=governance_runtime_owner.require_skill_registry,
            graph_runtime=graph_runtime,
            routing_matrix=routing_matrix,
            tool_catalog=governance_runtime_owner.require_tool_catalog,
            capability_broker=(
                authority_runtime_owner.require_capability_broker
            ),
            model_attempt_service=(
                authority_runtime_owner.require_model_attempt_service
            ),
            mode_transition_service_factory=ModeTransitionService,
            activity_monitor_factory=ActivityMonitor,
            resource_manager_factory=SystemResourceManager,
            ledger=ledger_provider,
            life_memory_store=(
                authority_runtime_owner.require_life_memory_store
            ),
            bond_update_queue=bond_runtime_owner.require_update_queue,
            life_snapshot_reader=(
                process_application_ports.life_snapshot_reader
            ),
            silence_engine=_app_local_provider(silence_engine),
            state_write_arbiter=(
                memory_runtime_owner.require_state_write_arbiter
            ),
            emotion_timeline=(
                memory_runtime_owner.require_emotion_timeline
            ),
            emotion_context_runtime=emotion_context_runtime,
            bond_stage_engine=bond_runtime_owner.require_stage_engine,
            bond_neo4j_client=bond_runtime_owner.require_client,
            shared_memory_index=shared_memory_index,
            milestone_tracker=_app_local_provider(milestone_tracker),
            prompt_registry=authority_runtime_owner.require_prompt_registry,
            user_emotion_profile=(
                lambda: emotion_context_runtime().user_emotion_profile()
            ),
            intent_engine=governance_runtime_owner.require_intent_engine,
            multimodal_observer=multimodal_observer,
            multimodal_processor=_app_local_provider(multimodal_processor),
            conversation_history=conversation_history,
            conversation_turn_store=conversation_turn_store,
            life_memory_service=memory_service_publication.require_service,
            model_route_catalog=(
                authority_runtime_owner.require_model_route_catalog
            ),
            session_topic_ledger_store=session_topic_ledger_store,
            session_tactic_ledger_store=session_tactic_ledger_store,
            tts_context_router=_app_local_provider(tts_context_router),
            meta_learning_loop=_app_local_provider(meta_learning_loop),
            probe_edge_rpc_health=(
                process_application_ports.probe_edge_rpc_health
            ),
            build_edge_dependency_probe_statuses=(
                process_application_ports.build_edge_dependency_probe_statuses
            ),
        ),
        memory_worth_evaluator=_app_local_provider(memory_worth_evaluator),
        security_immune_agent=security_immune_agent,
        token_service=governance_runtime_owner.require_token_service,
        bond_dynamics_writer=bond_runtime_owner.require_writer,
        capability_forge_service=capability_forge_service,
        capability_gap_telemetry=capability_gap_telemetry,
        capability_scorecard_provider=(
            governance_runtime_owner.require_capability_scorecard_provider
        ),
        begin_authority_runtime=authority_runtime_owner.begin,
        close_authority_runtime=authority_runtime_owner.close,
        begin_bond_runtime=bond_runtime_owner.begin,
        close_bond_runtime=bond_runtime_owner.close,
        begin_governance_runtime=governance_runtime_owner.begin,
        close_governance_runtime=governance_runtime_owner.close,
        publish_memory_service_container=memory_service_publication.publish,
        withdraw_memory_service_container=memory_service_publication.withdraw,
    )


__all__ = [
    "HostApplicationPorts",
    "MemoryContainerAction",
    "ServiceProvider",
    "build_production_host_application_ports",
]
