"""Explicit app-local composition for World/Scene/Intent/Guardian."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context_authority_schema import (
    RuntimeContextSchemaError,
    assert_runtime_context_authority_schema,
)
from .intent_arbiter import IntentArbiter
from .intent_authority import IntentAuthorityPort, IntentPolicyIdentityV1
from .runtime_guardian import RuntimeGuardian
from .scene_resolver import SceneResolver
from .world_resolver import WorldResolver


class RuntimeContextCompositionError(RuntimeError):
    """The context graph cannot be built with a durable authority."""


@dataclass(frozen=True, slots=True)
class RuntimeContextComposition:
    """The single object graph shared by Host context consumers."""

    world_resolver: WorldResolver
    scene_resolver: SceneResolver
    intent_arbiter: IntentArbiter
    guardian: RuntimeGuardian
    durable: bool = True


def build_runtime_context_composition(
    *,
    event_store: Any,
    observation_port: Any,
    runtime_scope_id: str,
    runtime_authority_provider: Any,
    intent_authority_port: IntentAuthorityPort,
    intent_policy_identity: IntentPolicyIdentityV1,
) -> RuntimeContextComposition:
    """Build the NE-03 graph; reject a process-local-only production graph."""

    if event_store is None or not callable(getattr(event_store, "append_event", None)):
        raise RuntimeContextCompositionError(
            "Runtime context requires the durable RuntimeEventStore authority"
        )
    if not callable(getattr(event_store, "list_events_for_aggregates", None)):
        raise RuntimeContextCompositionError(
            "Runtime context requires aggregate replay for restart recovery"
        )
    if not callable(getattr(observation_port, "list_observation_batches", None)):
        raise RuntimeContextCompositionError(
            "Runtime context requires the workspace ObservationPort authority"
        )
    if not callable(getattr(observation_port, "read_observation_head", None)):
        raise RuntimeContextCompositionError(
            "Runtime context requires the canonical Observation head authority"
        )
    if not callable(
        getattr(intent_authority_port, "resolve_intent_authority", None)
    ):
        raise RuntimeContextCompositionError(
            "Runtime context requires the canonical IntentAuthorityPort"
        )
    if type(intent_policy_identity) is not IntentPolicyIdentityV1:
        raise RuntimeContextCompositionError(
            "Runtime context requires a typed composition-owned Intent policy identity"
        )
    try:
        assert_runtime_context_authority_schema(event_store)
    except RuntimeContextSchemaError as exc:
        raise RuntimeContextCompositionError(
            "Runtime context CAS and lease schema is not ready"
        ) from exc
    guardian = RuntimeGuardian(
        event_store=event_store,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    return RuntimeContextComposition(
        world_resolver=WorldResolver(observation_port=observation_port),
        scene_resolver=SceneResolver(
            event_store=event_store,
            runtime_scope_id=runtime_scope_id,
            runtime_authority_provider=runtime_authority_provider,
            guardian_snapshot_provider=guardian.snapshot,
        ),
        intent_arbiter=IntentArbiter(
            authority_port=intent_authority_port,
            policy_identity=intent_policy_identity,
        ),
        guardian=guardian,
    )


__all__ = [
    "RuntimeContextComposition",
    "RuntimeContextCompositionError",
    "build_runtime_context_composition",
]
