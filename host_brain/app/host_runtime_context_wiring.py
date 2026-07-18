"""Focused app-local World, Scene, Intent, and Guardian composition wiring."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from host_brain.composition.task_runtime import ProcessTaskRuntimeContainer
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
)
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.runtime.intent_authority import IntentPolicyIdentityV1
from host_brain.runtime.production_composition import build_runtime_context_composition
from host_brain.runtime.production_intent_authority import (
    ProductionIntentAuthorityPort,
)
from host_brain.runtime.world_source_verifier import RuntimeEventWorldSourceVerifier


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
    "compose_host_runtime_context",
    "runtime_environment_observation_provider",
]
