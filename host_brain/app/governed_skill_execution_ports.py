"""Authority-minimal ports for one already-admitted governed Skill node.

The Task Runtime has already selected the route and target before this boundary
is entered.  Consequently these ports contain only collaborators that can
execute the frozen Skill payload; they deliberately expose no router, planner,
replan service, task registry, or reply-delivery authority.

The legacy low-level Skill executor remains shared while Executive compatibility
routes still exist.  Its private entry points are hidden behind the functions in
this module so the governed adapter never has to compose the full Executive
object graph.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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

    def __post_init__(self) -> None:
        if not callable(self.production_authority_verifier):
            raise TypeError("production_authority_verifier must be callable")
        if not isinstance(
            self.degradation_capability_port,
            DegradationCapabilityPort,
        ):
            raise TypeError("degradation_capability_port must be injected")

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
        degradation_capability_port=degradation_capability_port,
    )


def execute_frozen_governed_skill(
    *,
    ports: GovernedSkillExecutionPorts,
    durable_authority_ledger: Any,
    request_body: Any,
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
) -> Any:
    """Run one frozen Skill without constructing Executive application ports."""

    from host_brain.executive_runtime.skill_execution import (  # noqa: PLC0415
        _execute_skill,
    )

    return _execute_skill(
        ports=ports,
        request_body=request_body,
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
        skill_registry=ports.skill_registry,
        ledger=False,
        durable_authority_ledger=durable_authority_ledger,
        capability_broker_override=ports.capability_broker,
        model_attempt_service_override=ports.model_attempt_service,
        production_authority_verifier=ports.production_authority_verifier,
        governed_execution_context=governed_execution_context,
        execution_args_override=execution_args_override,
        verified_node_grant=verified_node_grant,
        degradation_capability_port=ports.degradation_capability_port,
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
) -> None:
    """Persist the worker-thread result on the ledger-owning event-loop thread."""

    from host_brain.executive_runtime.skill_execution import (  # noqa: PLC0415
        _log_skill_exec_row,
    )

    _log_skill_exec_row(
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
    )


__all__ = [
    "GovernedSkillExecutionPorts",
    "build_governed_skill_execution_ports",
    "execute_frozen_governed_skill",
    "record_governed_skill_execution",
]
