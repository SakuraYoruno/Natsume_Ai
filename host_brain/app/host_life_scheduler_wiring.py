"""Focused Life and Scheduler wiring used by the Host ASGI composition root."""

from __future__ import annotations

import hashlib
import json
from functools import partial
from typing import Any, Callable, Mapping, Protocol

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.scheduler.task_registry import (
    SchedulerTaskConfig,
    TaskPriority,
    TriggerMode,
)


class LifeCommandServicePort(Protocol):
    def submit(self, command: Any) -> Any: ...

    def submit_next(self, person_id: str, command_factory: Any) -> Any: ...

    def adopt_all_legacy_projections(self, *, adopted_at_ms: int) -> Any: ...


class LifeSnapshotReaderPort(Protocol):
    def get_snapshot(self, person_id: str) -> Any: ...

    def get_receipt(self, command_id: str) -> Any | None: ...

    def list_companion_proactive_targets(
        self,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, Any]]: ...


SCHEDULER_HANDLER_RESULT_INVALID = "SCHEDULER_HANDLER_RESULT_INVALID"

# This was the pre-DL-06 timer-driven proactive route.  New definitions are
# already excluded by the registry scheduler policy; the composition root also
# disables an old persisted head during startup so it cannot survive upgrade.
RETIRED_LEGACY_SCHEDULER_TASK_IDS = ("proactive_talk_periodic",)


class SchedulerHandlerResultError(RuntimeError):
    """A real Scheduler handler returned no trustworthy terminal meaning."""

    def __init__(self, detail: str) -> None:
        self.reason_code = SCHEDULER_HANDLER_RESULT_INVALID
        self.detail = str(detail or "Scheduler handler result is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def compose_life_command_authority(
    ledger: Any,
    *,
    container_builder: Callable[..., Any],
    provider: Any,
    clock_ms: Callable[[], int],
) -> tuple[LifeCommandServicePort, LifeSnapshotReaderPort]:
    """Resolve the frozen Digital Life command boundary at process startup."""

    container = container_builder(ledger=ledger, adopted_at_ms=int(clock_ms()))
    provider.install(container)
    return container.command_service, container.snapshot_reader


def compose_life_dispatchers(
    ledger: Any,
    *,
    embodiment_runtime: Any,
    action_preview_service: Any | None = None,
    agent_runtime_ports: AgentRuntimeApplicationPorts | None = None,
    relationship_lifecycle_service_provider: Callable[[], Any] | None = None,
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

    if not isinstance(agent_runtime_ports, AgentRuntimeApplicationPorts):
        raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
    repository = LifeCommandRepository.from_ledger(ledger)
    return (
        LifeProjectionDispatcher(
            repository,
            partial(handle_life_projection, agent_runtime_ports=agent_runtime_ports),
        ),
        LifeOutboxDispatcher(
            repository,
            LifeDeliveryTransport(
                ledger=ledger,
                embodiment_runtime=embodiment_runtime,
                action_preview_service=action_preview_service,
            ),
            relationship_lifecycle_service_provider=(
                relationship_lifecycle_service_provider
            ),
        ),
    )


def reset_life_command_authority(*, provider: Any) -> None:
    provider.clear()


def submit_life_tick_command(
    *,
    occurrence: Any,
    definition: Any,
    command: Any,
    execution_context: Mapping[str, Any],
    runtime_context_application: RuntimeContextApplicationService,
    life_command_service: LifeCommandServicePort,
    life_snapshot_reader: LifeSnapshotReaderPort,
    clock_ms: Callable[[], int],
) -> dict[str, Any]:
    """Translate one governed scheduler occurrence into one Life command."""

    from natsume_contracts.life import LifeCommand  # noqa: PLC0415

    intent = dict(definition.intent_template)
    args = dict(intent.get("args") or {})
    args.update(dict(occurrence.payload or {}))
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
        result = scheduler_handler_result(replayed_receipt)
        result["replayed"] = True
        return result
    person_id, target_selection = _select_life_tick_person_id(
        args=args,
        occurrence_id=str(occurrence.occurrence_id),
        now_ms=int(clock_ms()),
        frozen_runtime_context=frozen_runtime_context,
        life_snapshot_reader=life_snapshot_reader,
    )
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
            int(clock_ms()),
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
                "companion_target_selection": target_selection,
            },
        )

    receipt = life_command_service.submit_next(person_id, _command_factory)
    return scheduler_handler_result(receipt)


def _select_life_tick_person_id(
    *,
    args: Mapping[str, Any],
    occurrence_id: str,
    now_ms: int,
    frozen_runtime_context: Mapping[str, Any],
    life_snapshot_reader: LifeSnapshotReaderPort,
) -> tuple[str, str]:
    """Select one scope-compatible canonical target per scheduler occurrence.

    The Scheduler still produces exactly one LifeCommand, so a single clock
    remains the authority.  A stable occurrence digest distributes ticks over
    currently registered targets without an in-process queue; receipt replay
    occurs before this lookup and therefore never changes a committed target.
    """

    explicit = str(args.get("person_id") or "").strip()
    if explicit and explicit.lower() != "default":
        return explicit, "EXPLICIT_PERSON_ID"
    list_targets = getattr(life_snapshot_reader, "list_companion_proactive_targets", None)
    if not callable(list_targets):
        return "default", "REGISTRY_READER_UNAVAILABLE"
    scene = dict(frozen_runtime_context.get("scene_decision_v2") or {})
    scene_workspace = str(scene.get("workspace_id") or "").strip()
    scene_subject = str(scene.get("subject_id") or "").strip()
    try:
        candidates = list_targets(limit=1_000)
    except Exception:
        return "default", "REGISTRY_READ_DEGRADED"
    compatible = [
        dict(row)
        for row in candidates
        if isinstance(row, Mapping)
        and str(row.get("aggregate_id") or "").strip()
        and (
            not scene_workspace
            or str(row.get("workspace_id") or "").strip() == scene_workspace
        )
        and (
            not scene_subject
            or str(row.get("relationship_subject_id") or "").strip()
            == scene_subject
        )
    ]
    if not compatible:
        return "default", "NO_SCENE_COMPATIBLE_REGISTERED_TARGET"
    from host_brain.digital_life.companion_proactive import (  # noqa: PLC0415
        PROACTIVE_SILENCE_GRACE_MS,
    )

    observed_at_ms = max(0, int(now_ms))
    due_silence = [
        row
        for row in compatible
        if _pending_proactive_silence_is_due(
            row,
            now_ms=observed_at_ms,
            grace_ms=PROACTIVE_SILENCE_GRACE_MS,
        )
    ]
    ready_for_contact = [
        row
        for row in compatible
        if not int(row.get("pending_proactive_opened_at_ms") or 0)
    ]
    candidates = due_silence or ready_for_contact
    if not candidates:
        return "default", "NO_SCENE_COMPATIBLE_REGISTERED_TARGET"
    candidates.sort(key=lambda row: str(row.get("aggregate_id") or ""))
    digest = hashlib.sha256(str(occurrence_id).encode("utf-8")).digest()
    selected = candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
    selection = (
        "CANONICAL_PROACTIVE_SILENCE_MAINTENANCE_TARGET"
        if due_silence
        else "CANONICAL_REGISTERED_TARGET"
    )
    return str(selected["aggregate_id"]), selection


def _pending_proactive_silence_is_due(
    candidate: Mapping[str, Any],
    *,
    now_ms: int,
    grace_ms: int,
) -> bool:
    opened_at_ms = max(0, int(candidate.get("pending_proactive_opened_at_ms") or 0))
    return bool(opened_at_ms and max(0, int(now_ms)) >= opened_at_ms + max(1, int(grace_ms)))


def scheduler_seed_configs(
    *,
    settings: Any,
    security_immune_agent_provider: Callable[[], Any],
    logger: Any,
) -> list[SchedulerTaskConfig]:
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
        policy = security_immune_agent_provider().policy
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


def scheduler_handler_result(value: Any) -> dict[str, Any]:
    """Normalize only an explicit, canonical Scheduler terminal result.

    A handler may already have crossed an external-effect boundary before its
    return value is inspected.  Missing or ambiguous output is consequently
    an unknown outcome, not evidence of success (or of definite failure).
    """

    if isinstance(value, Mapping):
        result = dict(value)
    else:
        result: dict[str, Any] | None = None
        for method_name in ("as_dict", "to_dict", "model_dump"):
            method = getattr(value, method_name, None)
            if callable(method):
                candidate = method()
                if isinstance(candidate, Mapping):
                    result = dict(candidate)
                    break
    if not result:
        raise SchedulerHandlerResultError(
            "handler must return a non-empty mapping or serializable object"
        )
    if "status" not in result:
        raise SchedulerHandlerResultError("handler result requires explicit status")
    raw = str(result.get("status") or "").strip().upper()
    normalized = {
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
    if normalized not in {
        "EXECUTED",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED",
        "DEGRADED",
        "SKIPPED",
    }:
        raise SchedulerHandlerResultError(
            f"handler result status is invalid: {raw or '<empty>'}"
        )
    result["status"] = normalized
    if normalized == "SKIPPED":
        reason_code = str(result.get("reason_code") or "").strip().upper()
        if not reason_code:
            raise SchedulerHandlerResultError(
                "SKIPPED handler result requires explicit reason_code"
            )
        result["reason_code"] = reason_code
    _assert_canonical_scheduler_result(result)
    return result


def proactive_scheduler_handler_result(value: Any) -> dict[str, Any]:
    """Preserve the explicit terminal meaning returned by proactive dispatch."""

    result = scheduler_handler_result(value)
    if result["status"] in {"EXECUTED", "SUCCEEDED"} and result.get("success") is False:
        raise SchedulerHandlerResultError(
            "proactive result reports success status with success=false"
        )
    return result


def distillation_scheduler_handler_result(report: Any) -> dict[str, Any]:
    """Project ``BatchDistillationReport`` counters into one real outcome."""

    total = _non_negative_result_int(report, "total_candidates")
    distilled = _non_negative_result_int(report, "distilled_count")
    skipped = _non_negative_result_int(report, "skipped_count")
    failed = _non_negative_result_int(report, "failed_count")
    duration_ms = _non_negative_result_int(report, "duration_ms")
    if distilled + skipped + failed != total:
        raise SchedulerHandlerResultError(
            "distillation counters do not account for every candidate"
        )
    payload: dict[str, Any] = {
        "total_candidates": total,
        "distilled_count": distilled,
        "skipped_count": skipped,
        "failed_count": failed,
        "duration_ms": duration_ms,
    }
    raw_ts = _result_field(report, "ts_ms")
    if raw_ts is not None:
        payload["ts_ms"] = _non_negative_exact_int(raw_ts, "distillation.ts_ms")
    if total == 0:
        payload.update(
            {
                "status": "SKIPPED",
                "reason_code": "DISTILLATION_NO_CANDIDATES",
                "summary": "No episodic-memory candidates were due",
            }
        )
    elif failed == total:
        payload.update(
            {
                "status": "FAILED",
                "reason_code": "DISTILLATION_BATCH_FAILED",
                "summary": f"All {total} distillation candidates failed",
            }
        )
    elif failed > 0:
        payload.update(
            {
                "status": "DEGRADED",
                "reason_code": "DISTILLATION_PARTIAL_FAILURE",
                "summary": f"Distilled {distilled} of {total}; {failed} failed",
            }
        )
    elif distilled == 0:
        payload.update(
            {
                "status": "SKIPPED",
                "reason_code": "DISTILLATION_NO_ELIGIBLE_CANDIDATES",
                "summary": f"All {total} candidates were skipped",
            }
        )
    else:
        payload.update(
            {
                "status": "SUCCEEDED",
                "reason_code": None,
                "summary": f"Distilled {distilled} of {total} candidates",
            }
        )
    return scheduler_handler_result(payload)


def security_immune_scheduler_handler_result(value: Any) -> dict[str, Any]:
    """Project the real SecurityImmune snapshot without inventing success."""

    snapshot = _result_mapping(value, field_name="security immune snapshot")
    enabled = snapshot.get("enabled")
    if type(enabled) is not bool:
        raise SchedulerHandlerResultError(
            "security immune snapshot requires boolean enabled"
        )
    run_count = _non_negative_exact_int(
        snapshot.get("run_count"),
        "security_immune.run_count",
    )
    asset_count = _non_negative_exact_int(
        snapshot.get("asset_count"),
        "security_immune.asset_count",
    )
    incident_count = _non_negative_exact_int(
        snapshot.get("incident_count"),
        "security_immune.incident_count",
    )
    last_run = snapshot.get("last_run_ts_ms")
    if last_run is not None:
        last_run = _non_negative_exact_int(
            last_run,
            "security_immune.last_run_ts_ms",
        )
    provider_rows = snapshot.get("provider_statuses", [])
    if not isinstance(provider_rows, list) or any(
        not isinstance(row, Mapping) for row in provider_rows
    ):
        raise SchedulerHandlerResultError(
            "security immune provider_statuses must be an array of objects"
        )
    allowed_provider_statuses = {"ok", "skipped", "degraded"}
    provider_statuses: list[dict[str, str]] = []
    for row in provider_rows:
        provider_id = str(row.get("provider_id") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        if not provider_id or status not in allowed_provider_statuses:
            raise SchedulerHandlerResultError(
                "security immune provider status is missing or unknown"
            )
        provider_statuses.append({"provider_id": provider_id, "status": status})

    last_error = str(snapshot.get("last_error") or "").strip()
    degraded_providers = sorted(
        row["provider_id"] for row in provider_statuses if row["status"] == "degraded"
    )
    skipped_providers = sorted(
        row["provider_id"] for row in provider_statuses if row["status"] == "skipped"
    )
    result: dict[str, Any] = {
        "enabled": enabled,
        "run_count": run_count,
        "last_run_ts_ms": last_run,
        "asset_count": asset_count,
        "incident_count": incident_count,
        "provider_statuses": provider_statuses,
        "degraded_provider_ids": degraded_providers,
        "skipped_provider_ids": skipped_providers,
    }
    if not enabled:
        result.update(
            {
                "status": "SKIPPED",
                "reason_code": "SECURITY_IMMUNE_DISABLED",
                "summary": "Security immune scan is disabled",
            }
        )
    elif last_error:
        result.update(
            {
                "status": "FAILED",
                "reason_code": "SECURITY_IMMUNE_SCAN_FAILED",
                "error_digest": "sha256:"
                + hashlib.sha256(last_error.encode("utf-8")).hexdigest(),
                "summary": "Security immune scan failed",
            }
        )
    elif last_run is None or run_count < 1:
        raise SchedulerHandlerResultError(
            "enabled security immune scan has no completed run identity"
        )
    elif degraded_providers:
        result.update(
            {
                "status": "DEGRADED",
                "reason_code": "SECURITY_IMMUNE_PROVIDER_DEGRADED",
                "summary": (
                    "Security immune scan completed with degraded providers: "
                    + ", ".join(degraded_providers)
                ),
            }
        )
    else:
        result.update(
            {
                "status": "SUCCEEDED",
                "reason_code": None,
                "summary": (
                    f"Security immune scan completed: {asset_count} assets, "
                    f"{incident_count} incidents"
                ),
            }
        )
    return scheduler_handler_result(result)


def build_agent_immune_scheduler_handler(
    *,
    degradation_state_provider: Callable[[], Any] | None,
    ledger_provider: Callable[[], Any],
    dependency_probe_provider: Callable[..., Any],
    workspace_cleanup_health_provider_resolver: Callable[[], Any],
) -> Callable[..., Any]:
    """Bind the health-based immune proposal handler to app-local read ports."""

    async def run(**_: Any) -> dict[str, Any]:
        from host_brain.agent_runtime.immune import (  # noqa: PLC0415
            build_immune_proposal_from_report,
        )
        from host_brain.app.health_routes import (  # noqa: PLC0415
            build_live_health_report,
        )

        proposal = build_immune_proposal_from_report(
            await build_live_health_report(
                degradation_state_provider=degradation_state_provider,
                ledger_provider=ledger_provider,
                dependency_probe_provider=dependency_probe_provider,
                workspace_cleanup_health_provider=(
                    workspace_cleanup_health_provider_resolver()
                ),
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

    return run


def _result_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        result = dict(value)
    else:
        result = None
        for method_name in ("as_dict", "to_dict", "model_dump"):
            method = getattr(value, method_name, None)
            if callable(method):
                candidate = method()
                if isinstance(candidate, Mapping):
                    result = dict(candidate)
                    break
    if not result:
        raise SchedulerHandlerResultError(f"{field_name} is empty or not serializable")
    return result


def _result_field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _non_negative_result_int(value: Any, field_name: str) -> int:
    return _non_negative_exact_int(
        _result_field(value, field_name),
        f"distillation.{field_name}",
    )


def _non_negative_exact_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchedulerHandlerResultError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _assert_canonical_scheduler_result(value: Mapping[str, Any]) -> None:
    try:
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SchedulerHandlerResultError(
            "handler result must be finite canonical JSON"
        ) from exc


__all__ = [
    "LifeCommandServicePort",
    "LifeSnapshotReaderPort",
    "RETIRED_LEGACY_SCHEDULER_TASK_IDS",
    "SCHEDULER_HANDLER_RESULT_INVALID",
    "SchedulerHandlerResultError",
    "build_agent_immune_scheduler_handler",
    "compose_life_command_authority",
    "compose_life_dispatchers",
    "distillation_scheduler_handler_result",
    "proactive_scheduler_handler_result",
    "reset_life_command_authority",
    "scheduler_handler_result",
    "scheduler_seed_configs",
    "security_immune_scheduler_handler_result",
    "submit_life_tick_command",
]
