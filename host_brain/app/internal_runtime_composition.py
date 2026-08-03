"""App-local composition for Internal, Workspace, Operator, and runtime-group ports.

The ASGI lifespan remains the sole owner of startup and shutdown sequencing.  This
module only builds and activates the object graph supplied by that lifespan; it
does not publish a process-global service locator or own a second lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from host_brain.app.host_startup_graph_continuation import (
    HostStartupGraphContinuation,
)
from host_brain.app.periodic_durable_continuation import (
    PeriodicDurableContinuation,
)
from host_brain.app.review_continuation_guardian_observer import (
    ReviewContinuationGuardianObserver,
)
from host_brain.app.surface_runtime_composition import build_surface_runtime_ports
from host_brain.app.workspace_cleanup_guardian_observer import (
    WorkspaceCleanupGuardianObserver,
)


@dataclass(frozen=True, slots=True)
class InternalRuntimeComposition:
    """Runtime objects that must be shared with Scheduler and shutdown wiring."""

    canonical_owner: str
    workspace_container: Any | None
    artifact_store: Any | None
    scheduler_workers: Any
    host_runtime: Any
    internal_runtime: Any
    operator_runtime: Any
    acceptance_effect_dispatcher: Any
    acceptance_effect_runtime: Any
    review_commands: Any
    review_continuation_observer: Any


@dataclass(frozen=True, slots=True)
class ActivatedInternalRuntime:
    """Activated command ports returned to the app-local lifespan slots."""

    runtime_group: Any
    review_commands: Any
    internal_command_service: Any


def compose_internal_runtime(
    *,
    application: Any,
    task_runtime: Any,
    runtime_settings: Any,
    ledger: Any,
    command_ingress: Any,
    tool_composition: Any,
    runtime_context_application: Any,
    action_preview_service: Any,
    cancellation_authority_verifier: Any,
    episodic_projection_store: Any,
    semantic_recaller: Any,
    mode_transition_service_provider: Callable[[], Any],
    scheduler_command_service_provider: Callable[[], Any],
    life_receipt_reader_provider: Callable[[], Any],
    life_projection_dispatcher: Any,
    life_authority_provider: Callable[[], tuple[Any, Any]],
    agent_runtime_ports: Any,
    token_service_provider: Callable[[], Any],
    intent_engine_provider: Callable[[], Any],
    model_attempt_service_provider: Callable[[], Any],
    prompt_registry_provider: Callable[[], Any],
    workspace_composer: Callable[..., Any],
    artifact_store_factory: Callable[[], Any],
    scheduler_worker_registry_factory: Callable[[], Any],
    scheduler_operation_handler_factory: Callable[..., Any],
    operator_composition_factory: Callable[..., Any],
    runtime_router_factory: Callable[[], Any],
    agent_executor_factory: Callable[..., Any],
    internal_runtime_factory: Callable[..., Any],
    embodiment_runtime_provider: Callable[[], Any] | None = None,
) -> InternalRuntimeComposition:
    """Build one app-local canonical runtime graph without activating it."""

    logical_owner = runtime_settings.task_runtime_canonical_owner or "host-task-runtime"
    canonical_owner = task_runtime.execution_owner_id(logical_owner)
    workspace_container = workspace_composer(
        application,
        task_runtime,
        runtime_settings,
        canonical_owner,
        cleanup_observer=WorkspaceCleanupGuardianObserver(
            guardian=runtime_context_application.composition.guardian,
        ),
    )
    artifact_store = artifact_store_factory() if workspace_container is not None else None
    scheduler_workers = scheduler_worker_registry_factory()
    scheduler_operation_handler = scheduler_operation_handler_factory(
        worker_registry=scheduler_workers,
        definition_repository=task_runtime.scheduler_definition_repository,
        occurrence_repository=task_runtime.scheduler_definition_repository,
        command_repository=task_runtime.command_repository,
        result_repository=task_runtime.scheduler_result_repository,
    )
    surface_runtime_ports = build_surface_runtime_ports(
        agent_runtime_ports=agent_runtime_ports,
        life_authority_provider=life_authority_provider,
    )
    workspace_runtime = (
        workspace_container.runtime if workspace_container is not None else None
    )
    operator_composition = operator_composition_factory(
        task_runtime_container=task_runtime,
        ledger=ledger,
        token_service=token_service_provider(),
        intent_engine=intent_engine_provider(),
        runtime_context_application=runtime_context_application,
        action_preview_service=action_preview_service,
        mode_transition_service_provider=mode_transition_service_provider,
        scheduler_command_service_provider=(
            scheduler_command_service_provider
        ),
        cancellation_authority_verifier=cancellation_authority_verifier,
        secure_ingress_authority_resolver=command_ingress.store,
        runtime_owner_id=canonical_owner,
        scheduler_operation_handler=scheduler_operation_handler,
        life_command_service_provider=(
            lambda: life_authority_provider()[0]
        ),
        life_receipt_reader_provider=life_receipt_reader_provider,
        life_projection_dispatcher=life_projection_dispatcher,
        relationship_lifecycle_service_provider=(
            surface_runtime_ports.get_relationship_lifecycle_service
        ),
        agent_runtime_ports=agent_runtime_ports,
        internal_agent_artifact_store=artifact_store,
        internal_agent_workspace_runtime=workspace_runtime,
        embodiment_runtime_provider=embodiment_runtime_provider,
    )
    acceptance_effect_dispatcher = operator_composition.acceptance_effect_dispatcher
    host_runtime = tool_composition.activate(
        task_runtime=task_runtime,
        ledger=ledger,
        command_ingress=command_ingress,
        owner_id=canonical_owner,
        runtime_settings=runtime_settings,
        runtime_context_application=runtime_context_application,
        cancellation_authority_verifier=cancellation_authority_verifier,
        action_preview_service=action_preview_service,
        acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        episodic_store=episodic_projection_store,
        semantic_recaller=semantic_recaller,
        agent_artifact_store=artifact_store,
        agent_workspace_runtime=workspace_runtime,
        surface_runtime_ports=surface_runtime_ports,
    )
    executor_kwargs: dict[str, Any] = {
        "model_attempt_service": model_attempt_service_provider(),
        "prompt_registry": prompt_registry_provider(),
    }
    if workspace_runtime is not None:
        executor_kwargs.update(
            workspace_runtime=workspace_runtime,
            artifact_store=artifact_store,
        )
    internal_runtime = internal_runtime_factory(
        task_runtime_container=task_runtime,
        ledger=ledger,
        token_service=token_service_provider(),
        result_repository=task_runtime.agent_result_repository,
        runtime_router=runtime_router_factory(),
        owner_id=canonical_owner,
        canonical_executor=agent_executor_factory(**executor_kwargs),
        cancellation_authority_verifier=cancellation_authority_verifier,
        runtime_context_application=runtime_context_application,
        action_preview_service=action_preview_service,
        acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        artifact_store=artifact_store,
        workspace_runtime=workspace_runtime,
        claim_result_store=command_ingress.store,
    )
    return InternalRuntimeComposition(
        canonical_owner=canonical_owner,
        workspace_container=workspace_container,
        artifact_store=artifact_store,
        scheduler_workers=scheduler_workers,
        host_runtime=host_runtime,
        internal_runtime=internal_runtime,
        operator_runtime=operator_composition.runtime,
        acceptance_effect_dispatcher=acceptance_effect_dispatcher,
        acceptance_effect_runtime=operator_composition.acceptance_effect_runtime,
        review_commands=operator_composition.review_command_service,
        review_continuation_observer=ReviewContinuationGuardianObserver(
            guardian=runtime_context_application.composition.guardian,
        ),
    )


async def activate_internal_runtime(
    *,
    application: Any,
    task_runtime: Any,
    command_ingress: Any,
    security: Any,
    scheduler: Any,
    composition: InternalRuntimeComposition,
    cancellation_operator_enabled: bool,
    operation_reconciliation_operator_enabled: bool,
    effect_reconciliation_operator_enabled: bool,
    terminal_reconciliation_operator_enabled: bool,
    terminal_reconciliation_read_authority: Any,
    memory: Any,
    intent_engine_provider: Callable[[], Any],
    runtime_group_factory: Callable[..., Any],
    internal_command_service_factory: Callable[..., Any],
    activate_scheduler_clock: bool = True,
) -> ActivatedInternalRuntime:
    """Bind and activate the canonical group, then publish its app-local ports."""

    runtime_group = runtime_group_factory(
        {
            "host": composition.host_runtime,
            "internal_agent": composition.internal_runtime,
            "scheduler": scheduler.runtime,
            "operator": composition.operator_runtime,
        },
        review_store=task_runtime.review_continuation_store,
        review_decision_store=task_runtime.review_store,
        command_repository=task_runtime.command_repository,
        claim_result_store=command_ingress.store,
        operator_outcome_finalizer=getattr(
            getattr(security, "obs_command_composition", None),
            "ingress",
            None,
        ),
        review_continuation_worker_id=(
            f"{task_runtime.recovery_id}::review-continuation"
        ),
        review_continuation_observer=(
            composition.review_continuation_observer
        ),
        graph_cancellation_operator_enabled=cancellation_operator_enabled,
        graph_cancellation_state_reader=task_runtime.task_store,
        acceptance_effect_runtime=composition.acceptance_effect_runtime,
        operation_reacquisition_owner_id=composition.canonical_owner,
        startup_task_store=task_runtime.task_store,
        startup_continuation_factory=HostStartupGraphContinuation,
        startup_survivor_factory=PeriodicDurableContinuation,
    )
    review_commands = composition.review_commands
    bind_review_wake = getattr(
        review_commands,
        "bind_wake_continuation",
        None,
    )
    if not callable(bind_review_wake):
        raise TypeError(
            "app-local Review command service lacks continuation binding"
        )
    bind_review_wake(runtime_group.wake_review_continuation)
    bind_reacquisition_owner = getattr(
        task_runtime.operation_reconciliation_service,
        "bind_execution_owner",
        None,
    )
    if not callable(bind_reacquisition_owner):
        raise TypeError(
            "Operation reconciliation service lacks execution-owner binding"
        )
    bind_reacquisition_owner(composition.canonical_owner)
    task_runtime.bind_runtime(runtime_group)
    await task_runtime.activate_runtime()
    await composition.host_runtime.recover_startup_graphs()
    await runtime_group.recover_startup_graphs()
    if activate_scheduler_clock:
        await scheduler.activate_clock()
    internal_command_service = internal_command_service_factory(
        runtime=composition.internal_runtime,
        intent_engine=intent_engine_provider(),
        claim_result_store=command_ingress.store,
    )

    application.state.task_runtime_container = task_runtime
    application.state.governed_host_runtime = composition.host_runtime
    application.state.governed_internal_runtime = composition.internal_runtime
    application.state.governed_scheduler_runtime = scheduler.runtime
    application.state.governed_operator_runtime = composition.operator_runtime
    application.state.scheduler_container = scheduler
    application.state.task_review_command_service = review_commands
    application.state.task_graph_cancellation_command_service = runtime_group
    application.state.task_graph_cancellation_operator_enabled = bool(
        cancellation_operator_enabled
    )
    application.state.task_operation_reacquisition_continuation = (
        runtime_group if operation_reconciliation_operator_enabled else None
    )
    application.state.task_operation_reconciliation_command_service = (
        task_runtime.operation_reconciliation_service
        if operation_reconciliation_operator_enabled
        else None
    )
    application.state.task_operation_reconciliation_operator_enabled = bool(
        operation_reconciliation_operator_enabled
    )
    application.state.task_effect_reconciliation_command_service = (
        task_runtime.effect_reconciliation_service
        if effect_reconciliation_operator_enabled
        else None
    )
    application.state.task_effect_reconciliation_operator_enabled = bool(
        effect_reconciliation_operator_enabled
    )
    application.state.task_terminal_reconciliation_operator_enabled = bool(
        terminal_reconciliation_operator_enabled
    )
    application.state.task_terminal_reconciliation_read_authority = (
        terminal_reconciliation_read_authority
    )
    application.state.command_ingress_container = command_ingress
    application.state.memory_service_container = memory
    return ActivatedInternalRuntime(
        runtime_group=runtime_group,
        review_commands=review_commands,
        internal_command_service=internal_command_service,
    )


__all__ = [
    "ActivatedInternalRuntime",
    "InternalRuntimeComposition",
    "activate_internal_runtime",
    "compose_internal_runtime",
]
