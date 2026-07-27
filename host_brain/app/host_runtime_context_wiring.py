"""Focused app-local World, Scene, Intent, and Guardian composition wiring."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from host_brain.composition.task_runtime import ProcessTaskRuntimeContainer
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.app.resource_manager_status_journal import (
    RuntimeEventResourceManagerStatusJournal,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.host_mode_policy_journal import (
    SQLiteHostModePolicyJournal,
)
from host_brain.persistence.long_term_goal_repository import (
    SQLiteLongTermGoalAuthority,
)
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
)
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.runtime.intent_authority import IntentPolicyIdentityV1
from host_brain.runtime.production_composition import build_runtime_context_composition
from host_brain.runtime.production_intent_authority import (
    ProductionIntentAuthorityPort,
)
from host_brain.runtime.world_source_verifier import RuntimeEventWorldSourceVerifier


@dataclass(frozen=True, slots=True)
class ServiceCatalogGuardianObservation:
    """Polling work composed for, but still owned by, the ASGI lifespan."""

    store: Any
    observer: Any
    operation_id: str
    logger: Any

    async def observe_terminal_receipt(self) -> None:
        try:
            for _attempt in range(240):
                receipt = await asyncio.to_thread(
                    self.store.terminal_receipt,
                    self.operation_id,
                )
                if receipt is not None:
                    self.observer.observe_operation(self.operation_id)
                    return
                await asyncio.sleep(0.25)
            self.logger.error(
                "HOST_SERVICE_CATALOG_TERMINAL_RECEIPT_TIMEOUT",
                extra={"operation_id": self.operation_id},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.error(
                "HOST_SERVICE_CATALOG_GUARDIAN_OBSERVE_FAILED",
                extra={"operation_id": self.operation_id},
                exc_info=True,
            )


def compose_service_catalog_guardian_observation(
    *,
    canonical_db_path: str,
    guardian: Any,
    operation_id: str,
    logger: Any,
) -> ServiceCatalogGuardianObservation:
    """Bind one staged Catalog operation to the app-local Guardian authority."""

    from host_brain.persistence.service_catalog_rollout_repository import (  # noqa: PLC0415
        SQLiteServiceCatalogRolloutRepository,
    )
    from host_brain.service_catalog.rollout_authority import (  # noqa: PLC0415
        ServiceCatalogRolloutGuardianObserver,
    )

    store = SQLiteServiceCatalogRolloutRepository(canonical_db_path)
    return ServiceCatalogGuardianObservation(
        store=store,
        observer=ServiceCatalogRolloutGuardianObserver(
            store=store,
            guardian=guardian,
        ),
        operation_id=str(operation_id),
        logger=logger,
    )


def compose_host_runtime_context(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    embodiment_runtime: Any,
    environment_observation_provider: Callable[..., Mapping[str, Any]],
) -> RuntimeContextApplicationService:
    """Build the sole app-local World/Scene/Intent/Guardian object graph."""

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
    long_term_goal_authority = SQLiteLongTermGoalAuthority(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    long_term_goal_authority.assert_schema_ready()
    composition = build_runtime_context_composition(
        event_store=event_store,
        observation_port=observation_journal,
        long_term_goal_authority=long_term_goal_authority,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
        intent_policy_identity=policy_identity,
        intent_authority_port_factory=(
            lambda scene_resolver: ProductionIntentAuthorityPort(
                command_repository=command_repository,
                scene_resolver=scene_resolver,
                governor_ledger=ledger,
                policy_identity=policy_identity,
                long_term_goal_authority=long_term_goal_authority,
            )
        ),
        environment_observation_provider=environment_observation_provider,
    )
    return RuntimeContextApplicationService(composition)


def compose_action_preview_service(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    runtime_context_application: RuntimeContextApplicationService,
) -> ActionPreviewService:
    """Bind Preview validation to the same durable Runtime Context authority."""

    composition = runtime_context_application.composition
    authority_resolver = ProductionActionPreviewAuthorityResolver(
        command_repository=task_runtime.command_repository,
        task_store=task_runtime.task_store,
        node_grant_verifier=DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=task_runtime.review_continuation_store,
        ),
        runtime_context_application=runtime_context_application,
    )
    return ActionPreviewService(
        runtime_event_store=composition.event_store,
        guardian=composition.guardian,
        authority_resolver=authority_resolver,
    )


def observe_runtime_startup(
    *,
    runtime_context_application: RuntimeContextApplicationService,
    startup_probe_statuses: Mapping[str, Any],
    observed_at_ms: int,
) -> None:
    """Append one deterministic startup observation to the Guardian journal."""

    guardian = runtime_context_application.composition.guardian
    guardian.observe_startup_batch(
        startup_probe_statuses,
        source_ref=(
            "startup-probe://host/"
            + hashlib.sha256(
                (
                    f"{guardian.snapshot().snapshot_digest}:"
                    f"{int(observed_at_ms)}"
                ).encode("utf-8")
            ).hexdigest()
        ),
        observed_at_ms=int(observed_at_ms),
    )


def bind_host_hard_state_runtime(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    embodiment_runtime: Any,
    host_lifecycle: Any,
) -> Mapping[str, object]:
    """Bind HardState only after migration and the Host fence are active."""

    runtime_scope_id = str(
        getattr(embodiment_runtime, "runtime_scope_id", "") or ""
    ).strip()
    if not runtime_scope_id:
        raise RuntimeError("HOST_HARD_STATE_SCOPE_UNAVAILABLE")

    def runtime_authority_provider() -> dict[str, Any]:
        renew = getattr(embodiment_runtime, "renew_authority", None)
        if not callable(renew):
            raise RuntimeError("HOST_HARD_STATE_AUTHORITY_UNAVAILABLE")
        claim = renew()
        if not isinstance(claim, Mapping):
            raise RuntimeError("HOST_HARD_STATE_AUTHORITY_INVALID")
        return {**dict(claim), "runtime_scope_id": runtime_scope_id}

    journal = SQLiteHardStateJournal(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    bind = getattr(host_lifecycle, "bind_hard_state_journal", None)
    if not callable(bind):
        raise RuntimeError("HOST_HARD_STATE_COMPOSITION_UNAVAILABLE")
    result = bind(journal)
    if not isinstance(result, Mapping):
        raise RuntimeError("HOST_HARD_STATE_BIND_RESULT_INVALID")
    policy_journal = SQLiteHostModePolicyJournal(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    bind_policy = getattr(host_lifecycle, "bind_mode_policy_journal", None)
    if not callable(bind_policy):
        raise RuntimeError("HOST_MODE_POLICY_COMPOSITION_UNAVAILABLE")
    policy_result = bind_policy(policy_journal)
    if not isinstance(policy_result, Mapping):
        raise RuntimeError("HOST_MODE_POLICY_BIND_RESULT_INVALID")
    return {
        **dict(result),
        "mode_policy_revision": policy_result.get("revision"),
        "mode_policy_event_digest": policy_result.get("event_digest"),
    }


def compose_resource_manager_status_journal(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    embodiment_runtime: Any,
) -> RuntimeEventResourceManagerStatusJournal:
    """Bind ResourceManager status to the exact live Host runtime fence."""

    runtime_scope_id = str(
        getattr(embodiment_runtime, "runtime_scope_id", "") or ""
    ).strip()
    if not runtime_scope_id:
        raise RuntimeError("HOST_RESOURCE_MANAGER_SCOPE_UNAVAILABLE")

    def runtime_authority_provider() -> dict[str, Any]:
        renew = getattr(embodiment_runtime, "renew_authority", None)
        if not callable(renew):
            raise RuntimeError("HOST_RESOURCE_MANAGER_AUTHORITY_UNAVAILABLE")
        claim = renew()
        if not isinstance(claim, Mapping):
            raise RuntimeError("HOST_RESOURCE_MANAGER_AUTHORITY_INVALID")
        return {**dict(claim), "runtime_scope_id": runtime_scope_id}

    event_store = getattr(task_runtime, "event_store", None)
    if event_store is None:
        raise RuntimeError("HOST_RESOURCE_MANAGER_EVENT_STORE_UNAVAILABLE")
    return RuntimeEventResourceManagerStatusJournal(
        event_store=event_store,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )


def start_host_runtime_context_services(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    embodiment_runtime: Any,
    host_lifecycle: Any,
    startup_probe_statuses: dict[str, str],
    runtime_settings: Any,
    scheduler_settings: Any,
    observed_at_ms: int,
) -> tuple[RuntimeContextApplicationService, ActionPreviewService]:
    """Compose Context first, then start its fenced ResourceManager writer."""

    activity_monitor = host_lifecycle.start_activity_monitor(ledger=ledger)
    startup_probe_statuses["activity_monitor"] = (
        f"OK (gaming_check={activity_monitor._gaming_interval}s, "
        f"idle_check={activity_monitor._idle_interval}s, "
        "hard_state_journal=bound)"
    )
    application = compose_host_runtime_context(
        task_runtime=task_runtime,
        ledger=ledger,
        embodiment_runtime=embodiment_runtime,
        environment_observation_provider=runtime_environment_observation_provider(
            host_lifecycle.get_mode_transition_service()
        ),
    )
    preview = compose_action_preview_service(
        task_runtime=task_runtime,
        ledger=ledger,
        runtime_context_application=application,
    )
    if not bool(scheduler_settings.background_tasks_enabled):
        startup_probe_statuses["system_resource_manager"] = (
            "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
        )
    elif not bool(runtime_settings.resource_manager_enabled):
        startup_probe_statuses["system_resource_manager"] = (
            "SKIPPED: HOST_RESOURCE_MANAGER_ENABLED=false"
        )
    else:
        manager = host_lifecycle.start_resource_manager(
            status_journal=compose_resource_manager_status_journal(
                task_runtime=task_runtime,
                embodiment_runtime=embodiment_runtime,
            ),
            degradation_state_provider=(
                application.composition.guardian.read_degradation_state
            ),
            run_immediately=bool(
                runtime_settings.resource_manager_run_on_startup
            ),
        )
        status = manager.get_status()
        durability = dict(status.get("durability") or {})
        startup_probe_statuses["system_resource_manager"] = (
            "OK "
            f"(interval={status.get('interval_s')}s, "
            f"revision={durability.get('revision')}, "
            f"durability={durability.get('status')})"
        )
    observe_runtime_startup(
        runtime_context_application=application,
        startup_probe_statuses=startup_probe_statuses,
        observed_at_ms=observed_at_ms,
    )
    return application, preview


def runtime_environment_observation_provider(
    mode_transition_service: Any,
) -> Callable[..., Mapping[str, Any]]:
    """Freeze one app-local environment reader for the Runtime Context graph."""

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


__all__ = [
    "ServiceCatalogGuardianObservation",
    "bind_host_hard_state_runtime",
    "compose_action_preview_service",
    "compose_host_runtime_context",
    "compose_resource_manager_status_journal",
    "compose_service_catalog_guardian_observation",
    "observe_runtime_startup",
    "runtime_environment_observation_provider",
    "start_host_runtime_context_services",
]
