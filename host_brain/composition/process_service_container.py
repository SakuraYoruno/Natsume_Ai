"""Process-owned service state for the Host composition root.

Only this module owns mutable process service references.  Construction lives
in the explicit composition accessors; application adapters only request
typed services and never create a second object graph.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from threading import Lock, RLock
from typing import Any


@dataclass(slots=True)
class ProcessServiceContainer:
    """The single mutable service registry owned by the Host process."""

    ledger: Any = None
    query_projection_store: Any = None
    token_service: Any = None
    intent_engine: Any = None
    skill_registry: Any = None
    policy_cache: Any = None
    capability_scorecard_provider: Any = None
    mcp_tool_registry: Any = None
    mcp_tool_broker: Any = None
    tool_catalog: Any = None
    tool_broker: Any = None
    bond_stage_engine: Any = None
    bond_dynamics_writer: Any = None
    bond_update_queue: Any = None
    episodic_store: Any = None
    life_memory_store: Any = None
    memory_service_container: Any = None
    retrieval_pipeline: Any = None
    retrieval_orchestrator: Any = None
    obs_metrics_collector: Any = None
    graph_runtime: Any = None
    routing_matrix: Any = None
    memory_guard: Any = None
    state_arbiter: Any = None
    state_write_arbiter: Any = None
    distillation_engine: Any = None
    semantic_recaller: Any = None
    context_compressor: Any = None
    emotion_timeline: Any = None
    silence_engine: Any = None
    bond_aware_style_engine: Any = None
    reflection_chain: Any = None
    tts_context_router: Any = None
    multimodal_processor: Any = None
    multimodal_observer: Any = None
    memory_worth_evaluator: Any = None
    episodic_memory_graph: Any = None
    consolidation_trigger: Any = None
    predictive_state_engine: Any = None
    meta_learning_loop: Any = None
    planning_agent: Any = None
    skill_composer: Any = None
    runtime_task_registry: Any = None
    capability_gap_telemetry: Any = None
    capability_forge_service: Any = None
    capability_forge_token_service: Any = None
    runtime_capability_registry: Any = None
    shared_memory_index: Any = None
    milestone_tracker: Any = None
    conversation_turn_store: Any = None
    conversation_history: Any = None
    session_topic_ledger_store: Any = None
    session_tactic_ledger_store: Any = None
    security_immune_agent: Any = None
    model_runtime_composition: Any = None
    capability_runtime_composition: Any = None
    hard_state: Any = None
    activity_monitor: Any = None
    resource_manager: Any = None
    application_ports: Any = None
    tribe_runtime_composition: Any = None
    model_runtime_lock: RLock = None  # type: ignore[assignment]
    capability_runtime_lock: RLock = None  # type: ignore[assignment]
    tribe_runtime_lock: RLock = None  # type: ignore[assignment]
    hard_state_lock: Lock = None  # type: ignore[assignment]
    activity_monitor_lock: Lock = None  # type: ignore[assignment]
    resource_manager_lock: Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.model_runtime_lock = RLock()
        self.capability_runtime_lock = RLock()
        self.tribe_runtime_lock = RLock()
        self.hard_state_lock = Lock()
        self.activity_monitor_lock = Lock()
        self.resource_manager_lock = Lock()

    def clear_service_references(self) -> None:
        """Reset service references while preserving synchronization objects."""

        for item in fields(self):
            if item.name.endswith("_lock") or item.name == "application_ports":
                continue
            setattr(self, item.name, None)


_PROCESS_SERVICE_CONTAINER = ProcessServiceContainer()


def get_process_service_container() -> ProcessServiceContainer:
    return _PROCESS_SERVICE_CONTAINER


__all__ = ["ProcessServiceContainer", "get_process_service_container"]
