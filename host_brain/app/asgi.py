"""ASGI entrypoint for the host main service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, Callable, Mapping, Protocol

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from host_brain.app.app_factory import create_host_fastapi_app, docs_redirect_response
from host_brain.app.chat_handler import handle_chat
from host_brain.app.chat_immune_gate import evaluate_immune_gate_for_chat, schedule_chat_immune_gate_event
from host_brain.app.chat_routes import handle_v1_chat_route
from host_brain.composition.process_services import (
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
    get_mcp_tool_broker,
    get_tool_catalog,
    get_bond_dynamics_writer,
    get_model_attempt_service,
    get_prompt_registry,
    get_capability_broker,
    get_capability_forge_service,
    get_capability_gap_telemetry,
    get_capability_scorecard_provider,
    get_semantic_recaller,
    get_life_snapshot_reader,
)
from host_brain.app.edge_event_routes import handle_edge_events_route
from host_brain.app.http_hooks import runtime_lifespan
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.host_obs_source_composition import (
    HostObsSourceEpoch,
    HostObsSourceRuntimePorts,
    build_host_obs_read_ports,
)
from host_brain.app.process_service_wiring import configure_process_service_ports
from host_brain.lifecycle_runtime.proactive_dispatch import run_proactive_intent_dispatch
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.worker_dispatch import _dispatch_to_graph
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse
from host_brain.app.governed_task_adapter import (
    GovernedHostRuntime,
    build_governed_host_runtime,
)
from host_brain.app.model_provider_composition import ApplicationModelAttemptServicePort
from host_brain.app.production_authority import verify_durable_production_authority
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
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.core.ids import now_ms
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.app.security_services_composition import (
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
from host_brain.composition import agent_workspace as workspace
from host_brain.composition.life import PROCESS_LIFE_PROVIDER, build_process_life_container
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
from host_brain.task_runtime.review_command_service import TaskReviewCommandService
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    TaskPriority,
    TriggerMode,
)
from host_brain.composition.tribe_runtime import require_process_tribe_runtime
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
)
from host_brain.runtime.context_application import (
    RuntimeContextApplicationService,
)
from host_brain.runtime.intent_authority import IntentPolicyIdentityV1
from host_brain.runtime.intent_admission import DurableIntentAdmissionVerifier
from host_brain.runtime.production_composition import (
    RuntimeContextComposition,
    build_runtime_context_composition,
)
from host_brain.runtime.production_intent_authority import (
    ProductionIntentAuthorityPort,
)
from host_brain.runtime.world_source_verifier import (
    RuntimeEventWorldSourceVerifier,
)


logger = logging.getLogger("Natsume.asgi")
runtime_metrics = RuntimeMetrics()


class _LifeCommandServicePort(Protocol):
    def submit(self, command: Any) -> Any: ...

    def submit_next(self, person_id: str, command_factory: Any) -> Any: ...

    def adopt_all_legacy_projections(self, *, adopted_at_ms: int) -> Any: ...


class _LifeSnapshotReaderPort(Protocol):
    def get_snapshot(self, person_id: str) -> Any: ...

    def get_receipt(self, command_id: str) -> Any | None: ...


def _compose_life_command_authority(
    ledger: Any,
) -> tuple[_LifeCommandServicePort, _LifeSnapshotReaderPort]:
    """Resolve the frozen Digital Life command boundary at process startup."""

    container = build_process_life_container(ledger=ledger, adopted_at_ms=now_ms())
    PROCESS_LIFE_PROVIDER.install(container)
    service = container.command_service
    reader = container.snapshot_reader
    return service, reader


def _compose_life_dispatchers(
    ledger: Any,
    *,
    embodiment_runtime: Any,
    action_preview_service: Any | None = None,
) -> tuple[Any, Any]:
    """Bind projection and delivery workers to the canonical Life repository."""

    from host_brain.app.life_projection_handlers import (  # noqa: PLC0415
        handle_life_projection,
    )
    from host_brain.channels.life_delivery_transport import (  # noqa: PLC0415
        LifeDeliveryTransport,
    )
    from host_brain.digital_life.outbox import LifeOutboxDispatcher  # noqa: PLC0415
    from host_brain.digital_life.projection import (  # noqa: PLC0415
        LifeProjectionDispatcher,
    )
    from host_brain.persistence.life_command_repository import (  # noqa: PLC0415
        LifeCommandRepository,
    )

    repository = LifeCommandRepository.from_ledger(ledger)
    return (
        LifeProjectionDispatcher(repository, handle_life_projection),
        LifeOutboxDispatcher(
            repository,
            LifeDeliveryTransport(
                ledger=ledger,
                embodiment_runtime=embodiment_runtime,
                action_preview_service=action_preview_service,
            ),
        ),
    )


def _reset_life_command_authority() -> None:
    PROCESS_LIFE_PROVIDER.clear()


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
    """Translate one governed scheduler occurrence into one Life command."""

    from natsume_contracts.life import LifeCommand  # noqa: PLC0415

    intent = dict(definition.intent_template)
    args = dict(intent.get("args") or {})
    args.update(dict(occurrence.payload or {}))
    person_id = str(args.get("person_id") or "default").strip() or "default"
    runtime_execution = dict(execution_context or {})
    frozen_input_pack = runtime_execution.get("frozen_input_pack")
    if not isinstance(frozen_input_pack, Mapping):
        raise RuntimeError("LIFE_TICK_RUNTIME_CONTEXT_REQUIRED")
    frozen_runtime_context = runtime_context_application.require_frozen(
        dict(frozen_input_pack)
    ).to_dict()
    life_command_id = f"life_tick::{occurrence.occurrence_id}"
    existing_receipt = life_snapshot_reader.get_receipt(life_command_id)
    if existing_receipt is not None:
        as_replayed = getattr(existing_receipt, "as_replayed", None)
        replayed_receipt = as_replayed() if callable(as_replayed) else existing_receipt
        result = _scheduler_handler_result(replayed_receipt)
        result["status"] = "SUCCEEDED"
        result["replayed"] = True
        return result
    from host_brain.digital_life.policy import build_life_tick_payload  # noqa: PLC0415

    occurrence_context = {
        **args,
        **dict(occurrence.payload or {}),
        "scene_decision_v2": frozen_runtime_context["scene_decision_v2"],
        "scene_identity_v2": frozen_runtime_context["scene_identity_v2"],
    }
    action_preview_context = runtime_execution.get("_action_preview_context")
    if isinstance(action_preview_context, Mapping):
        occurrence_context["_action_preview_context"] = dict(
            action_preview_context
        )
    grant_request_id = str(getattr(command, "request_id", "") or "").strip()
    governor_grant = str(
        getattr(command, "governor_grant_ref", "") or ""
    ).strip()
    authority_principal_id = str(
        getattr(command, "principal_id", "")
        or getattr(occurrence, "principal_id", "")
        or ""
    ).strip()
    if grant_request_id and governor_grant and authority_principal_id:
        occurrence_context["delivery_authorization"] = {
            "request_id": grant_request_id,
            "governor_grant_ref": governor_grant,
            "authority_kind": "scheduler_host_command",
            "authority_principal_id": authority_principal_id,
        }

    def _command_factory(snapshot: Any) -> LifeCommand:
        applied_at_ms = max(
            int(now_ms()),
            int(getattr(snapshot, "updated_at_ms", 0) or 0),
        )
        return LifeCommand(
            command_id=life_command_id,
            person_id=person_id,
            command_type="tick",
            payload=build_life_tick_payload(
                snapshot,
                scheduled_at_ms=int(occurrence.scheduled_at_ms),
                occurrence_payload=occurrence_context,
                task_id=str(occurrence.task_id),
                definition_revision=int(occurrence.definition_revision),
            ),
            expected_version=int(snapshot.version),
            created_at_ms=applied_at_ms,
            correlation_id=str(command.request_id),
            causation_id=str(occurrence.occurrence_id),
            metadata={
                "producer": "scheduler_clock",
                "scheduler_command_id": str(command.command_id),
                "scheduler_principal_id": str(occurrence.principal_id),
                "scheduled_at_ms": int(occurrence.scheduled_at_ms),
                "governor_grant_request_id": grant_request_id or None,
                "governor_grant_ref": governor_grant or None,
            },
        )

    receipt = life_command_service.submit_next(person_id, _command_factory)
    return _scheduler_handler_result(receipt)


def resolve_bond_engine():
    from host_brain.composition.process_services import (  # noqa: PLC0415
        get_bond_stage_engine,
    )

    return get_bond_stage_engine()


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


async def edge_events_endpoint(
    request_body: EdgeEventsRequest,
    *,
    input_runtime_ports: InputRuntimeApplicationPorts,
) -> EdgeEventsResponse:
    return await handle_edge_events_route(
        request_body=request_body,
        get_ledger=get_ledger,
        logger=logger,
        input_dispatcher=partial(_dispatch_to_graph, ports=input_runtime_ports),
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


def _assert_graph_cancellation_operator_startup(
    *,
    enabled: bool,
    operation_reconciliation_enabled: bool,
    effect_reconciliation_enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before route activation when cancellation authority is absent."""

    if not enabled:
        return
    if not operation_reconciliation_enabled:
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_OPERATION_RECONCILIATION"
        )
    if not effect_reconciliation_enabled:
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_EFFECT_RECONCILIATION"
        )
    mode = getattr(command_ingress, "mode", None)
    mode_value = getattr(mode, "value", mode)
    if str(mode_value or "").strip().upper() != "ENFORCE":
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        )
    composition = getattr(security, "obs_command_composition", None)
    if (
        composition is None
        or not bool(getattr(composition, "enabled", False))
        or not bool(getattr(composition, "execution_enabled", False))
        or not callable(
            getattr(getattr(composition, "ingress", None), "execute", None)
        )
    ):
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE"
        )


def _assert_task_operation_reconciliation_operator_startup(
    *,
    enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before exposing the signed operation reconciliation port."""

    if not enabled:
        return
    mode = getattr(command_ingress, "mode", None)
    mode_value = getattr(mode, "value", mode)
    if str(mode_value or "").strip().upper() != "ENFORCE":
        raise RuntimeError(
            "TASK_OPERATION_RECONCILIATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        )
    composition = getattr(security, "obs_command_composition", None)
    if (
        composition is None
        or not bool(getattr(composition, "enabled", False))
        or not bool(getattr(composition, "execution_enabled", False))
        or not callable(
            getattr(getattr(composition, "ingress", None), "execute", None)
        )
    ):
        raise RuntimeError(
            "TASK_OPERATION_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE"
        )


def _assert_task_effect_reconciliation_operator_startup(
    *,
    enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before exposing the signed Effect reconciliation port."""

    if not enabled:
        return
    mode = getattr(command_ingress, "mode", None)
    mode_value = getattr(mode, "value", mode)
    if str(mode_value or "").strip().upper() != "ENFORCE":
        raise RuntimeError(
            "TASK_EFFECT_RECONCILIATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        )
    composition = getattr(security, "obs_command_composition", None)
    if (
        composition is None
        or not bool(getattr(composition, "enabled", False))
        or not bool(getattr(composition, "execution_enabled", False))
        or not callable(
            getattr(getattr(composition, "ingress", None), "execute", None)
        )
    ):
        raise RuntimeError(
            "TASK_EFFECT_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE"
        )


def _assert_task_terminal_reconciliation_operator_startup(
    *,
    enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before exposing historical terminal-state repair."""

    if not enabled:
        return
    mode = getattr(command_ingress, "mode", None)
    mode_value = getattr(mode, "value", mode)
    if str(mode_value or "").strip().upper() != "ENFORCE":
        raise RuntimeError(
            "TASK_TERMINAL_RECONCILIATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        )
    composition = getattr(security, "obs_command_composition", None)
    if (
        composition is None
        or not bool(getattr(composition, "enabled", False))
        or not bool(getattr(composition, "execution_enabled", False))
        or not callable(
            getattr(getattr(composition, "ingress", None), "execute", None)
        )
    ):
        raise RuntimeError(
            "TASK_TERMINAL_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE"
        )


def _assert_graph_cancellation_reconciliation_production_ready(
    *,
    enabled: bool,
    task_store: Any,
) -> None:
    """Block cancellation on unresolved Effect or terminal inventory."""

    if not enabled:
        return
    checker = getattr(
        task_store,
        "assert_effect_reconciliation_production_ready",
        None,
    )
    if not callable(checker):
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_EFFECT_PRODUCTION_GATE_UNAVAILABLE"
        )
    checker()
    terminal_checker = getattr(
        task_store,
        "assert_terminal_reconciliation_production_ready",
        None,
    )
    if not callable(terminal_checker):
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_TERMINAL_RECONCILIATION_"
            "PRODUCTION_GATE_UNAVAILABLE"
        )
    terminal_checker()


def _scheduler_seed_configs() -> list[SchedulerTaskConfig]:
    settings = get_scheduler_settings()
    from host_brain.scheduler.scheduling_center import (  # noqa: PLC0415
        build_life_tick_task_config,
    )

    # The definition is always seeded so the catalog has one explicit Life
    # periodic authority. Its enabled flag is still governed by Host settings.
    configs: list[SchedulerTaskConfig] = [
        build_life_tick_task_config(),
        SchedulerTaskConfig(
            task_id="life_io_dispatch_periodic",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 5},
            intent_template={
                "skill_name": "life_io_dispatch",
                "args": {"limit": 100},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.HIGH,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            max_consecutive_failures=3,
            description=(
                "Dispatch committed Life projection/outbox work; SchedulerClock "
                "remains the sole timer"
            ),
            resource_profile={
                "resource_class": "life_io",
                "uses_llm": False,
                "uses_gpu": False,
            },
        ),
        SchedulerTaskConfig(
            task_id="embodiment_outbox_periodic",
            trigger_mode=TriggerMode.INTERVAL,
            trigger_config={"seconds": 1},
            intent_template={
                "skill_name": "embodiment_outbox_dispatch",
                "args": {"limit": 100},
                "proposer_node_id": "scheduler",
            },
            priority=TaskPriority.HIGH,
            mode_allowlist=["normal", "focus", "gaming", "sleep"],
            enabled=True,
            max_consecutive_failures=3,
            description="Dispatch the canonical Host physical-command outbox",
            resource_profile={
                "resource_class": "embodiment_io",
                "uses_llm": False,
                "uses_gpu": False,
            },
        ),
    ]
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
        "ACCEPTED": "SUCCEEDED",
        "APPLIED": "SUCCEEDED",
        "COMMITTED": "SUCCEEDED",
        "REPLAYED": "SUCCEEDED",
        "ERROR": "FAILED",
        "REJECTED": "BLOCKED",
        "CONFLICT": "BLOCKED",
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


def _compose_host_runtime_context(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    embodiment_runtime: Any,
    environment_observation_provider: Callable[..., Mapping[str, Any]],
) -> RuntimeContextApplicationService:
    """Build the sole app-local World/Scene/Intent/Guardian object graph.

    The durable Host embodiment lease is reused as the write fence for every
    context journal.  Construction is deliberately fail-closed: routes are not
    exposed when the lease, schema, source verifier or Intent authorities are
    unavailable.
    """

    runtime_scope_id = str(
        getattr(embodiment_runtime, "runtime_scope_id", "") or ""
    ).strip()
    if not runtime_scope_id:
        raise RuntimeError("HOST_RUNTIME_CONTEXT_SCOPE_UNAVAILABLE")

    def runtime_authority_provider() -> dict[str, Any]:
        renew = getattr(embodiment_runtime, "renew_authority", None)
        if not callable(renew):
            raise RuntimeError("HOST_RUNTIME_CONTEXT_AUTHORITY_UNAVAILABLE")
        claim = renew()
        if not isinstance(claim, Mapping):
            raise RuntimeError("HOST_RUNTIME_CONTEXT_AUTHORITY_INVALID")
        return {**dict(claim), "runtime_scope_id": runtime_scope_id}

    event_store = getattr(task_runtime, "event_store", None)
    command_repository = getattr(task_runtime, "command_repository", None)
    policy_version = "natsume.intent-arbiter.production.v1"
    policy_identity = IntentPolicyIdentityV1(
        policy_ref="policy://intent-arbiter/production-v1",
        policy_digest=hashlib.sha256(policy_version.encode("utf-8")).hexdigest(),
    )
    observation_journal = SQLiteWorldObservationJournal(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
        source_event_verifier=RuntimeEventWorldSourceVerifier(event_store),
    )
    composition = build_runtime_context_composition(
        event_store=event_store,
        observation_port=observation_journal,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
        intent_policy_identity=policy_identity,
        intent_authority_port_factory=(
            lambda scene_resolver: ProductionIntentAuthorityPort(
                command_repository=command_repository,
                scene_resolver=scene_resolver,
                governor_ledger=ledger,
                policy_identity=policy_identity,
            )
        ),
        environment_observation_provider=environment_observation_provider,
    )
    return RuntimeContextApplicationService(composition)


def _runtime_environment_observation_provider(
    mode_transition_service: Any,
) -> Callable[..., Mapping[str, Any]]:
    """Freeze one app-local environment reader for the Runtime Context graph.

    Time comes from the deterministic environment snapshot.  Activity comes
    from the exact ``ModeTransitionService`` instance created by this ASGI
    lifespan; the returned closure never looks up a process-global service.
    """

    status_reader = getattr(mode_transition_service, "get_current_status", None)
    if not callable(status_reader):
        raise RuntimeError("HOST_RUNTIME_CONTEXT_MODE_AUTHORITY_UNAVAILABLE")

    def observe(*, now_ms: int) -> Mapping[str, Any]:
        from host_brain.runtime.environment_state import (  # noqa: PLC0415
            build_environment_snapshot,
        )

        snapshot = dict(build_environment_snapshot(now_ms=int(now_ms)))
        raw_status = status_reader()
        if not isinstance(raw_status, Mapping):
            raise RuntimeError("HOST_RUNTIME_CONTEXT_MODE_AUTHORITY_INVALID")
        mode = str(raw_status.get("sys_mode") or "").strip().lower()
        activity = {
            "normal": "NORMAL",
            "focus": "FOCUS",
            "gaming": "GAMING",
            "sleep": "SLEEP",
            "maintenance": "MAINTENANCE",
            "locked": "MAINTENANCE",
        }.get(mode)
        if activity is None:
            raise RuntimeError("HOST_RUNTIME_CONTEXT_MODE_UNSUPPORTED")
        snapshot["activity_state"] = activity
        snapshot["mode_identity"] = mode
        return snapshot

    return observe


def create_app() -> FastAPI:
    from host_brain.app.tribe_composition import configure_tribe_application_ports

    mode_transition_service: Any | None = None

    def mode_transition_service_provider() -> Any | None:
        return mode_transition_service

    configure_tribe_application_ports()
    configure_process_service_ports()
    lifecycle_runtime_ports = build_lifecycle_runtime_application_ports()
    input_runtime_ports = build_input_runtime_application_ports(
        embodiment_runtime_provider=lambda: embodiment_runtime,
    )
    # This slot belongs to one FastAPI application factory invocation.  Route
    # providers close over it explicitly; there is no module-global service
    # locator and no app-state lookup inside the API registry.
    security_container: ProcessSecurityServiceContainer | None = None
    command_ingress_container: ProcessCommandIngressContainer | None = None
    memory_container: ProcessMemoryContainer | None = None
    task_runtime_container: ProcessTaskRuntimeContainer | None = None
    agent_workspace_container: workspace.ProcessAgentWorkspaceContainer | None = None
    governed_host_runtime: GovernedHostRuntime | None = None
    governed_internal_runtime: GovernedInternalRuntime | None = None
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
    mcp_degradation_broker: Any | None = None
    guardian_heartbeat_task: asyncio.Task[None] | None = None
    catalog_rollout_guardian_task: asyncio.Task[None] | None = None
    startup_probe_statuses: dict[str, Any] = {}

    def capture_startup_probe_statuses(statuses: Mapping[str, Any]) -> None:
        nonlocal startup_probe_statuses
        startup_probe_statuses = dict(statuses)

    def task_runtime_container_provider() -> ProcessTaskRuntimeContainer | None:
        return task_runtime_container

    def governed_command_application_provider() -> GovernedHostRuntime | None:
        return governed_host_runtime

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

    def capture_mode_transition_service(service: Any | None) -> None:
        nonlocal mode_transition_service
        mode_transition_service = service

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
        tool_broker_provider=get_tool_broker,
        tool_catalog_provider=get_tool_catalog,
        bond_engine_provider=resolve_bond_engine,
        bond_writer_provider=get_bond_dynamics_writer,
        mode_transition_service_provider=mode_transition_service_provider,
        embodiment_runtime_provider=embodiment_runtime_provider,
    )
    host_obs_route_ports = ObsRoutePorts(
        query_projection_store_provider=lambda: host_query_projection_store,
    )

    host_obs_read_ports = build_host_obs_read_ports(
        HostObsSourceRuntimePorts(
            mode_service_provider=lambda: mode_transition_service,
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

    async def edge_events_endpoint_with_runtime(
        request_body: EdgeEventsRequest,
    ) -> EdgeEventsResponse:
        # Keep the FastAPI signature to one request body. ``functools.partial``
        # exposes the bound ports dataclass as a second body parameter.
        return await edge_events_endpoint(
            request_body,
            input_runtime_ports=input_runtime_ports,
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
        nonlocal agent_workspace_container
        nonlocal governed_internal_runtime, internal_agent_command_service
        nonlocal task_review_command_service
        nonlocal internal_agent_ingress, scheduler_container
        nonlocal mode_transition_service
        nonlocal host_obs_ledger, host_query_projection_store
        nonlocal host_obs_capability_gap_telemetry
        nonlocal host_obs_capability_forge, host_obs_semantic_recaller
        nonlocal host_obs_capability_scorecards
        nonlocal host_obs_degradation_state_provider
        nonlocal host_obs_pentest_environment, host_obs_tribe_state_reader
        nonlocal host_obs_source_epoch
        nonlocal canon_container
        nonlocal life_command_authority, life_dispatchers
        nonlocal embodiment_runtime, embodiment_authority_task
        nonlocal runtime_context_composition, runtime_context_application
        nonlocal mcp_degradation_broker
        nonlocal guardian_heartbeat_task, catalog_rollout_guardian_task
        nonlocal startup_probe_statuses
        async with runtime_lifespan(
            get_ledger=get_ledger,
            logger=logger,
            lifecycle_ports=lifecycle_runtime_ports,
            canon_service_provider=canon_service_provider,
            mode_transition_service_sink=capture_mode_transition_service,
            startup_probe_sink=capture_startup_probe_statuses,
            degradation_state_provider=(
                lambda: host_obs_degradation_state_provider()
            ),
            start_service_heartbeat=False,
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
                    route_ports=route_ports,
                    degradation_capability_port_provider=(
                        degradation_capability_port_provider
                    ),
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
                # Host source readers may observe these services, but never
                # construct them from a GET request.
                host_obs_ledger = ledger
                host_obs_source_epoch = HostObsSourceEpoch.create()
                host_obs_tribe_state_reader = (
                    require_process_tribe_runtime().state_registry.snapshot
                )
                try:
                    host_obs_capability_gap_telemetry = (
                        get_capability_gap_telemetry()
                    )
                    host_obs_capability_forge = get_capability_forge_service()
                except Exception:
                    logger.warning(
                        "HOST_OBS_CAPABILITY_SOURCE_PREWARM_FAILED",
                        exc_info=True,
                    )
                try:
                    host_obs_semantic_recaller = get_semantic_recaller()
                except Exception:
                    logger.warning(
                        "HOST_OBS_SEMANTIC_RECALL_SOURCE_PREWARM_FAILED",
                        exc_info=True,
                    )
                try:
                    host_obs_capability_scorecards = (
                        get_capability_scorecard_provider()
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
                task_runtime = build_process_task_runtime_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                    reconciliation_public_keys=(
                        runtime_settings.task_reconciliation_public_keys()
                    ),
                    terminal_reconciliation_auto_enabled=bool(
                        getattr(
                            runtime_settings,
                            "task_terminal_reconciliation_auto_enabled",
                            False,
                        )
                    ),
                )
                # Digital Life repositories are migration-gated.  The task
                # runtime owns canonical migration application, so Life must
                # be composed only after that startup gate has completed.
                life_command_authority = _compose_life_command_authority(ledger)
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
                runtime_context_application = _compose_host_runtime_context(
                    task_runtime=task_runtime,
                    ledger=ledger,
                    embodiment_runtime=embodiment_runtime,
                    environment_observation_provider=(
                        _runtime_environment_observation_provider(
                            mode_transition_service
                        )
                    ),
                )
                runtime_context_composition = runtime_context_application.composition
                from host_brain.governor.action_preview import (  # noqa: PLC0415
                    ActionPreviewService,
                )
                from host_brain.orchestration.node_grants import (  # noqa: PLC0415
                    DurableNodeGrantVerifier,
                )
                from host_brain.runtime.action_preview_resolver import (  # noqa: PLC0415
                    ProductionActionPreviewAuthorityResolver,
                )

                action_preview_authority_resolver = (
                    ProductionActionPreviewAuthorityResolver(
                        command_repository=task_runtime.command_repository,
                        task_store=task_runtime.task_store,
                        node_grant_verifier=DurableNodeGrantVerifier(
                            ledger=ledger,
                            review_store=task_runtime.review_continuation_store,
                        ),
                        runtime_context_application=runtime_context_application,
                    )
                )

                action_preview_service = ActionPreviewService(
                    runtime_event_store=runtime_context_composition.event_store,
                    guardian=runtime_context_composition.guardian,
                    authority_resolver=action_preview_authority_resolver,
                )
                # The process-owned MCP facade may be constructed while the
                # Skill registry is prewarmed.  Bind it explicitly to this
                # application's lifespan-owned Guardian before any route can
                # execute a provider capability.
                mcp_degradation_broker = get_mcp_tool_broker()
                mcp_degradation_broker.bind_degradation_capability_port(
                    runtime_context_composition.guardian
                )
                startup_observed_at_ms = now_ms()
                runtime_context_composition.guardian.observe_startup_batch(
                    startup_probe_statuses,
                    source_ref=(
                        "startup-probe://host/"
                        + hashlib.sha256(
                            (
                                f"{runtime_context_composition.guardian.snapshot().snapshot_digest}:"
                                f"{startup_observed_at_ms}"
                            ).encode("utf-8")
                        ).hexdigest()
                    ),
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
                    ),
                    name="host-runtime-guardian-health-heartbeat",
                )
                catalog_operation_id = str(
                    os.environ.get("HOST_SERVICE_CATALOG_OPERATION_ID") or ""
                ).strip()
                if catalog_operation_id:
                    from host_brain.service_catalog.rollout_authority import (  # noqa: PLC0415
                        ServiceCatalogRolloutGuardianObserver,
                        ServiceCatalogRolloutStore,
                    )

                    catalog_guardian = runtime_context_composition.guardian
                    catalog_rollout_store = ServiceCatalogRolloutStore(
                        task_runtime.canonical_db_path
                    )
                    catalog_rollout_observer = (
                        ServiceCatalogRolloutGuardianObserver(
                            store=catalog_rollout_store,
                            guardian=catalog_guardian,
                        )
                    )
                    application.state.service_catalog_rollout_store = (
                        catalog_rollout_store
                    )
                    application.state.service_catalog_rollout_observer = (
                        catalog_rollout_observer
                    )

                    async def observe_catalog_rollout_receipt() -> None:
                        try:
                            for _attempt in range(240):
                                receipt = await asyncio.to_thread(
                                    catalog_rollout_store.terminal_receipt,
                                    catalog_operation_id,
                                )
                                if receipt is not None:
                                    catalog_rollout_observer.observe_operation(
                                        catalog_operation_id
                                    )
                                    return
                                await asyncio.sleep(0.25)
                            logger.error(
                                "HOST_SERVICE_CATALOG_TERMINAL_RECEIPT_TIMEOUT",
                                extra={"operation_id": catalog_operation_id},
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.error(
                                "HOST_SERVICE_CATALOG_GUARDIAN_OBSERVE_FAILED",
                                extra={"operation_id": catalog_operation_id},
                                exc_info=True,
                            )

                    catalog_rollout_guardian_task = asyncio.create_task(
                        observe_catalog_rollout_receipt(),
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
                logical_canonical_owner = runtime_settings.task_runtime_canonical_owner or "host-task-runtime"
                canonical_owner = task_runtime.execution_owner_id(logical_canonical_owner)
                agent_workspace_container = workspace.compose_agent_workspace_application_scope(
                    application, task_runtime, runtime_settings, canonical_owner
                )
                governed_skill_resource_manager = get_resource_manager(
                    create_if_missing=False,
                )
                governed_skill_episodic_store = episodic_projection_store
                governed_skill_semantic_recaller = host_obs_semantic_recaller
                governed_runtime = build_governed_host_runtime(
                    task_runtime_container=task_runtime,
                    ledger=ledger,
                    skill_registry=get_skill_registry(),
                    token_service=get_token_service(),
                    capability_broker=get_capability_broker(),
                    model_attempt_service=ApplicationModelAttemptServicePort(),
                    production_authority_verifier=verify_durable_production_authority,
                    resource_manager=governed_skill_resource_manager,
                    episodic_store=governed_skill_episodic_store,
                    semantic_recaller=governed_skill_semantic_recaller,
                    cancellation_authority_verifier=cancellation_authority_verifier,
                    runtime_router=CanonicalRuntimeRouter(),
                    owner_id=canonical_owner,
                    bounded_fanout_enabled=runtime_settings.task_runtime_bounded_fanout_enabled,
                    atomic_graph_success_enabled=runtime_settings.task_runtime_atomic_graph_success_enabled,
                    runtime_context_application=runtime_context_application,
                    intent_admission_verifier=DurableIntentAdmissionVerifier(
                        authority_port=(
                            runtime_context_composition.intent_authority_port
                        ),
                        intent_arbiter=runtime_context_composition.intent_arbiter,
                        event_store=runtime_context_composition.event_store,
                        review_store=task_runtime.review_store,
                    ),
                    degradation_capability_port=(
                        runtime_context_composition.guardian
                    ),
                    action_preview_service=action_preview_service,
                )
                canonical_agent_executor_kwargs: dict[str, Any] = {
                    "model_attempt_service": get_model_attempt_service(),
                    "prompt_registry": get_prompt_registry(),
                }
                if agent_workspace_container is not None:
                    canonical_agent_executor_kwargs["workspace_runtime"] = agent_workspace_container.runtime
                internal_runtime = build_governed_internal_runtime(
                    task_runtime_container=task_runtime,
                    ledger=ledger,
                    token_service=get_token_service(),
                    result_repository=task_runtime.agent_result_repository,
                    runtime_router=CanonicalRuntimeRouter(),
                    owner_id=canonical_owner,
                    canonical_executor=AgentRuntimeExecutor(**canonical_agent_executor_kwargs),
                    cancellation_authority_verifier=cancellation_authority_verifier,
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
                        get_ledger=get_ledger,
                        life_snapshot_reader=get_life_snapshot_reader,
                        retention_policy=None,
                        ui_pusher=scheduler_ui_pusher,
                        execution_context=execution_context,
                        runtime_context_application=runtime_context_application,
                        action_preview_service=action_preview_service,
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
                    token_service=get_token_service(),
                    evaluator=command_ingress.evaluator,
                    claim_result_store=command_ingress.store,
                    intent_engine=get_intent_engine(),
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
                    seed_definitions=_scheduler_seed_configs(),
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
                )
                runtime_group = GovernedRuntimeGroup(
                    {
                        "host": governed_runtime,
                        "internal_agent": internal_runtime,
                        "scheduler": scheduler.runtime,
                    },
                    review_store=task_runtime.review_continuation_store,
                    review_decision_store=task_runtime.review_store,
                    command_repository=task_runtime.command_repository,
                    claim_result_store=command_ingress.store,
                    review_continuation_worker_id=(
                        f"{task_runtime.recovery_id}::review-continuation"
                    ),
                    graph_cancellation_operator_enabled=(
                        cancellation_operator_enabled
                    ),
                    graph_cancellation_state_reader=task_runtime.task_store,
                )
                review_commands = TaskReviewCommandService(
                    review_store=task_runtime.review_store,
                    wake_continuation=runtime_group.wake_review_continuation,
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
                task_review_command_service = review_commands
                command_ingress_container = command_ingress
                memory_container = memory
                internal_agent_ingress = internal_ingress
                scheduler_container = scheduler
                application.state.task_runtime_container = task_runtime
                application.state.governed_host_runtime = governed_runtime
                application.state.governed_internal_runtime = internal_runtime
                application.state.governed_scheduler_runtime = scheduler.runtime
                application.state.scheduler_container = scheduler
                application.state.task_review_command_service = review_commands
                application.state.task_graph_cancellation_command_service = (
                    runtime_group
                )
                application.state.task_operation_reconciliation_command_service = (
                    task_runtime.operation_reconciliation_service
                    if operation_reconciliation_operator_enabled
                    else None
                )
                application.state.task_effect_reconciliation_command_service = (
                    task_runtime.effect_reconciliation_service
                    if effect_reconciliation_operator_enabled
                    else None
                )
                application.state.task_terminal_reconciliation_command_service = (
                    task_runtime.terminal_reconciliation_service
                    if terminal_reconciliation_operator_enabled
                    else None
                )
                application.state.task_terminal_reconciliation_read_authority = (
                    terminal_reconciliation_read_authority
                )
                application.state.command_ingress_container = command_ingress
                application.state.memory_service_container = memory
                yield
            finally:
                scheduler_container = None
                host_obs_source_epoch = None
                host_obs_tribe_state_reader = None
                host_obs_capability_forge = None
                host_obs_capability_gap_telemetry = None
                host_obs_semantic_recaller = None
                host_obs_capability_scorecards = None
                host_obs_degradation_state_provider = lambda: None
                host_obs_pentest_environment = {}
                host_obs_ledger = None
                host_query_projection_store = None
                internal_agent_ingress = None
                internal_agent_command_service = None
                task_review_command_service = None
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
                application.state.task_review_command_service = None
                application.state.task_graph_cancellation_command_service = None
                application.state.task_operation_reconciliation_command_service = None
                application.state.task_effect_reconciliation_command_service = None
                application.state.task_terminal_reconciliation_command_service = None
                application.state.task_terminal_reconciliation_read_authority = None
                application.state.governed_host_runtime = None
                application.state.task_runtime_container = None
                application.state.service_catalog_rollout_store = None
                application.state.service_catalog_rollout_observer = None
                if (
                    mcp_degradation_broker is not None
                    and runtime_context_composition is not None
                ):
                    try:
                        mcp_degradation_broker.unbind_degradation_capability_port(
                            runtime_context_composition.guardian
                        )
                    except Exception:
                        logger.error(
                            "MCP_DEGRADATION_AUTHORITY_UNBIND_FAILED",
                            exc_info=True,
                        )
                    finally:
                        mcp_degradation_broker = None
                application.state.runtime_context_composition = None
                runtime_context_application = None
                runtime_context_composition = None
                try:
                    if catalog_rollout_guardian_task is not None:
                        catalog_rollout_guardian_task.cancel()
                        try:
                            await catalog_rollout_guardian_task
                        except asyncio.CancelledError:
                            pass
                        catalog_rollout_guardian_task = None
                    if guardian_heartbeat_task is not None:
                        guardian_heartbeat_task.cancel()
                        try:
                            await guardian_heartbeat_task
                        except asyncio.CancelledError:
                            pass
                        guardian_heartbeat_task = None
                    if scheduler is not None:
                        try:
                            # Producers stop before runtime admission closes.
                            await scheduler.stop_clock()
                        except Exception:
                            logger.error(
                                "HOST_SCHEDULER_CLOCK_CLOSE_FAILED",
                                exc_info=True,
                            )
                    workspace.quiesce_agent_workspace_application_scope(
                        application, agent_workspace_container, task_runtime
                    )
                    agent_workspace_container = None
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
                        if embodiment_authority_task is not None:
                            embodiment_authority_task.cancel()
                            try:
                                await embodiment_authority_task
                            except asyncio.CancelledError:
                                pass
                            embodiment_authority_task = None
                        _reset_life_command_authority()
                        life_command_authority = None
                        life_dispatchers = None
                        if embodiment_runtime is not None:
                            embodiment_runtime.close()
                        embodiment_runtime = None
                        application.state.embodiment_runtime = None
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
        get_bond_writer=get_bond_dynamics_writer,
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
        governed_command_application_provider=(
            governed_command_application_provider
        ),
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
    application.state.security_service_container = None
    application.state.command_ingress_container = None
    application.state.memory_service_container = None
    application.state.task_runtime_container = None
    application.state.service_catalog_rollout_store = None
    application.state.service_catalog_rollout_observer = None
    application.state.agent_workspace_container = None
    application.state.task_effect_reconciliation_command_service = None
    application.state.task_terminal_reconciliation_command_service = None
    application.state.task_terminal_reconciliation_read_authority = None
    application.state.governed_host_runtime = None
    application.state.governed_internal_runtime = None
    application.state.governed_scheduler_runtime = None
    application.state.scheduler_container = None
    application.state.task_review_command_service = None
    application.state.task_graph_cancellation_command_service = None
    application.state.task_operation_reconciliation_command_service = None
    application.state.canon_container = None
    application.state.embodiment_runtime = None
    application.state.runtime_context_composition = None
    application.state.lifecycle_runtime_ports = lifecycle_runtime_ports
    application.state.input_runtime_ports = input_runtime_ports
    application.state.task_runtime_container_provider = (
        task_runtime_container_provider
    )
    return application


app = create_app()


__all__ = ["app", "create_app"]
