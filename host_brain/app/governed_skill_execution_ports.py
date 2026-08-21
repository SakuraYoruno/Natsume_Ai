"""Authority-minimal ports for one already-admitted governed Skill node.

The Task Runtime has already selected the route and target before this boundary
is entered.  Consequently these ports contain only collaborators that can
execute the frozen Skill payload; they deliberately expose no router, planner,
replan service, task registry, or reply-delivery authority.

The execution implementation is app-local and accepts only a live governed
TaskGraph node.  No Executive or token-only compatibility executor is exposed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from host_brain.app.governed_skill_execution import (
    execute_governed_skill,
    record_governed_skill_execution as _record_governed_skill_execution,
)
from host_brain.runtime.degradation_authority import DegradationCapabilityPort


@dataclass(frozen=True, slots=True)
class GovernedSkillExecutionPorts:
    """Immutable collaborators allowed to execute a frozen Skill node."""

    skill_registry: Any
    capability_broker: Any
    model_attempt_service: Any
    production_authority_verifier: Callable[..., Any]
    resource_manager: Any | None
    episodic_store: Any | None
    semantic_recaller: Any | None
    degradation_capability_port: DegradationCapabilityPort
    agent_artifact_store: Any | None = None
    agent_workspace_runtime: Any | None = None
    knowledge_graph_neo4j_client_provider: Callable[[], Any | None] | None = None
    pentest_skill_runtime: Any | None = None

    def __post_init__(self) -> None:
        if not callable(self.production_authority_verifier):
            raise TypeError("production_authority_verifier must be callable")
        if not isinstance(
            self.degradation_capability_port,
            DegradationCapabilityPort,
        ):
            raise TypeError("degradation_capability_port must be injected")
        if (self.agent_artifact_store is None) != (
            self.agent_workspace_runtime is None
        ):
            raise TypeError(
                "Agent artifact store and Workspace runtime must be composed together"
            )

    # These method names are the structural subset consumed by the shared
    # low-level executor for resource and memory Skills.
    def get_resource_manager(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return self.resource_manager

    def get_episodic_store(self) -> Any:
        return self.episodic_store

    def get_semantic_recaller(self) -> Any:
        return self.semantic_recaller


def build_governed_skill_execution_ports(
    *,
    skill_registry: Any,
    capability_broker: Any,
    model_attempt_service: Any,
    production_authority_verifier: Callable[..., Any],
    resource_manager: Any | None,
    episodic_store: Any | None,
    semantic_recaller: Any | None,
    degradation_capability_port: DegradationCapabilityPort,
    agent_artifact_store: Any | None = None,
    agent_workspace_runtime: Any | None = None,
    knowledge_graph_neo4j_client_provider: Callable[[], Any | None] | None = None,
    pentest_skill_runtime: Any | None = None,
) -> GovernedSkillExecutionPorts:
    """Compose only services usable after Skill route admission is complete."""

    return GovernedSkillExecutionPorts(
        skill_registry=skill_registry,
        capability_broker=capability_broker,
        model_attempt_service=model_attempt_service,
        production_authority_verifier=production_authority_verifier,
        resource_manager=resource_manager,
        episodic_store=episodic_store,
        semantic_recaller=semantic_recaller,
        agent_artifact_store=agent_artifact_store,
        agent_workspace_runtime=agent_workspace_runtime,
        knowledge_graph_neo4j_client_provider=(
            knowledge_graph_neo4j_client_provider
        ),
        pentest_skill_runtime=pentest_skill_runtime,
        degradation_capability_port=degradation_capability_port,
    )


def execute_frozen_governed_skill(
    *,
    ports: GovernedSkillExecutionPorts,
    durable_authority_ledger: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str,
    skill_id: str,
    task_text: str,
    user_message: str,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
    governed_execution_context: Mapping[str, Any],
    execution_args_override: Mapping[str, Any],
    verified_node_grant: Mapping[str, Any],
    frozen_executor_payload: Mapping[str, Any] | None = None,
) -> Any:
    """Run one frozen Skill under exact durable node authority."""

    return execute_governed_skill(
        ports=ports,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        intent_id=intent_id,
        skill_id=skill_id,
        task_text=task_text,
        user_message=user_message,
        governor_result=governor_result,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        render_target_lang=render_target_lang,
        durable_authority_ledger=durable_authority_ledger,
        governed_execution_context=governed_execution_context,
        execution_args=execution_args_override,
        verified_node_grant=verified_node_grant,
        frozen_executor_payload=frozen_executor_payload,
    )


def record_governed_skill_execution(
    *,
    ledger: Any,
    request_id: str,
    intent_id: str,
    skill_name: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    approval_token_ref: str | None,
    ts_ms: int,
    evidence_ref: str | None,
    manifest_ref: str | None = None,
) -> None:
    """Persist the worker-thread result on the ledger-owning event-loop thread."""

    _record_governed_skill_execution(
        request_id=request_id,
        intent_id=intent_id,
        skill_name=skill_name,
        result_status=result_status,
        result_code=result_code,
        result_digest=result_digest,
        approval_token_ref=approval_token_ref,
        ts_ms=ts_ms,
        ledger=ledger,
        evidence_ref=evidence_ref,
        manifest_ref=manifest_ref,
    )


__all__ = [
    "GovernedSkillExecutionPorts",
    "build_governed_skill_execution_ports",
    "execute_frozen_governed_skill",
    "record_governed_skill_execution",
]
