"""Authority-minimal ports for one already-admitted governed Skill node.

The Task Runtime has already selected the route and target before this boundary
is entered.  Consequently these ports contain only collaborators that can
execute the frozen Skill payload; they deliberately expose no router, planner,
replan service, task registry, or reply-delivery authority.

The execution implementation is app-local and accepts only a live governed
TaskGraph node.  No Executive or token-only compatibility executor is exposed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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


__all__ = [
    "GovernedSkillExecutionPorts",
    "build_governed_skill_execution_ports",
]
