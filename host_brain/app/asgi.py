"""ASGI entrypoint for the host main service."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from host_brain.app.app_factory import create_host_fastapi_app, docs_redirect_response
from host_brain.app.chat_handler import handle_chat
from host_brain.app.chat_immune_gate import evaluate_immune_gate_for_chat, schedule_chat_immune_gate_event
from host_brain.app.chat_routes import handle_v1_chat_route
from host_brain.app.dependencies import (
    get_ledger,
    get_intent_engine,
    get_episodic_store,
    get_skill_registry,
    get_memory_guard,
    get_memory_worth_evaluator,
    get_distillation_engine,
    get_hard_state,
    get_resource_manager,
    get_security_immune_agent,
    set_memory_service_container,
    get_token_service,
    get_tool_broker,
    get_tool_catalog,
)
from host_brain.app.edge_event_routes import handle_edge_events_route
from host_brain.app.http_hooks import runtime_lifespan
from host_brain.app.http_hooks_runtime_helpers import run_proactive_intent_dispatch
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse
from host_brain.app.governed_task_adapter import (
    GovernedHostRuntime,
    build_governed_host_runtime,
)
from host_brain.app.internal_agent_ingress import InternalAgentIngress
from host_brain.app.internal_task_runtime import (
    GovernedInternalRuntime,
    InternalAgentCommandService,
    build_governed_internal_runtime,
)
from host_brain.app.memory_policy_adapter import ExistingMemoryGuardAdapter
from host_brain.app.settings import (
    get_core_settings,
    get_persistence_settings,
    get_scheduler_settings,
)
from host_brain.app.settings.input import get_input_settings
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.composition.security_services import (
    ProcessSecurityServiceContainer,
    SecurityServiceContainerCloseError,
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
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.composition.scheduler import (
    ProcessSchedulerContainer,
    SchedulerClockIdentityConfig,
    build_process_scheduler_container,
)
from host_brain.composition.task_runtime import (
    ProcessTaskRuntimeContainer,
    TaskRuntimeContainerCloseError,
    build_process_task_runtime_container,
)
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    TaskPriority,
    TriggerMode,
)


logger = logging.getLogger("Natsume.asgi")
runtime_metrics = RuntimeMetrics()


def resolve_bond_engine():
    from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

    return get_social_brain_agent().resolve_stage_engine()


def schedule_immune_gate_audit(
    *,
    request_id: str,
    request_body: ChatRequest,
    immune_result: dict,
) -> None:
    schedule_chat_immune_gate_event(
        request_id=request_id,
        request_body=request_body,
        immune_result=immune_result,
        get_ledger=get_ledger,
        logger=logger,
    )


async def edge_events_endpoint(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
    return await handle_edge_events_route(
        request_body=request_body,
        get_ledger=get_ledger,
        logger=logger,
    )


async def root_endpoint() -> Response:
    return docs_redirect_response()


def _command_ingress_hmac_secrets() -> dict[str, str]:
    settings = get_input_settings()
    configured: dict[str, str] = {}
    raw = str(settings.secure_ingress_hmac_secrets_json or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            configured.update(
                {
                    str(key).strip(): str(value)
                    for key, value in parsed.items()
                    if str(key).strip() and isinstance(value, str) and value
                }
            )
    edge_key = str(settings.edge_gateway_hmac_key_id or "").strip()
    edge_secret = str(settings.edge_gateway_hmac_secret or "")
    if edge_key and edge_secret:
        configured.setdefault(edge_key, edge_secret)
    scheduler = get_scheduler_settings()
    scheduler_key = str(scheduler.clock_key_id or "").strip()
    scheduler_secret = scheduler.clock_hmac_secret.get_secret_value()
    if scheduler_key and scheduler_secret:
        configured.setdefault(scheduler_key, scheduler_secret)
    return configured


def _scheduler_seed_configs() -> list[SchedulerTaskConfig]:
    settings = get_scheduler_settings()
    configs: list[SchedulerTaskConfig] = []
    if settings.seed_builtin_definitions:
        from host_brain.scheduler.scheduling_center import (  # noqa: PLC0415
            build_personality_scheduler_seed_task_configs,
        )

        configs.extend(build_personality_scheduler_seed_task_configs())
    enabled = bool(settings.background_tasks_enabled)
    configs.extend(
        [
            SchedulerTaskConfig(
                task_id="audit_retention_daily",
                trigger_mode=TriggerMode.CRON,
                trigger_config={
                    "hour": 2,
                    "minute": 0,
                    "catch_up_window_minutes": 1_440,
                },
                intent_template={
                    "skill_name": "audit_retention",
                    "args": {},
                    "proposer_node_id": "scheduler",
                },
                priority=TaskPriority.LOW,
                mode_allowlist=["normal", "sleep"],
                enabled=enabled,
                description="Daily audit retention policy",
                resource_profile={
                    "resource_class": "maintenance",
                    "uses_llm": False,
                    "uses_gpu": False,
                },
            ),
            SchedulerTaskConfig(
                task_id="distillation_engine_periodic",
                trigger_mode=TriggerMode.INTERVAL,
                trigger_config={"seconds": settings.distillation_interval_seconds},
                intent_template={
                    "skill_name": "distillation_engine_periodic",
                    "args": {},
                    "proposer_node_id": "scheduler",
                },
                priority=TaskPriority.HIGH,
                mode_allowlist=["normal", "sleep"],
                enabled=enabled and settings.distillation_enabled,
                max_consecutive_failures=3,
                description="Durable episodic-memory distillation",
                resource_profile={
                    "resource_class": "memory_heavy",
                    "uses_llm": True,
                    "uses_gpu": False,
                },
            ),
            SchedulerTaskConfig(
                task_id="agent_immune_auto_dispatch_tick",
                trigger_mode=TriggerMode.INTERVAL,
                trigger_config={"seconds": settings.agent_immune_interval_seconds},
                intent_template={
                    "skill_name": "agent_immune_auto_dispatch_tick",
                    "args": {},
                    "proposer_node_id": "scheduler",
                },
                priority=TaskPriority.HIGH,
                mode_allowlist=["normal", "sleep"],
                enabled=enabled and settings.agent_immune_enabled,
                max_consecutive_failures=2,
                description="Governed internal-agent immune proposal tick",
                resource_profile={
                    "resource_class": "background_llm",
                    "uses_llm": True,
                    "uses_gpu": False,
                },
            ),
        ]
    )
    try:
        policy = get_security_immune_agent().policy
        configs.append(
            SchedulerTaskConfig(
                task_id="security_immune_periodic",
                trigger_mode=TriggerMode.INTERVAL,
                trigger_config={"seconds": int(policy.scan_interval_seconds)},
                intent_template={
                    "skill_name": "security_immune_periodic",
                    "args": {},
                    "proposer_node_id": "scheduler",
                },
                priority=TaskPriority.HIGH,
                mode_allowlist=["normal", "sleep"],
                enabled=enabled and bool(policy.enabled),
                max_consecutive_failures=2,
                description="Governed internal security immune scan",
                resource_profile={
                    "resource_class": "security",
                    "uses_llm": False,
                    "uses_gpu": False,
                },
            )
        )
    except Exception:
        logger.warning("SCHEDULER_SECURITY_IMMUNE_SEED_DEGRADED", exc_info=True)
    deduplicated: dict[str, SchedulerTaskConfig] = {}
    for config in configs:
        config.enabled = bool(config.enabled and enabled)
        deduplicated.setdefault(config.task_id, config)
    return list(deduplicated.values())


def _scheduler_handler_result(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        result = dict(value)
    else:
        result = {}
        for method_name in ("as_dict", "to_dict", "model_dump"):
            method = getattr(value, method_name, None)
            if callable(method):
                candidate = method()
                if isinstance(candidate, dict):
                    result = dict(candidate)
                    break
    if not result:
        result = {"detail": str(value or "")[:500]}
    raw = str(result.get("status") or "SUCCEEDED").strip().upper()
    result["status"] = {
        "OK": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "DONE": "SUCCEEDED",
        "ERROR": "FAILED",
        "REJECTED": "BLOCKED",
        "DEFERRED": "DEGRADED",
    }.get(raw, raw)
    if result["status"] not in {
        "EXECUTED",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED",
        "DEGRADED",
        "SKIPPED",
    }:
        result["status"] = "DEGRADED"
        result.setdefault("reason_code", f"SCHEDULER_HANDLER_STATUS_{raw or 'UNKNOWN'}")
    return result


def create_app() -> FastAPI:
    # This slot belongs to one FastAPI application factory invocation.  Route
    # providers close over it explicitly; there is no module-global service
    # locator and no app-state lookup inside the API registry.
    security_container: ProcessSecurityServiceContainer | None = None
    command_ingress_container: ProcessCommandIngressContainer | None = None
    memory_container: ProcessMemoryContainer | None = None
    task_runtime_container: ProcessTaskRuntimeContainer | None = None
    governed_host_runtime: GovernedHostRuntime | None = None
    governed_internal_runtime: GovernedInternalRuntime | None = None
    internal_agent_command_service: InternalAgentCommandService | None = None
    internal_agent_ingress: InternalAgentIngress | None = None
    scheduler_container: ProcessSchedulerContainer | None = None
    canon_container: ProcessCanonContainer | None = None

    def task_runtime_container_provider() -> ProcessTaskRuntimeContainer | None:
        return task_runtime_container

    def governed_command_application_provider() -> GovernedHostRuntime | None:
        return governed_host_runtime

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

    def canon_service_provider() -> Any | None:
        current = canon_container
        return current.application if current is not None else None

    async def handle_chat_with_runtime(
        request_body: ChatRequest,
        request: Request,
        metrics: RuntimeMetrics,
    ) -> StreamingResponse:
        return await handle_chat(
            request_body,
            request,
            metrics,
            governed_command_application=governed_command_application_provider(),
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
            schedule_immune_gate_fn=schedule_immune_gate_audit,
            handle_chat_fn=handle_chat_with_runtime,
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
        nonlocal security_container, command_ingress_container
        nonlocal memory_container
        nonlocal task_runtime_container, governed_host_runtime
        nonlocal governed_internal_runtime, internal_agent_command_service
        nonlocal internal_agent_ingress, scheduler_container
        nonlocal canon_container
        async with runtime_lifespan(
            get_ledger=get_ledger,
            logger=logger,
            canon_service_provider=canon_service_provider,
        ):
            security: ProcessSecurityServiceContainer | None = None
            command_ingress: ProcessCommandIngressContainer | None = None
            memory: ProcessMemoryContainer | None = None
            task_runtime: ProcessTaskRuntimeContainer | None = None
            scheduler: ProcessSchedulerContainer | None = None
            canon: ProcessCanonContainer | None = None
            try:
                ledger = get_ledger()
                security = build_process_security_service_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                    token_service=get_token_service(),
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
                ledger = get_ledger()
                task_runtime = build_process_task_runtime_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                )
                canon = build_process_canon_container(
                    ledger=ledger,
                    canonical_db_path=task_runtime.canonical_db_path,
                    world_root=get_persistence_settings().world_canon_root,
                )
                # Startup reconciliation is a production composition action;
                # the legacy HTTP lifespan no longer constructs this writer.
                canon.application.sync_startup()
                canon_container = canon
                application.state.canon_container = canon
                runtime_settings = get_core_settings()
                ingress_settings = get_input_settings()
                command_ingress = build_process_command_ingress_container(
                    canonical_db_path=task_runtime.canonical_db_path,
                    mode=ingress_settings.secure_ingress_mode,
                    hmac_secrets=_command_ingress_hmac_secrets(),
                )
                internal_ingress = InternalAgentIngress(
                    evaluator=command_ingress.evaluator,
                )
                try:
                    episodic_projection_store = get_episodic_store()
                except Exception:
                    episodic_projection_store = None
                    logger.warning(
                        "LIFE_MEMORY_LEGACY_PROJECTION_UNAVAILABLE",
                        exc_info=True,
                    )
                memory = build_process_memory_container(
                    ledger=ledger,
                    canonical_db_path=task_runtime.canonical_db_path,
                    worth_evaluator=get_memory_worth_evaluator(),
                    memory_guard=ExistingMemoryGuardAdapter(get_memory_guard()),
                    episodic_projection_store=episodic_projection_store,
                )
                set_memory_service_container(memory)
                canonical_owner = (
                    runtime_settings.task_runtime_canonical_owner
                    or runtime_settings.task_runtime_shadow_owner
                    or "host-task-runtime"
                )
                governed_runtime = build_governed_host_runtime(
                    task_runtime_container=task_runtime,
                    ledger=ledger,
                    skill_registry=get_skill_registry(),
                    token_service=get_token_service(),
                    runtime_router=CanonicalRuntimeRouter(
                        mode=runtime_settings.task_runtime_mode,
                        canary_percent=(
                            runtime_settings.task_runtime_canary_percent
                        ),
                        kill_switch=runtime_settings.task_runtime_kill_switch,
                        canonical_owner=canonical_owner,
                        # The process composition is the canonical worker
                        # owner; request payloads cannot self-confirm it.
                        confirmed_owner=canonical_owner,
                    ),
                    owner_id=canonical_owner,
                )
                internal_runtime = build_governed_internal_runtime(
                    task_runtime_container=task_runtime,
                    ledger=ledger,
                    result_repository=task_runtime.agent_result_repository,
                    runtime_router=CanonicalRuntimeRouter(
                        mode=runtime_settings.task_runtime_mode,
                        canary_percent=(
                            runtime_settings.task_runtime_canary_percent
                        ),
                        kill_switch=runtime_settings.task_runtime_kill_switch,
                        canonical_owner=canonical_owner,
                        confirmed_owner=canonical_owner,
                    ),
                    owner_id=canonical_owner,
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

                def scheduler_ui_pusher(message: dict[str, Any]) -> dict[str, Any]:
                    if proactive_client is None:
                        return {
                            "status": "SKIPPED",
                            "success": False,
                            "detail": proactive_push_detail,
                        }
                    return dict(proactive_client.push(message) or {})

                async def scheduler_production_handler(
                    *,
                    occurrence: Any,
                    definition: Any,
                    command: Any,
                    execution_context: Any,
                ) -> dict[str, Any]:
                    del execution_context
                    intent = dict(definition.intent_template)
                    args = dict(intent.get("args") or {})
                    args.update(dict(occurrence.payload or {}))
                    intent["args"] = args
                    intent["request_id"] = command.request_id
                    result = await asyncio.to_thread(
                        run_proactive_intent_dispatch,
                        intent,
                        logger=logger,
                        get_ledger=get_ledger,
                        retention_policy=None,
                        ui_pusher=scheduler_ui_pusher,
                    )
                    return _scheduler_handler_result(result)

                async def distillation_scheduler_handler(**_: Any) -> dict[str, Any]:
                    report = await get_distillation_engine().run_batch()
                    return _scheduler_handler_result(report)

                async def security_immune_scheduler_handler(**_: Any) -> dict[str, Any]:
                    result = await get_security_immune_agent().run_once(
                        reason="scheduled"
                    )
                    return _scheduler_handler_result(result)

                async def agent_immune_scheduler_handler(**_: Any) -> dict[str, Any]:
                    from host_brain.agent_runtime.immune import (  # noqa: PLC0415
                        build_immune_proposal_from_report,
                    )
                    from host_brain.app.health_routes import (  # noqa: PLC0415
                        build_live_health_report,
                    )

                    proposal = build_immune_proposal_from_report(
                        await build_live_health_report(),
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

                def scheduler_current_mode() -> str:
                    return str(get_hard_state().sys_mode or "normal")

                def scheduler_resource_gate(definition: Any, mode: str) -> Any:
                    del mode
                    manager = get_resource_manager(create_if_missing=False)
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
                    evaluator=command_ingress.evaluator,
                    claim_result_store=command_ingress.store,
                    intent_engine=get_intent_engine(),
                    runtime_router=CanonicalRuntimeRouter(
                        mode=runtime_settings.task_runtime_mode,
                        canary_percent=(
                            runtime_settings.task_runtime_canary_percent
                        ),
                        kill_switch=runtime_settings.task_runtime_kill_switch,
                        canonical_owner=canonical_owner,
                        confirmed_owner=canonical_owner,
                    ),
                    owner_id=canonical_owner,
                    current_mode=scheduler_current_mode,
                    resource_gate=scheduler_resource_gate,
                    default_handler=scheduler_production_handler,
                    task_handlers={
                        "distillation_engine": distillation_scheduler_handler,
                        "security_immune": security_immune_scheduler_handler,
                        "agent_immune": agent_immune_scheduler_handler,
                    },
                    seed_definitions=_scheduler_seed_configs(),
                    seed_worker_keys={
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
                )
                runtime_group = GovernedRuntimeGroup(
                    {
                        "host": governed_runtime,
                        "internal_agent": internal_runtime,
                        "scheduler": scheduler.runtime,
                    }
                )
                task_runtime.bind_runtime(runtime_group)
                await task_runtime.activate_runtime()
                await scheduler.activate_clock()
                task_runtime_container = task_runtime
                governed_host_runtime = governed_runtime
                governed_internal_runtime = internal_runtime
                internal_agent_command_service = InternalAgentCommandService(
                    runtime=internal_runtime,
                    intent_engine=get_intent_engine(),
                    claim_result_store=command_ingress.store,
                )
                command_ingress_container = command_ingress
                memory_container = memory
                internal_agent_ingress = internal_ingress
                scheduler_container = scheduler
                application.state.task_runtime_container = task_runtime
                application.state.governed_host_runtime = governed_runtime
                application.state.governed_internal_runtime = internal_runtime
                application.state.governed_scheduler_runtime = scheduler.runtime
                application.state.scheduler_container = scheduler
                application.state.command_ingress_container = command_ingress
                application.state.memory_service_container = memory
                yield
            finally:
                scheduler_container = None
                internal_agent_ingress = None
                internal_agent_command_service = None
                governed_internal_runtime = None
                governed_host_runtime = None
                task_runtime_container = None
                memory_container = None
                command_ingress_container = None
                application.state.command_ingress_container = None
                application.state.memory_service_container = None
                application.state.governed_internal_runtime = None
                application.state.governed_scheduler_runtime = None
                application.state.scheduler_container = None
                application.state.governed_host_runtime = None
                application.state.task_runtime_container = None
                try:
                    if scheduler is not None:
                        try:
                            # Producers stop before runtime admission closes.
                            await scheduler.stop_clock()
                        except Exception:
                            logger.error(
                                "HOST_SCHEDULER_CLOCK_CLOSE_FAILED",
                                exc_info=True,
                            )
                    if task_runtime is not None:
                        try:
                            await task_runtime.aclose()
                        except TaskRuntimeContainerCloseError as exc:
                            logger.error(
                                "HOST_TASK_RUNTIME_CONTAINER_CLOSE_FAILED:%s",
                                ",".join(
                                    failure.component
                                    for failure in exc.failures
                                ),
                            )
                finally:
                    try:
                        set_memory_service_container(None)
                        if memory is not None:
                            memory.close()
                    finally:
                        try:
                            if command_ingress is not None:
                                await command_ingress.aclose()
                        finally:
                            # Security was constructed first, so it is always
                            # released last, including partial startup failure.
                            canon_container = None
                            application.state.canon_container = None
                            security_container = None
                            application.state.security_service_container = None
                            if security is not None:
                                try:
                                    await security.aclose()
                                except SecurityServiceContainerCloseError as exc:
                                    logger.error(
                                        "HOST_SECURITY_SERVICE_CONTAINER_CLOSE_FAILED:%s",
                                        ",".join(
                                            failure.service.value
                                            for failure in exc.failures
                                        ),
                                    )

    application = create_host_fastapi_app(
        lifespan=app_lifespan,
        metrics=runtime_metrics,
        logger=logger,
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=resolve_bond_engine,
        chat_endpoint=chat_endpoint_with_runtime,
        edge_events_endpoint=edge_events_endpoint,
        root_endpoint=root_endpoint,
        obs_command_ingress_provider=obs_command_ingress_provider,
        pentest_secure_run_composition_provider=(
            pentest_secure_run_composition_provider
        ),
        signed_skill_provider_intake_provider=(
            signed_skill_provider_intake_provider
        ),
        governed_command_application_provider=(
            governed_command_application_provider
        ),
        internal_agent_ingress_provider=internal_agent_ingress_provider,
        internal_agent_command_service_provider=(
            internal_agent_command_service_provider
        ),
        scheduler_command_ingress_provider=scheduler_command_ingress_provider,
        scheduler_command_service_provider=scheduler_command_service_provider,
        canon_service_provider=canon_service_provider,
    )
    application.state.security_service_container = None
    application.state.command_ingress_container = None
    application.state.memory_service_container = None
    application.state.task_runtime_container = None
    application.state.governed_host_runtime = None
    application.state.governed_internal_runtime = None
    application.state.governed_scheduler_runtime = None
    application.state.scheduler_container = None
    application.state.canon_container = None
    application.state.task_runtime_container_provider = (
        task_runtime_container_provider
    )
    return application


app = create_app()


__all__ = ["app", "create_app"]
