"""Explicit process composition accessors for Host-owned services.

Mutable process state is owned exclusively by :class:`ProcessServiceContainer`.
This module constructs the production object graph; HTTP adapters only import
the narrow accessor they need.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Mapping, Optional

if TYPE_CHECKING:
    from host_brain.capability_plane.production_composition import CapabilityRuntimeComposition
    from host_brain.model_runtime.composition import ModelRuntimeComposition
    from host_brain.model_runtime.model_attempt_service import ModelAttemptService
    from host_brain.model_runtime.prompt_registry import PromptRegistry

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.neo4j_client import InMemoryNeo4jBondClient, SqliteBondClient, build_neo4j_bond_client, load_neo4j_runtime_config  # 修复审计问题 #10
from host_brain.integrations.graph_runtime import GraphRuntimeContract
from host_brain.memory.episodic import EpisodicStore
from host_brain.memory.life_memory.store import LifeMemoryStore
from host_brain.observability.metrics import MetricsCollector
from host_brain.reasoning.reflection_chain import ReflectionChain
from host_brain.channels.multimodal_processor import MultimodalProcessor
from host_brain.channels.tts_context_router import TtsContextRouter
from host_brain.memory.memory_worth_evaluator import MemoryWorthEvaluator
from host_brain.core.predictive_state import PredictiveStateEngine
from host_brain.core.meta_learning_loop import MetaLearningLoop
from host_brain.agents.planning_agent import PlanningAgent
from host_brain.executive_runtime.capability_synthesis import RuntimeCapabilityRegistry

# 新模块导入（优化计划新增模块）
from host_brain.persona.context.tactic_ledger import SessionTacticLedgerStore
from host_brain.persona.context.topic_ledger import SessionTopicLedgerStore
from host_brain.conversation.history import ConversationHistory
from host_brain.persistence.context_repository import ContextRepository

_logger = logging.getLogger("Natsume.dependencies")
_EDGE_RPC_DEPENDENCY_SERVICE_IDS = (
    "edge.rpc.gateway",
    "edge.rpc.asr",
    "edge.rpc.tts",
    "edge.rpc.barge_in",
    "edge.rpc.resource_gate",
)
_EDGE_RPC_COMPONENT_BY_SERVICE_ID = {
    "edge.rpc.asr": "asr",
    "edge.rpc.tts": "tts",
    "edge.rpc.barge_in": "voice_pipeline",
    "edge.rpc.resource_gate": "resource_gate",
}

from host_brain.composition.process_service_container import get_process_service_container
from host_brain.composition.process_application_ports import require_process_application_ports


def _container():
    return get_process_service_container()



from host_brain.composition.process_environment import (
    _parse_bool_env,
    _parse_int_env,
    build_edge_dependency_probe_statuses,
    collect_dependency_probes,
    collect_dependency_probes_sync,
    get_activity_monitor,
    get_edge_base_url,
    get_edge_base_urls,
    get_emotion_timeline,
    get_endocrine_state,
    get_hard_state,
    get_resource_manager,
    notify_user_interaction,
    probe_edge_rpc_health,
    set_sys_mode,
)



def get_ledger() -> SQLiteLedger:
    """Return the canonical ledger only after the SQL catalog is ready.

    Durable Host processes never rely on SQLiteLedger inline DDL.  The
    in-memory branch remains solely for isolated tests that intentionally have
    no durable migration history.
    """
    if _container().ledger is None:
        db_path = str(
            require_process_application_ports().persistence_db_path()
            or "host_brain/data/audit.sqlite3"
        ).strip()
        if db_path == ":memory:":
            _container().ledger = SQLiteLedger(
                db_path=db_path,
                check_same_thread=False,
                life_audit_anchor_writer=_write_life_audit_anchor,
            )
        else:
            from host_brain.audit.migration_runner import (  # noqa: PLC0415
                SQLiteMigrationRunner,
            )

            migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
                request_id="host-ledger-startup",
            )
            if not migrated.success:
                raise RuntimeError(
                    f"{migrated.reason_code or 'MIGRATION_EXEC_FAILED'}: "
                    f"{migrated.detail}"
                )
            _container().ledger = SQLiteLedger(
                db_path=db_path,
                check_same_thread=False,
                initialize_schema=False,
                life_audit_anchor_writer=_write_life_audit_anchor,
            )
    return _container().ledger


def _write_life_audit_anchor(**anchor: Any) -> None:
    """Composition-owned bridge from audit events to Life memory."""

    get_life_memory_store().write_background_episode(**anchor)


def get_capability_execution_journal():
    """Bind capability admission/receipts to the canonical durable ledger."""

    from host_brain.capability_plane.journal import (  # noqa: PLC0415
        SQLiteCapabilityExecutionJournal,
    )

    return SQLiteCapabilityExecutionJournal(get_ledger())


def get_capability_runtime_composition() -> "CapabilityRuntimeComposition":
    """Return the sole process CapabilityBroker object graph."""

    with _container().capability_runtime_lock:
        if _container().capability_runtime_composition is None:
            from host_brain.capability_plane.production_composition import (  # noqa: PLC0415
                build_production_capability_runtime,
            )

            _container().capability_runtime_composition = build_production_capability_runtime(
                execution_journal=get_capability_execution_journal(),
            )
        return _container().capability_runtime_composition


def get_capability_broker():
    """Return the unique production capability execution authority."""

    return get_capability_runtime_composition().broker


def reset_capability_runtime_composition(
    replacement: "CapabilityRuntimeComposition | None" = None,
) -> None:
    """Replace the complete capability graph for lifecycle control or tests."""

    if replacement is not None:
        from host_brain.capability_plane.production_composition import (  # noqa: PLC0415
            CapabilityRuntimeComposition,
        )

        if not isinstance(replacement, CapabilityRuntimeComposition):
            raise TypeError("replacement must be CapabilityRuntimeComposition or None")
    with _container().capability_runtime_lock:
        current = _container().capability_runtime_composition
        _container().capability_runtime_composition = replacement
    if current is not None and current is not replacement:
        current.broker.close()


def get_query_projection_store():
    """Return the generic AW07 durable query projection repository.

    The canonical ledger startup applies migration 0082 before this accessor
    is constructed, so every production projection kind shares one governed
    durable database and schema contract.
    """
    ledger = get_ledger()
    canonical_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
    if not canonical_path or canonical_path == ":memory:":
        raise RuntimeError("OPERATOR_PROJECTION_DURABLE_DB_REQUIRED")
    if (
        _container().query_projection_store is None
        or str(getattr(_container().query_projection_store, "db_path", "")) != canonical_path
    ):
        from host_brain.observability.projection_store import QueryProjectionStore  # noqa: PLC0415

        _container().query_projection_store = QueryProjectionStore(canonical_path)
    return _container().query_projection_store


def peek_query_projection_store():
    """Return the initialized query store without constructing runtime state.

    Operator GET handlers use this non-owning accessor so a read request can
    never open the ledger, assert migrations or initialize a repository.
    Projection workers remain the owners of ``get_query_projection_store``.
    """

    return _container().query_projection_store


def get_model_runtime_composition() -> "ModelRuntimeComposition":
    """Return the sole process model-runtime object graph.

    The default graph uses the production prompt catalog, a server-owned
    dynamic route resolver and vault-backed one-attempt provider adapters.
    Tests may replace only the whole composition.
    """
    with _container().model_runtime_lock:
        if _container().model_runtime_composition is None:
            from host_brain.model_runtime.production_composition import (  # noqa: PLC0415
                build_default_production_model_runtime,
            )
            ports = require_process_application_ports()

            _container().model_runtime_composition = build_default_production_model_runtime(
                ledger=get_ledger(),
                credential_resolver=ports.model_credential_resolver,
                base_url_resolver=ports.model_base_url_resolver,
                transport=ports.model_transport,
            )
        return _container().model_runtime_composition


def get_model_attempt_service() -> "ModelAttemptService":
    """Return the unique model invocation authority."""

    return get_model_runtime_composition().attempt_service


def get_model_route_catalog():
    """Return the process route catalog without forcing ledger construction."""

    with _container().model_runtime_lock:
        current = _container().model_runtime_composition
    if current is not None and current.route_catalog is not None:
        return current.route_catalog
    from host_brain.model_runtime.production_composition import (  # noqa: PLC0415
        get_default_production_route_catalog,
    )

    return get_default_production_route_catalog()


def get_prompt_registry() -> "PromptRegistry":
    """Return the process prompt registry without constructing the ledger."""

    with _container().model_runtime_lock:
        current = _container().model_runtime_composition
    if current is not None:
        return current.prompt_registry
    from host_brain.model_runtime.production_composition import (  # noqa: PLC0415
        get_default_production_prompt_registry,
    )

    return get_default_production_prompt_registry()


def reset_model_runtime_composition(
    replacement: "ModelRuntimeComposition | None" = None,
) -> None:
    """Reset or explicitly replace the process model runtime.

    This is an explicit lifecycle/test hook; callers cannot mutate individual
    route, registry or service singletons independently.
    """
    if replacement is not None:
        from host_brain.model_runtime.composition import ModelRuntimeComposition  # noqa: PLC0415

        if not isinstance(replacement, ModelRuntimeComposition):
            raise TypeError("replacement must be ModelRuntimeComposition or None")
    with _container().model_runtime_lock:
        current = _container().model_runtime_composition
        _container().model_runtime_composition = replacement
    if current is not None and current is not replacement:
        current.attempt_service.clear_cache()


def get_security_immune_agent():
    """获取内网安全免疫智能体单例。"""
    if _container().security_immune_agent is None:
        from host_brain.security_immune.agent import get_security_immune_agent as _get_agent  # noqa: PLC0415

        _container().security_immune_agent = _get_agent(ledger_provider=get_ledger)
    return _container().security_immune_agent


def get_conversation_turn_store() -> ContextRepository:
    if _container().conversation_turn_store is None:
        _container().conversation_turn_store = ContextRepository.from_ledger(get_ledger())
    return _container().conversation_turn_store


def get_conversation_history() -> ConversationHistory:
    if _container().conversation_history is None:
        _container().conversation_history = ConversationHistory(get_conversation_turn_store())
    return _container().conversation_history


def get_session_topic_ledger_store() -> SessionTopicLedgerStore:
    if _container().session_topic_ledger_store is None:
        _container().session_topic_ledger_store = SessionTopicLedgerStore(get_ledger())
    return _container().session_topic_ledger_store


def get_session_tactic_ledger_store() -> SessionTacticLedgerStore:
    if _container().session_tactic_ledger_store is None:
        _container().session_tactic_ledger_store = SessionTacticLedgerStore(get_ledger())
    return _container().session_tactic_ledger_store


def _resolve_episodic_embedding_binding() -> tuple[Any, str, str]:
    from host_brain.memory.chroma_client import embed_text  # noqa: PLC0415

    def _embed(text: str) -> tuple[list[float], str]:
        return embed_text(str(text or "").strip())

    return _embed, "memory.chroma_client.embed_text", "memory_chroma"


def _build_episodic_embedding_callable() -> Any:
    embedder, _model_id, _provider = _resolve_episodic_embedding_binding()
    return embedder


def get_episodic_store() -> EpisodicStore:
    if _container().episodic_store is None:
        from host_brain.memory.chroma_client import (  # noqa: PLC0415
            PersistentChromaClient,
            load_chroma_runtime_config,
        )

        cfg_result = load_chroma_runtime_config(env=os.environ)
        if not cfg_result.success or cfg_result.config is None:
            raise RuntimeError(f"episodic chroma runtime config invalid: {cfg_result.as_dict()}")
        _embedding_fn, _embedding_model_id, _embedding_provider = _resolve_episodic_embedding_binding()
        _container().episodic_store = EpisodicStore(
            ledger=get_ledger(),
            chroma_client=PersistentChromaClient.from_runtime_config(cfg_result.config),
            embedding_fn=_embedding_fn,
            embedding_model_id_hint=_embedding_model_id,
            embedding_provider_hint=_embedding_provider,
        )
    return _container().episodic_store


def get_life_memory_store() -> LifeMemoryStore:
    """Legacy read/background-memory compatibility store.

    Production chat admission must use ``get_life_memory_service``.  This
    provider remains while background/action and recall slices are migrated.
    """
    ledger = get_ledger()
    if _container().life_memory_store is None or getattr(_container().life_memory_store, "ledger", None) is not ledger:
        _container().life_memory_store = LifeMemoryStore(ledger=ledger)
    return _container().life_memory_store


def set_memory_service_container(container: Any | None) -> None:
    """Install the composition-owned process Memory container."""
    if container is not None and getattr(container, "service", None) is None:
        raise TypeError("memory service container must expose .service")
    _container().memory_service_container = container


def get_memory_service_container() -> Any:
    container = _container().memory_service_container
    if container is None:
        raise RuntimeError("LIFE_MEMORY_SERVICE_NOT_COMPOSED")
    return container


def get_life_memory_service() -> Any:
    """Return the sole composition-owned production chat memory writer."""

    return get_memory_service_container().service


def get_life_snapshot_reader() -> Any:
    """Return the reader owned by the single composed Digital Life authority."""

    from host_brain.composition.life import PROCESS_LIFE_PROVIDER

    return PROCESS_LIFE_PROVIDER.require().snapshot_reader


def get_life_command_service() -> Any:
    """Return the service owned by the single composed Digital Life authority."""

    from host_brain.composition.life import PROCESS_LIFE_PROVIDER

    return PROCESS_LIFE_PROVIDER.require().command_service


def get_tribe_handoff_coordinator() -> Any:
    """Return the handoff writer from the explicitly composed Tribe authority."""

    from host_brain.composition.tribe_runtime import require_process_tribe_runtime

    return require_process_tribe_runtime().handoff_coordinator


def get_silence_engine() -> Any:
    """Return the sole process-owned emotional silence engine."""

    if _container().silence_engine is None:
        from host_brain.persona.emotional_silence import EmotionalSilenceEngine

        _container().silence_engine = EmotionalSilenceEngine()
    return _container().silence_engine


from host_brain.composition.process_runtime_services import (
    _sync_mcp_skills_into_registry,
    get_bond_aware_style_engine,
    get_bond_dynamics_writer,
    get_bond_neo4j_client,
    get_bond_stage_engine,
    get_bond_update_queue,
    get_capability_forge_service,
    get_capability_gap_telemetry,
    get_capability_scorecard_provider,
    get_distillation_engine,
    get_graph_runtime,
    get_intent_engine,
    get_mcp_tool_registry,
    get_memory_guard,
    get_memory_worth_evaluator,
    get_meta_learning_loop,
    get_milestone_tracker,
    get_multimodal_observer,
    get_multimodal_processor,
    get_obs_metrics_collector,
    get_planning_agent,
    get_policy_cache,
    get_predictive_state_engine,
    get_reflection_chain,
    get_routing_matrix,
    get_runtime_capability_registry,
    get_semantic_recaller,
    get_shared_memory_index,
    get_skill_registry,
    get_state_arbiter,
    get_state_write_arbiter,
    get_token_service,
    get_tool_broker,
    get_tool_catalog,
    get_tts_context_router,
)
