"""ASGI entrypoint for the host main service."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Callable, Mapping

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from host_brain.app.app_factory import create_host_fastapi_app
from host_brain.app.asgi_endpoint_helpers import root_endpoint
from host_brain.app.agent_runtime_composition import (
    AgentRuntimeApplicationScope,
)
from host_brain.app.chat_handler import handle_chat
from host_brain.app.chat_immune_gate import evaluate_immune_gate_for_chat, schedule_chat_immune_gate_event
from host_brain.app.chat_routes import handle_v1_chat_route
from host_brain.app.asgi_runtime_helpers import (
    build_scheduler_seed_configs,
    dispatch_edge_events,
)
from host_brain.app.http_hooks import runtime_lifespan
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.app.host_lifecycle_composition import (
    build_host_lifecycle_container,
)
from host_brain.app import host_life_scheduler_wiring as _scheduler_result_wiring
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.host_life_scheduler_wiring import (
    LifeCommandServicePort as _LifeCommandServicePort,
    LifeSnapshotReaderPort as _LifeSnapshotReaderPort,
    compose_life_command_authority,
    compose_life_dispatchers as _compose_life_dispatchers,
    reset_life_command_authority,
    submit_life_tick_command,
)
from host_brain.app.host_runtime_context_wiring import (
    bind_host_hard_state_runtime as _bind_host_hard_state_runtime,
    compose_action_preview_service as _compose_action_preview_service,
    compose_host_runtime_context as _compose_host_runtime_context,
    compose_service_catalog_guardian_observation as _compose_service_catalog_guardian_observation,
    observe_runtime_startup as _observe_runtime_startup,
    runtime_environment_observation_provider as _runtime_environment_observation_provider,
)
from host_brain.app.internal_runtime_composition import (
    activate_internal_runtime as _activate_internal_runtime,
    compose_internal_runtime as _compose_internal_runtime,
)
from host_brain.app.host_runtime_startup_guards import (
    assert_graph_cancellation_operator_startup as _assert_graph_cancellation_operator_startup,
    assert_graph_cancellation_reconciliation_production_ready as _assert_graph_cancellation_reconciliation_production_ready,
    assert_task_effect_reconciliation_operator_startup as _assert_task_effect_reconciliation_operator_startup,
    assert_task_operation_reconciliation_operator_startup as _assert_task_operation_reconciliation_operator_startup,
    assert_task_terminal_reconciliation_operator_startup as _assert_task_terminal_reconciliation_operator_startup,
    command_ingress_hmac_secrets,
)
from host_brain.app.host_runtime_shutdown import (
    shutdown_host_runtime_authority,
    withdraw_published_runtime_state,
)
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.host_obs_source_composition import (
    HostObsSourceEpoch,
    HostObsSourceRuntimePorts,
    build_host_obs_read_ports,
)
from host_brain.app.process_service_wiring import configure_process_service_ports
from host_brain.lifecycle_runtime.proactive_dispatch import run_proactive_intent_dispatch
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse
from host_brain.app.tool_runtime_composition import (
    GovernedHostToolRuntimeComposition,
    GovernedHostToolRuntimeDependencies,
)
from host_brain.app.internal_agent_ingress import InternalAgentIngress
from host_brain.app.internal_task_runtime import (
    GovernedInternalRuntime,
    InternalAgentCommandService,
    build_governed_internal_runtime,
)
from host_brain.app.governed_operator_runtime import (
    GovernedOperatorRuntime,
    build_governed_operator_composition,
)
from host_brain.app.memory_policy_adapter import ExistingMemoryGuardAdapter
from host_brain.app.settings import (
    get_core_settings,
    get_persistence_settings,
    get_scheduler_settings,
)
from host_brain.app.settings.input import get_input_settings
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.core.ids import now_ms
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.app.security_services_composition import (
    ProcessSecurityServiceContainer,
    build_process_security_service_container,
)
from host_brain.composition.command_ingress import (
    ProcessCommandIngressContainer,
    build_process_command_ingress_container,
)
from host_brain.composition.canon import (
    ProcessCanonContainer,
    build_process_canon_container,
)
from host_brain.composition.memory import (
    ProcessMemoryContainer,
    build_process_memory_container,
)
from host_brain.composition.host_application import (
    HostApplicationPorts,
    build_production_host_application_ports,
)
from host_brain.composition import agent_workspace as workspace
from host_brain.composition.life import PROCESS_LIFE_PROVIDER, build_process_life_container
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.composition.scheduler import (
    ProcessSchedulerContainer,
    SchedulerClockIdentityConfig,
    build_process_scheduler_container,
)
from host_brain.scheduler.accepted_operation import (
    SchedulerAcceptedOperationHandler,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.composition.task_runtime import (
    ProcessTaskRuntimeContainer,
    build_process_task_runtime_container,
)
from host_brain.task_runtime.review_command_service import TaskReviewCommandService
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.composition.tribe_runtime import require_process_tribe_runtime
from host_brain.runtime.context_application import (
    RuntimeContextApplicationService,
)
from host_brain.runtime.production_composition import (
    RuntimeContextComposition,
)


logger = logging.getLogger("Natsume.asgi")
runtime_metrics = RuntimeMetrics()


def _compose_life_command_authority(
    ledger: Any,
) -> tuple[_LifeCommandServicePort, _LifeSnapshotReaderPort]:
    return compose_life_command_authority(
        ledger,
        container_builder=build_process_life_container,
        provider=PROCESS_LIFE_PROVIDER,
        clock_ms=now_ms,
    )


def _reset_life_command_authority() -> None:
    reset_life_command_authority(provider=PROCESS_LIFE_PROVIDER)


def _submit_life_tick_command(
    *,
    occurrence: Any,
    definition: Any,
    command: Any,
    execution_context: Mapping[str, Any],
    runtime_context_application: RuntimeContextApplicationService,
    life_command_service: _LifeCommandServicePort,
    life_snapshot_reader: _LifeSnapshotReaderPort,
) -> dict[str, Any]:
    return submit_life_tick_command(
        occurrence=occurrence,
        definition=definition,
        command=command,
        execution_context=execution_context,
        runtime_context_application=runtime_context_application,
        life_command_service=life_command_service,
        life_snapshot_reader=life_snapshot_reader,
        clock_ms=now_ms,
    )


def _command_ingress_hmac_secrets() -> dict[str, str]:
    return command_ingress_hmac_secrets(
        input_settings=get_input_settings(),
        scheduler_settings=get_scheduler_settings(),
    )


def create_app(
    *,
    host_application_ports: HostApplicationPorts | None = None,
) -> FastAPI:
    from host_brain.app.tribe_composition import build_tribe_application_ports

    configure_process_service_ports()
    application_ports = (
        host_application_ports
        if host_application_ports is not None
        else build_production_host_application_ports()
    )
    if not isinstance(application_ports, HostApplicationPorts):
        raise TypeError("host_application_ports must be HostApplicationPorts")
    host_lifecycle = build_host_lifecycle_container(
        initial_sys_mode=get_core_settings().sys_mode,
        ports=application_ports.lifecycle,
        require_durable_hard_state=True,
    )
    host_services = host_lifecycle.ports
    ledger_provider = host_services.require_provider("ledger")
    intent_engine_provider = host_services.require_provider("intent_engine")
    prompt_registry_provider = host_services.require_provider("prompt_registry")
    life_snapshot_reader_provider = host_services.require_provider(
        "life_snapshot_reader"
    )
    bond_engine_provider = host_services.require_provider("bond_stage_engine")
    agent_runtime_scope = AgentRuntimeApplicationScope(host_services=host_services)

    tribe_application_ports = build_tribe_application_ports(
        host_services=host_services,
        activity_monitor_provider=host_lifecycle.get_activity_monitor,
        hard_state_provider=host_lifecycle.get_hard_state,
        mode_transition_service_provider=(
            host_lifecycle.get_mode_transition_service
        ),
        resource_manager_provider=host_lifecycle.get_resource_manager,
    )
    lifecycle_runtime_ports = build_lifecycle_runtime_application_ports(
        host_services=host_services,
        activity_monitor_provider=host_lifecycle.get_activity_monitor,
        endocrine_state_provider=host_lifecycle.get_endocrine_state,
        episodic_store_provider=host_services.episodic_store,
        hard_state_provider=host_lifecycle.get_hard_state,
        model_attempt_service_provider=host_services.model_attempt_service,
    )
    input_runtime_ports = build_input_runtime_application_ports(
        host_services=host_services,
        embodiment_runtime_provider=lambda: embodiment_runtime,
        notify_user_interaction_callback=(
            host_lifecycle.notify_user_interaction
        ),
        agent_runtime_ports_provider=agent_runtime_scope.provider,
    )
    # This slot belongs to one FastAPI application factory invocation.  Route
    # providers close over it explicitly; there is no module-global service
    # locator and no app-state lookup inside the API registry.
    security_container: ProcessSecurityServiceContainer | None = None
    command_ingress_container: ProcessCommandIngressContainer | None = None
    memory_container: ProcessMemoryContainer | None = None
    task_runtime_container: ProcessTaskRuntimeContainer | None = None
    agent_workspace_container: workspace.ProcessAgentWorkspaceContainer | None = None
    tool_composition = GovernedHostToolRuntimeComposition(
        GovernedHostToolRuntimeDependencies(
            skill_registry=host_services.skill_registry,
            token_service=application_ports.token_service,
            capability_broker=host_services.capability_broker,
            resource_manager=host_lifecycle.get_resource_manager,
            ledger=ledger_provider,
            tool_catalog=host_services.tool_catalog,
            intent_engine=intent_engine_provider,
            model_attempt_service=host_services.model_attempt_service,
        )
    )
    governed_internal_runtime: GovernedInternalRuntime | None = None
    governed_operator_runtime: GovernedOperatorRuntime | None = None
    internal_agent_command_service: InternalAgentCommandService | None = None
    task_review_command_service: TaskReviewCommandService | None = None
    internal_agent_ingress: InternalAgentIngress | None = None
    scheduler_container: ProcessSchedulerContainer | None = None
    host_obs_ledger: Any | None = None
    host_query_projection_store: QueryProjectionStore | None = None
    host_obs_capability_gap_telemetry: Any | None = None
    host_obs_capability_forge: Any | None = None
    host_obs_semantic_recaller: Any | None = None
    host_obs_capability_scorecards: Any | None = None
    host_obs_degradation_state_provider: Callable[[], Mapping[str, Any] | None] = (
        lambda: None
    )
    host_obs_pentest_environment: Mapping[str, Any] = {}
    host_obs_tribe_state_reader: Callable[[], Mapping[str, Any]] | None = None
    host_obs_source_epoch: HostObsSourceEpoch | None = None
    canon_container: ProcessCanonContainer | None = None
    life_command_authority: (
        tuple[_LifeCommandServicePort, _LifeSnapshotReaderPort] | None
    ) = None
    life_dispatchers: tuple[Any, Any] | None = None
    embodiment_runtime: Any | None = None
    embodiment_authority_task: asyncio.Task[None] | None = None
    runtime_context_composition: RuntimeContextComposition | None = None
    runtime_context_application: RuntimeContextApplicationService | None = None
    guardian_heartbeat_task: asyncio.Task[None] | None = None
    catalog_rollout_guardian_task: asyncio.Task[None] | None = None
    startup_probe_statuses: dict[str, Any] = {}

    def capture_startup_probe_statuses(statuses: Mapping[str, Any]) -> None:
        nonlocal startup_probe_statuses
        startup_probe_statuses = dict(statuses)

    def task_runtime_container_provider() -> ProcessTaskRuntimeContainer | None:
        return task_runtime_container

    def degradation_capability_port_provider() -> Any | None:
        current = runtime_context_composition
        return current.guardian if current is not None else None

    def internal_agent_ingress_provider() -> InternalAgentIngress | None:
        return internal_agent_ingress

    def internal_agent_command_service_provider() -> InternalAgentCommandService | None:
        return internal_agent_command_service

    def scheduler_command_ingress_provider() -> Any | None:
        current = scheduler_container
        return current.command_ingress if current is not None else None

    def scheduler_command_service_provider() -> Any | None:
        current = scheduler_container
        return current.command_service if current is not None else None

    def read_host_obs_tribe_state() -> Mapping[str, Any]:
        reader = host_obs_tribe_state_reader
        if reader is None:
            raise RuntimeError("Host Tribe source is not active")
        return reader()

    def canon_service_provider() -> Any | None:
        current = canon_container
        return current.application if current is not None else None

    def embodiment_runtime_provider() -> Any | None:
        return embodiment_runtime

    route_ports = HostRoutePorts(
        canon_service_provider=canon_service_provider,
        tool_broker_provider=tool_composition.provide_tool_broker,
        tool_catalog_provider=host_services.tool_catalog,
        governed_tool_application_provider=tool_composition.provide_tool_application,
        bond_engine_provider=bond_engine_provider,
        bond_writer_provider=application_ports.bond_dynamics_writer,
        mode_transition_service_provider=(
            host_lifecycle.get_mode_transition_service
        ),
        embodiment_runtime_provider=embodiment_runtime_provider,
    )
    host_obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: host_query_projection_store,
    )

    host_obs_read_ports = build_host_obs_read_ports(
        HostObsSourceRuntimePorts(
            mode_service_provider=host_lifecycle.get_mode_transition_service,
            capability_gap_telemetry_provider=(
                lambda: host_obs_capability_gap_telemetry
            ),
            capability_forge_provider=lambda: host_obs_capability_forge,
            semantic_recaller_provider=lambda: host_obs_semantic_recaller,
            capability_scorecard_provider=(
                lambda: host_obs_capability_scorecards
            ),
            degradation_state_provider=(
                lambda: host_obs_degradation_state_provider()
            ),
            pentest_environment_provider=(
                lambda: dict(host_obs_pentest_environment)
            ),
            ledger_provider=lambda: host_obs_ledger,
            scheduler_container_provider=lambda: scheduler_container,
            tribe_state_reader=read_host_obs_tribe_state,
            source_epoch_provider=lambda: host_obs_source_epoch,
            clock_ms=now_ms,
        )
    )

    def life_command_authority_provider(
        ledger: Any,
    ) -> tuple[_LifeCommandServicePort, _LifeSnapshotReaderPort]:
        nonlocal life_command_authority
        if life_command_authority is None:
            life_command_authority = _compose_life_command_authority(ledger)
        return life_command_authority

    async def handle_chat_with_runtime(
        request_body: ChatRequest,
        request: Request,
        metrics: RuntimeMetrics,
    ) -> StreamingResponse:
        return await handle_chat(
            request_body,
            request,
            metrics,
            governed_command_application=tool_composition.provide_runtime(),
            hard_state_provider=host_lifecycle.get_hard_state,
            cortex_orchestrator_sse_generator=(
                input_runtime_ports.cortex_orchestrator_sse_generator
            ),
        )

    async def chat_endpoint_with_runtime(
        request_body: ChatRequest,
        request: Request,
    ) -> StreamingResponse:
        return await handle_v1_chat_route(
            request_body=request_body,
            request=request,
            metrics=runtime_metrics,
            logger=logger,
            evaluate_immune_gate_fn=evaluate_immune_gate_for_chat,
            schedule_immune_gate_fn=partial(
                schedule_chat_immune_gate_event,
                get_ledger=ledger_provider,
                logger=logger,
            ),
            handle_chat_fn=handle_chat_with_runtime,
            notify_user_interaction_fn=(
                host_lifecycle.notify_user_interaction
            ),
        )

    async def edge_events_endpoint_with_runtime(
        request_body: EdgeEventsRequest,
    ) -> EdgeEventsResponse:
        # Keep the FastAPI signature to one request body. ``functools.partial``
        # exposes the bound ports dataclass as a second body parameter.
        return await dispatch_edge_events(
            request_body,
            input_runtime_ports=input_runtime_ports,
            ledger_provider=ledger_provider,
            logger=logger,
        )

    def obs_command_ingress_provider():
        current = security_container
        composition = (
            current.obs_command_composition if current is not None else None
        )
        if composition is None or not composition.enabled:
            return None
        return composition.ingress

    def pentest_secure_run_composition_provider():
        current = security_container
        return (
            current.pentest_secure_run_composition
            if current is not None
            else None
        )

    def signed_skill_provider_intake_provider():
        current = security_container
        composition = (
            current.signed_skill_provider_intake_composition
            if current is not None
            else None
        )
        if composition is None or not composition.enabled:
            return None
        return composition.ingress

    @asynccontextmanager
    async def app_lifespan(application: FastAPI):
        nonlocal security_container, command_ingress_container, memory_container
        nonlocal task_runtime_container, agent_workspace_container
        nonlocal governed_internal_runtime, governed_operator_runtime
        nonlocal internal_agent_command_service, task_review_command_service
        nonlocal internal_agent_ingress, scheduler_container
        nonlocal host_obs_ledger, host_query_projection_store
        nonlocal host_obs_capability_gap_telemetry, host_obs_capability_forge
        nonlocal host_obs_semantic_recaller, host_obs_capability_scorecards
        nonlocal host_obs_degradation_state_provider, host_obs_pentest_environment
        nonlocal host_obs_tribe_state_reader, host_obs_source_epoch, canon_container
        nonlocal life_command_authority, life_dispatchers
        nonlocal embodiment_runtime, embodiment_authority_task
        nonlocal runtime_context_composition, runtime_context_application
        nonlocal guardian_heartbeat_task, catalog_rollout_guardian_task
        nonlocal startup_probe_statuses
        async with runtime_lifespan(
            get_ledger=ledger_provider,
            logger=logger,
            lifecycle_ports=lifecycle_runtime_ports,
            host_lifecycle=host_lifecycle,
            tribe_application_ports=tribe_application_ports,
            canon_service_provider=canon_service_provider,
            startup_probe_sink=capture_startup_probe_statuses,
            degradation_state_provider=(
                lambda: host_obs_degradation_state_provider()
            ),
            tool_broker_provider=tool_composition.provide_tool_broker,
            tool_catalog_provider=host_services.tool_catalog,
            skill_registry_provider=host_services.skill_registry,
            start_service_heartbeat=False,
            defer_activity_monitor=True,
        ):
            security: ProcessSecurityServiceContainer | None = None
            command_ingress: ProcessCommandIngressContainer | None = None
            memory: ProcessMemoryContainer | None = None
            task_runtime: ProcessTaskRuntimeContainer | None = None
            scheduler: ProcessSchedulerContainer | None = None
            canon: ProcessCanonContainer | None = None

            def withdraw_closed_process_references() -> None:
                nonlocal security_container, command_ingress_container
                nonlocal memory_container, task_runtime_container
                nonlocal agent_workspace_container, scheduler_container
                nonlocal governed_internal_runtime, governed_operator_runtime
                nonlocal internal_agent_ingress, internal_agent_command_service
                nonlocal task_review_command_service, canon_container
                nonlocal host_obs_source_epoch, host_obs_tribe_state_reader
                nonlocal host_obs_capability_forge, host_obs_capability_gap_telemetry
                nonlocal host_obs_semantic_recaller, host_obs_capability_scorecards
                nonlocal host_obs_degradation_state_provider
                nonlocal host_obs_pentest_environment, host_obs_ledger
                nonlocal host_query_projection_store, life_command_authority
                nonlocal life_dispatchers, embodiment_runtime
                nonlocal embodiment_authority_task, runtime_context_application
                nonlocal runtime_context_composition
                security_container = command_ingress_container = None
                memory_container = task_runtime_container = None
                agent_workspace_container = scheduler_container = None
                governed_internal_runtime = governed_operator_runtime = None
                internal_agent_ingress = internal_agent_command_service = None
                task_review_command_service = canon_container = None
                host_obs_source_epoch = host_obs_tribe_state_reader = None
                host_obs_capability_forge = host_obs_capability_gap_telemetry = None
                host_obs_semantic_recaller = host_obs_capability_scorecards = None
                host_obs_degradation_state_provider = lambda: None
                host_obs_pentest_environment = {}
                host_obs_ledger = host_query_projection_store = None
                life_command_authority = life_dispatchers = None
                agent_runtime_scope.clear()
                application.state.agent_runtime_application_ports = None
                embodiment_runtime = embodiment_authority_task = None
                runtime_context_application = runtime_context_composition = None

            try:
                ledger = ledger_provider()
                security = build_process_security_service_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                    token_service=application_ports.token_service(),
                    route_ports=route_ports,
                    degradation_capability_port_provider=(
                        degradation_capability_port_provider
                    ),
                    defer_obs_review_recovery=True,
                )
            except Exception:
                logger.error(
                    "HOST_SECURITY_SERVICE_CONTAINER_CONSTRUCTION_FAILED",
                    exc_info=True,
                )
            security_container = security
            application.state.security_service_container = security
            try:
                # This is the fail-closed durable startup gate.  The canonical
                # migrations and process-owned stores must be ready before the
                # ASGI lifespan yields and routes become available.
                ledger = ledger_provider()
                # Host source readers may observe these services, but never
                # construct them from a GET request.
                host_obs_ledger = ledger
                host_obs_source_epoch = HostObsSourceEpoch.create()
                host_obs_tribe_state_reader = (
                    require_process_tribe_runtime().state_registry.snapshot
                )
                try:
                    host_obs_capability_gap_telemetry = (
                        application_ports.capability_gap_telemetry()
                    )
                    host_obs_capability_forge = (
                        application_ports.capability_forge_service()
                    )
                except Exception:
                    logger.warning(
                        "HOST_OBS_CAPABILITY_SOURCE_PREWARM_FAILED",
                        exc_info=True,
                    )
                try:
                    host_obs_semantic_recaller = host_services.semantic_recaller()
                except Exception:
                    logger.warning(
                        "HOST_OBS_SEMANTIC_RECALL_SOURCE_PREWARM_FAILED",
                        exc_info=True,
                    )
                try:
                    host_obs_capability_scorecards = (
                        application_ports.capability_scorecard_provider()
                    )
                except Exception:
                    logger.warning(
                        "HOST_OBS_GOVERNANCE_SCORECARD_SOURCE_PREWARM_FAILED",
                        exc_info=True,
                    )
                truthy = {"1", "true", "yes", "on"}
                host_obs_pentest_environment = {
                    "lab_mode": str(
                        os.environ.get("PENTEST_WEB_LAB_MODE") or ""
                    ).strip().lower()
                    in truthy,
                    "allow_exploit": str(
                        os.environ.get("PENTEST_ALLOW_EXPLOIT") or ""
                    ).strip().lower()
                    in truthy,
                    "domain_mode": "domain",
                }
                runtime_settings = get_core_settings()
                authenticated_outcome_lookup = None
                if security is not None:
                    obs_composition = security.obs_command_composition
                    outcome_journal = (
                        obs_composition.outcome_journal
                        if obs_composition is not None
                        else None
                    )
                    lookup = getattr(outcome_journal, "lookup", None)
                    if callable(lookup):
                        authenticated_outcome_lookup = lookup
                task_runtime = build_process_task_runtime_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                    reconciliation_public_keys=(
                        runtime_settings.task_reconciliation_public_keys()
                    ),
                    feature_canary_public_keys=(
                        runtime_settings.task_runtime_canary_public_keys()
                    ),
                    terminal_reconciliation_auto_enabled=bool(
                        getattr(
                            runtime_settings,
                            "task_terminal_reconciliation_auto_enabled",
                            False,
                        )
                    ),
                    authenticated_outcome_lookup=authenticated_outcome_lookup,
                )
                obs_composition = (
                    security.obs_command_composition
                    if security is not None
                    else None
                )
                if obs_composition is not None:
                    obs_composition.recover_deferred_review_admissions(
                        command_repository=task_runtime.command_repository,
                        task_store=task_runtime.task_store,
                        review_store=task_runtime.review_store,
                        review_continuation_store=(
                            task_runtime.review_continuation_store
                        ),
                    )
                # Digital Life repositories are migration-gated.  The task
                # runtime owns canonical migration application, so Life must
                # be composed only after that startup gate has completed.
                life_command_authority = _compose_life_command_authority(ledger)
                agent_runtime_ports = agent_runtime_scope.initialize(
                    life_authority_provider=(
                        lambda: life_command_authority_provider(ledger)
                    ),
                )
                application.state.agent_runtime_application_ports = (
                    agent_runtime_ports
                )
                # ``QueryProjectionStore`` requires migration 0082.  The task
                # runtime is the startup migration authority, so constructing
                # this read model before the runtime above would make a fresh
                # database fail for the wrong reason and bypass the canonical
                # migration error contract.
                host_query_projection_store = QueryProjectionStore(
                    str(task_runtime.canonical_db_path)
                )
                terminal_reconciliation_read_authority = None
                if bool(getattr(runtime_settings, "nle_operator_views_enabled", False)):
                    from host_brain.persistence.reconciliation_proof_verifier import (  # noqa: PLC0415
                        Ed25519ReconciliationProofVerifier,
                    )
                    from host_brain.persistence.terminal_reconciliation_query_repository import (  # noqa: PLC0415
                        TerminalReconciliationQueryRepository,
                    )

                    read_keys = runtime_settings.task_reconciliation_public_keys()
                    terminal_reconciliation_read_authority = (
                        TerminalReconciliationQueryRepository(
                            task_runtime.canonical_db_path,
                            reconciliation_proof_verifier=(
                                Ed25519ReconciliationProofVerifier(read_keys)
                                if read_keys
                                else None
                            ),
                        )
                    )
                from host_brain.channels.embodiment import (  # noqa: PLC0415
                    HostEmbodimentRuntime,
                    build_embodiment_receipt_audit_observer,
                    build_durable_host_embodiment_session_config,
                )
                from host_brain.persistence.embodiment_outbox_repository import (  # noqa: PLC0415
                    EmbodimentOutboxRepository,
                )

                embodiment_repository = EmbodimentOutboxRepository(
                    task_runtime.canonical_db_path
                )
                embodiment_runtime = HostEmbodimentRuntime(
                    repository=embodiment_repository,
                    worker_id=f"{task_runtime.recovery_id}::embodiment",
                    session_config=build_durable_host_embodiment_session_config(
                        task_runtime.canonical_db_path
                    ),
                    receipt_observers=(
                        build_embodiment_receipt_audit_observer(
                            ledger.log_voice_event
                        ),
                    ),
                )
                # A replacement process must rotate any inherited live Edge
                # fence before routes can admit more physical work.  Fresh
                # stores have no inherited lease and do not force Edge startup.
                await embodiment_runtime.establish_transport_authority()
                embodiment_authority_task = asyncio.create_task(
                    embodiment_runtime.run_authority_heartbeat(),
                    name="host-embodiment-authority-heartbeat",
                )
                # Ensure the lifespan-owned heartbeat has entered its renewal
                # loop before composition can eventually expose routes.
                await asyncio.sleep(0)
                _bind_host_hard_state_runtime(
                    task_runtime=task_runtime,
                    embodiment_runtime=embodiment_runtime,
                    host_lifecycle=host_lifecycle,
                )
                activity_monitor = host_lifecycle.start_activity_monitor(
                    ledger=ledger,
                )
                startup_probe_statuses["activity_monitor"] = (
                    f"OK (gaming_check={activity_monitor._gaming_interval}s, "
                    f"idle_check={activity_monitor._idle_interval}s, "
                    "hard_state_journal=bound)"
                )
                runtime_context_application = _compose_host_runtime_context(
                    task_runtime=task_runtime,
                    ledger=ledger,
                    embodiment_runtime=embodiment_runtime,
                    environment_observation_provider=(
                        _runtime_environment_observation_provider(
                            host_lifecycle.get_mode_transition_service()
                        )
                    ),
                )
                runtime_context_composition = runtime_context_application.composition
                action_preview_service = _compose_action_preview_service(
                    task_runtime=task_runtime,
                    ledger=ledger,
                    runtime_context_application=runtime_context_application,
                )
                startup_observed_at_ms = now_ms()
                _observe_runtime_startup(
                    runtime_context_application=runtime_context_application,
                    startup_probe_statuses=startup_probe_statuses,
                    observed_at_ms=startup_observed_at_ms,
                )
                host_obs_degradation_state_provider = (
                    runtime_context_composition.guardian.read_degradation_state
                )
                from host_brain.app.observability_runtime.service_health_heartbeat import (  # noqa: PLC0415
                    heartbeat_loop,
                )

                guardian_heartbeat_task = asyncio.create_task(
                    heartbeat_loop(
                        30.0,
                        degradation_observer=(
                            runtime_context_composition.guardian.observe_service_batch
                        ),
                        resource_manager_provider=(
                            host_lifecycle.get_resource_manager
                        ),
                    ),
                    name="host-runtime-guardian-health-heartbeat",
                )
                catalog_operation_id = str(
                    os.environ.get("HOST_SERVICE_CATALOG_OPERATION_ID") or ""
                ).strip()
                if catalog_operation_id:
                    catalog_observation = (
                        _compose_service_catalog_guardian_observation(
                            canonical_db_path=task_runtime.canonical_db_path,
                            guardian=runtime_context_composition.guardian,
                            operation_id=catalog_operation_id,
                            logger=logger,
                        )
                    )
                    application.state.service_catalog_rollout_store = (
                        catalog_observation.store
                    )
                    application.state.service_catalog_rollout_observer = (
                        catalog_observation.observer
                    )
                    catalog_rollout_guardian_task = asyncio.create_task(
                        catalog_observation.observe_terminal_receipt(),
                        name="host-service-catalog-rollout-guardian-observer",
                    )
                await asyncio.sleep(0)
                application.state.runtime_context_composition = (
                    runtime_context_composition
                )
                life_dispatchers = _compose_life_dispatchers(
                    ledger,
                    embodiment_runtime=embodiment_runtime,
                    action_preview_service=action_preview_service,
                    agent_runtime_ports=agent_runtime_ports,
                )
                application.state.embodiment_runtime = embodiment_runtime
                canon = build_process_canon_container(
                    ledger=ledger,
                    canonical_db_path=task_runtime.canonical_db_path,
                    world_root=get_persistence_settings().world_canon_root.resolve(),
                    degradation_capability_port=(
                        runtime_context_composition.guardian
                    ),
                )
                # Startup reconciliation is a production composition action;
                # the legacy HTTP lifespan no longer constructs this writer.
                canon.application.sync_startup()
                canon_container = canon
                application.state.canon_container = canon
                cancellation_operator_enabled = bool(
                    getattr(
                        runtime_settings,
                        "task_graph_cancellation_operator_enabled",
                        False,
                    )
                )
                operation_reconciliation_operator_enabled = bool(
                    getattr(
                        runtime_settings,
                        "task_operation_reconciliation_operator_enabled",
                        False,
                    )
                )
                effect_reconciliation_operator_enabled = bool(
                    getattr(
                        runtime_settings,
                        "task_effect_reconciliation_operator_enabled",
                        False,
                    )
                )
                terminal_reconciliation_operator_enabled = bool(
                    getattr(
                        runtime_settings,
                        "task_terminal_reconciliation_operator_enabled",
                        False,
                    )
                )
                _assert_graph_cancellation_reconciliation_production_ready(
                    enabled=cancellation_operator_enabled,
                    task_store=task_runtime.task_store,
                )
                ingress_settings = get_input_settings()
                command_ingress = build_process_command_ingress_container(
                    canonical_db_path=task_runtime.canonical_db_path,
                    mode=ingress_settings.secure_ingress_mode,
                    hmac_secrets=_command_ingress_hmac_secrets(),
                )
                _assert_graph_cancellation_operator_startup(
                    enabled=cancellation_operator_enabled,
                    operation_reconciliation_enabled=(
                        operation_reconciliation_operator_enabled
                    ),
                    effect_reconciliation_enabled=(
                        effect_reconciliation_operator_enabled
                    ),
                    terminal_reconciliation_enabled=(
                        terminal_reconciliation_operator_enabled
                    ),
                    command_ingress=command_ingress,
                    security=security,
                )
                _assert_task_operation_reconciliation_operator_startup(
                    enabled=operation_reconciliation_operator_enabled,
                    command_ingress=command_ingress,
                    security=security,
                )
                _assert_task_effect_reconciliation_operator_startup(
                    enabled=effect_reconciliation_operator_enabled,
                    command_ingress=command_ingress,
                    security=security,
                )
                _assert_task_terminal_reconciliation_operator_startup(
                    enabled=terminal_reconciliation_operator_enabled,
                    command_ingress=command_ingress,
                    security=security,
                )
                cancellation_authority_verifier = (
                    SecureIngressGraphCancellationAuthorityVerifier(
                        command_ingress.store
                    )
                )
                internal_ingress = InternalAgentIngress(
                    evaluator=command_ingress.evaluator,
                )
                try:
                    episodic_projection_store = host_services.episodic_store()
                except Exception:
                    episodic_projection_store = None
                    logger.warning(
                        "LIFE_MEMORY_LEGACY_PROJECTION_UNAVAILABLE",
                        exc_info=True,
                    )
                memory = build_process_memory_container(
                    ledger=ledger,
                    canonical_db_path=task_runtime.canonical_db_path,
                    worth_evaluator=application_ports.memory_worth_evaluator(),
                    memory_guard=ExistingMemoryGuardAdapter(
                        host_services.memory_guard()
                    ),
                    episodic_projection_store=episodic_projection_store,
                )
                application_ports.memory_service_container_setter(memory)
                internal_composition = _compose_internal_runtime(
                    application=application,
                    task_runtime=task_runtime,
                    runtime_settings=runtime_settings,
                    ledger=ledger,
                    command_ingress=command_ingress,
                    tool_composition=tool_composition,
                    runtime_context_application=runtime_context_application,
                    action_preview_service=action_preview_service,
                    cancellation_authority_verifier=cancellation_authority_verifier,
                    episodic_projection_store=episodic_projection_store,
                    semantic_recaller=host_obs_semantic_recaller,
                    mode_transition_service_provider=host_lifecycle.get_mode_transition_service,
                    life_receipt_reader_provider=(
                        lambda: life_command_authority_provider(ledger)[1]
                    ),
                    life_authority_provider=(
                        lambda: life_command_authority_provider(ledger)
                    ),
                    agent_runtime_ports=agent_runtime_ports,
                    token_service_provider=application_ports.token_service,
                    intent_engine_provider=intent_engine_provider,
                    model_attempt_service_provider=host_services.model_attempt_service,
                    prompt_registry_provider=prompt_registry_provider,
                    workspace_composer=workspace.compose_agent_workspace_application_scope,
                    artifact_store_factory=AgentArtifactStore,
                    scheduler_worker_registry_factory=SchedulerWorkerRegistry,
                    scheduler_operation_handler_factory=SchedulerAcceptedOperationHandler,
                    operator_composition_factory=build_governed_operator_composition,
                    runtime_router_factory=CanonicalRuntimeRouter,
                    agent_executor_factory=AgentRuntimeExecutor,
                    internal_runtime_factory=build_governed_internal_runtime,
                )
                canonical_owner = internal_composition.canonical_owner
                agent_workspace_container = internal_composition.workspace_container
                scheduler_workers = internal_composition.scheduler_workers
                acceptance_effect_dispatcher = (
                    internal_composition.acceptance_effect_dispatcher
                )

                try:
                    from host_brain.app.vision.proactive_push_client import (  # noqa: PLC0415
                        build_proactive_push_client,
                    )

                    proactive_client = build_proactive_push_client()
                    proactive_push_detail = ""
                except Exception as exc:
                    proactive_client = None
                    proactive_push_detail = str(exc)[:300]
                    logger.warning("SCHEDULER_PROACTIVE_PUSH_DEGRADED:%s", exc)

                def scheduler_ui_pusher(
                    message: dict[str, Any],
                    *,
                    preview_proof: Any,
                ) -> dict[str, Any]:
                    if proactive_client is None:
                        return {
                            "status": "SKIPPED",
                            "success": False,
                            "detail": proactive_push_detail,
                        }
                    return dict(
                        proactive_client.push(
                            message,
                            preview_proof=preview_proof,
                        )
                        or {}
                    )

                async def scheduler_production_handler(
                    *,
                    occurrence: Any,
                    definition: Any,
                    command: Any,
                    execution_context: Any,
                ) -> dict[str, Any]:
                    intent = dict(definition.intent_template)
                    args = dict(intent.get("args") or {})
                    args.update(dict(occurrence.payload or {}))
                    intent["args"] = args
                    intent["request_id"] = command.request_id
                    result = await asyncio.to_thread(
                        run_proactive_intent_dispatch,
                        intent,
                        ports=lifecycle_runtime_ports,
                        logger=logger,
                        get_ledger=ledger_provider,
                        life_snapshot_reader=life_snapshot_reader_provider,
                        retention_policy=None,
                        ui_pusher=scheduler_ui_pusher,
                        execution_context=execution_context,
                        runtime_context_application=runtime_context_application,
                        action_preview_service=action_preview_service,
                    )
                    return _scheduler_result_wiring.proactive_scheduler_handler_result(result)

                async def distillation_scheduler_handler(**_: Any) -> dict[str, Any]:
                    report = await host_services.distillation_engine().run_batch()
                    return _scheduler_result_wiring.distillation_scheduler_handler_result(report)

                async def security_immune_scheduler_handler(**_: Any) -> dict[str, Any]:
                    result = await application_ports.security_immune_agent().run_once(
                        reason="scheduled"
                    )
                    return _scheduler_result_wiring.security_immune_scheduler_handler_result(result)

                async def agent_immune_scheduler_handler(**_: Any) -> dict[str, Any]:
                    from host_brain.agent_runtime.immune import (  # noqa: PLC0415
                        build_immune_proposal_from_report,
                    )
                    from host_brain.app.health_routes import (  # noqa: PLC0415
                        build_live_health_report,
                    )

                    proposal = build_immune_proposal_from_report(
                        await build_live_health_report(
                            degradation_state_provider=(
                                runtime_context_composition.guardian.read_degradation_state
                                if runtime_context_composition is not None
                                else None
                            )
                        ),
                        source="health",
                    )
                    if not proposal.get("triggered"):
                        return {
                            "status": "SKIPPED",
                            "reason_code": "IMMUNE_WORKORDER_NOT_REQUIRED",
                            "proposal": proposal,
                        }
                    return {
                        "status": "BLOCKED",
                        "reason_code": "IMMUNE_REVIEW_REQUIRED",
                        "summary": "Immune proposal requires governed review",
                        "proposal": proposal,
                    }

                async def life_tick_scheduler_handler(
                    *,
                    occurrence: Any,
                    definition: Any,
                    command: Any,
                    execution_context: Any,
                ) -> dict[str, Any]:
                    life_command_service, life_snapshot_reader = (
                        life_command_authority_provider(ledger)
                    )
                    return await asyncio.to_thread(
                        _submit_life_tick_command,
                        occurrence=occurrence,
                        definition=definition,
                        command=command,
                        execution_context=execution_context,
                        runtime_context_application=runtime_context_application,
                        life_command_service=life_command_service,
                        life_snapshot_reader=life_snapshot_reader,
                    )

                async def life_io_scheduler_handler(**_: Any) -> dict[str, Any]:
                    if life_dispatchers is None:
                        return {
                            "status": "FAILED",
                            "reason_code": "LIFE_DISPATCHERS_UNAVAILABLE",
                        }
                    projection_dispatcher, outbox_dispatcher = life_dispatchers
                    projection = await projection_dispatcher.dispatch_pending_async(
                        limit=100
                    )
                    delivery = await outbox_dispatcher.dispatch_pending_async(limit=100)
                    failed = int(projection.failed) + int(delivery.failed)
                    return {
                        "status": "SUCCEEDED" if failed == 0 else "DEGRADED",
                        "reason_code": (
                            None if failed == 0 else "LIFE_IO_DISPATCH_PARTIAL_FAILURE"
                        ),
                        "projection": projection.as_dict(),
                        "delivery": delivery.as_dict(),
                    }

                async def embodiment_io_scheduler_handler(**_: Any) -> dict[str, Any]:
                    if embodiment_runtime is None:
                        return {
                            "status": "FAILED",
                            "reason_code": "EMBODIMENT_DISPATCHER_UNAVAILABLE",
                        }
                    # An uncertain command must never be replayed by a later
                    # scheduler tick.  Run normal pending delivery and the
                    # independent read-only reconciliation pass separately so
                    # one transient dispatch failure cannot prevent recovery
                    # of an older ``IN_DOUBT`` command.
                    dispatch_error: Exception | None = None
                    try:
                        batch = await embodiment_runtime.dispatch_pending_async(limit=100)
                        dispatch = batch.as_dict()
                    except Exception as exc:  # durable claim expires to IN_DOUBT
                        dispatch_error = exc
                        dispatch = {
                            "status": "DEGRADED",
                            "reason_code": "EMBODIMENT_DISPATCH_UNAVAILABLE",
                        }
                    reconciliation = await embodiment_runtime.reconcile_in_doubt_async(
                        limit=100
                    )
                    result = {
                        "status": (
                            "SUCCEEDED"
                            if dispatch.get("status") == "SUCCEEDED"
                            and reconciliation.as_dict().get("status") == "SUCCEEDED"
                            else "DEGRADED"
                        ),
                        "dispatch": dispatch,
                        "reconciliation": reconciliation.as_dict(),
                    }
                    if dispatch_error is not None:
                        result["reason_code"] = "EMBODIMENT_DISPATCH_UNAVAILABLE"
                    return result

                def scheduler_current_mode() -> str:
                    return str(host_lifecycle.get_hard_state().sys_mode)

                def scheduler_resource_gate(definition: Any, mode: str) -> Any:
                    del mode
                    manager = host_lifecycle.get_resource_manager(
                        create_if_missing=False
                    )
                    if manager is None or not callable(
                        getattr(manager, "assess_background_task", None)
                    ):
                        return True
                    profile = dict(definition.resource_profile)
                    return manager.assess_background_task(
                        task_id=definition.task_id,
                        priority=definition.priority,
                        task_type=str(
                            profile.get("resource_class") or "host_scheduler"
                        ),
                        uses_llm=bool(profile.get("uses_llm", False)),
                        uses_gpu=bool(profile.get("uses_gpu", False)),
                        reason="governed_scheduler",
                    )

                scheduler_settings = get_scheduler_settings()
                scheduler_clock_enabled = bool(
                    scheduler_settings.clock_enabled
                    and scheduler_settings.background_tasks_enabled
                )
                scheduler_clock_identity = None
                if scheduler_clock_enabled:
                    scheduler_clock_identity = SchedulerClockIdentityConfig(
                        principal_id=str(scheduler_settings.clock_principal_id),
                        tenant_id=str(scheduler_settings.clock_tenant_id),
                        key_id=str(scheduler_settings.clock_key_id),
                        secret=(
                            scheduler_settings.clock_hmac_secret.get_secret_value()
                        ),
                        proof_ttl_seconds=(
                            scheduler_settings.clock_credential_ttl_seconds
                        ),
                    )
                scheduler = build_process_scheduler_container(
                    task_runtime_container=task_runtime,
                    ledger=ledger,
                    token_service=application_ports.token_service(),
                    evaluator=command_ingress.evaluator,
                    claim_result_store=command_ingress.store,
                    intent_engine=intent_engine_provider(),
                    runtime_router=CanonicalRuntimeRouter(),
                    owner_id=canonical_owner,
                    current_mode=scheduler_current_mode,
                    resource_gate=scheduler_resource_gate,
                    default_handler=scheduler_production_handler,
                    task_handlers={
                        "life_command": life_tick_scheduler_handler,
                        "life_io": life_io_scheduler_handler,
                        "embodiment_io": embodiment_io_scheduler_handler,
                        "distillation_engine": distillation_scheduler_handler,
                        "security_immune": security_immune_scheduler_handler,
                        "agent_immune": agent_immune_scheduler_handler,
                    },
                    seed_definitions=build_scheduler_seed_configs(
                        settings=get_scheduler_settings(),
                        security_immune_agent_provider=(
                            application_ports.security_immune_agent
                        ),
                        logger=logger,
                    ),
                    seed_worker_keys={
                        "life_tick": "life_command",
                        "life_io_dispatch_periodic": "life_io",
                        "embodiment_outbox_periodic": "embodiment_io",
                        "distillation_engine_periodic": "distillation_engine",
                        "security_immune_periodic": "security_immune",
                        "agent_immune_auto_dispatch_tick": "agent_immune",
                    },
                    clock_enabled=scheduler_clock_enabled,
                    clock_identity=scheduler_clock_identity,
                    clock_poll_interval_seconds=(
                        scheduler_settings.clock_poll_interval_seconds
                    ),
                    clock_batch_limit=scheduler_settings.clock_batch_limit,
                    cancellation_authority_verifier=(
                        cancellation_authority_verifier
                    ),
                    runtime_context_application=runtime_context_application,
                    action_preview_service=action_preview_service,
                    worker_registry=scheduler_workers,
                    acceptance_effect_dispatcher=(
                        acceptance_effect_dispatcher
                    ),
                )
                activated_internal = await _activate_internal_runtime(
                    application=application,
                    task_runtime=task_runtime,
                    command_ingress=command_ingress,
                    security=security,
                    scheduler=scheduler,
                    composition=internal_composition,
                    cancellation_operator_enabled=cancellation_operator_enabled,
                    operation_reconciliation_operator_enabled=(
                        operation_reconciliation_operator_enabled
                    ),
                    effect_reconciliation_operator_enabled=(
                        effect_reconciliation_operator_enabled
                    ),
                    terminal_reconciliation_operator_enabled=(
                        terminal_reconciliation_operator_enabled
                    ),
                    terminal_reconciliation_read_authority=(
                        terminal_reconciliation_read_authority
                    ),
                    memory=memory,
                    intent_engine_provider=intent_engine_provider,
                    runtime_group_factory=GovernedRuntimeGroup,
                    review_command_service_factory=TaskReviewCommandService,
                    internal_command_service_factory=InternalAgentCommandService,
                )
                task_runtime_container = task_runtime
                governed_internal_runtime = internal_composition.internal_runtime
                governed_operator_runtime = internal_composition.operator_runtime
                internal_agent_command_service = (
                    activated_internal.internal_command_service
                )
                task_review_command_service = activated_internal.review_commands
                command_ingress_container = command_ingress
                memory_container = memory
                internal_agent_ingress = internal_ingress
                scheduler_container = scheduler
                yield
            finally:
                for owned_task, task_name in (
                    (catalog_rollout_guardian_task, "catalog-rollout-guardian"),
                    (guardian_heartbeat_task, "runtime-guardian-heartbeat"),
                ):
                    if owned_task is None:
                        continue
                    owned_task.cancel()
                    try:
                        await owned_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.error(
                            "HOST_OWNED_TASK_CLOSE_FAILED:%s",
                            task_name,
                            exc_info=True,
                        )
                catalog_rollout_guardian_task = guardian_heartbeat_task = None
                await shutdown_host_runtime_authority(
                    application=application,
                    security=security,
                    scheduler=scheduler,
                    task_runtime=task_runtime,
                    agent_workspace_container=(
                        agent_workspace_container
                        or getattr(
                            application.state,
                            "agent_workspace_container",
                            None,
                        )
                    ),
                    workspace_quiesce=(
                        workspace.quiesce_agent_workspace_application_scope
                    ),
                    host_lifecycle=host_lifecycle,
                    logger=logger,
                    embodiment_authority_task=embodiment_authority_task,
                    reset_life_command_authority=_reset_life_command_authority,
                    embodiment_runtime=embodiment_runtime,
                    set_memory_service_container=(
                        application_ports.memory_service_container_setter
                    ),
                    memory=memory,
                    command_ingress=command_ingress,
                    tool_composition=tool_composition,
                    on_dependencies_released=withdraw_closed_process_references,
                )

    application = create_host_fastapi_app(
        lifespan=app_lifespan,
        metrics=runtime_metrics,
        logger=logger,
        get_ledger=ledger_provider,
        get_tool_broker=tool_composition.provide_tool_broker,
        get_tool_catalog=host_services.tool_catalog,
        get_bond_engine=bond_engine_provider,
        get_bond_writer=application_ports.bond_dynamics_writer,
        chat_endpoint=chat_endpoint_with_runtime,
        edge_events_endpoint=edge_events_endpoint_with_runtime,
        root_endpoint=root_endpoint,
        obs_command_ingress_provider=obs_command_ingress_provider,
        pentest_secure_run_composition_provider=(
            pentest_secure_run_composition_provider
        ),
        signed_skill_provider_intake_provider=(
            signed_skill_provider_intake_provider
        ),
        governed_command_application_provider=tool_composition.provide_runtime,
        internal_agent_ingress_provider=internal_agent_ingress_provider,
        internal_agent_command_service_provider=(
            internal_agent_command_service_provider
        ),
        scheduler_command_ingress_provider=scheduler_command_ingress_provider,
        scheduler_command_service_provider=scheduler_command_service_provider,
        degradation_state_provider=(
            lambda: host_obs_degradation_state_provider()
        ),
        degradation_capability_port_provider=(
            degradation_capability_port_provider
        ),
        host_obs_read_ports=host_obs_read_ports,
        canon_service_provider=canon_service_provider,
        route_ports=route_ports,
        obs_route_ports=host_obs_route_ports,
        input_runtime_ports=input_runtime_ports,
    )
    withdraw_published_runtime_state(application)
    application.state.host_runtime_shutdown_error = None
    application.state.security_service_container = None
    application.state.agent_workspace_container = None
    application.state.canon_container = None
    application.state.embodiment_runtime = None
    application.state.runtime_context_composition = None
    application.state.agent_runtime_application_ports = None
    application.state.host_lifecycle_container = host_lifecycle
    application.state.tribe_application_ports = tribe_application_ports
    application.state.lifecycle_runtime_ports = lifecycle_runtime_ports
    application.state.input_runtime_ports = input_runtime_ports
    application.state.task_runtime_container_provider = (
        task_runtime_container_provider
    )
    return application


app = create_app()


__all__ = ["app", "create_app"]
