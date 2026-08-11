"""Explicit app-local composition for World/Scene/Intent/Guardian."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .context_authority_schema import (
    RuntimeContextSchemaError,
    assert_runtime_context_authority_schema,
)
from .environment_state import build_environment_snapshot
from .intent_arbiter import IntentArbiter
from .intent_authority import IntentAuthorityPort, IntentPolicyIdentityV1
from .runtime_guardian import RuntimeGuardian
from .scene_resolver import SceneResolver
from .world_resolver import WorldResolver
from .world_observation_application import RuntimeWorldObservationApplication


class RuntimeContextCompositionError(RuntimeError):
    """The context graph cannot be built with a durable authority."""


@dataclass(frozen=True, slots=True)
class RuntimeContextComposition:
    """The single object graph shared by Host context consumers."""

    event_store: Any
    observation_port: Any
    long_term_goal_authority: Any
    world_resolver: WorldResolver
    world_observation_application: RuntimeWorldObservationApplication
    scene_resolver: SceneResolver
    intent_arbiter: IntentArbiter
    intent_authority_port: IntentAuthorityPort
    guardian: RuntimeGuardian
    durable: bool = True


def build_runtime_context_composition(
    *,
    event_store: Any,
    observation_port: Any,
    long_term_goal_authority: Any,
    runtime_scope_id: str,
    runtime_authority_provider: Any,
    intent_policy_identity: IntentPolicyIdentityV1,
    intent_authority_port: IntentAuthorityPort | None = None,
    intent_authority_port_factory: Callable[[SceneResolver], IntentAuthorityPort]
    | None = None,
    environment_observation_provider: Callable[..., Any] | None = None,
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
    if not callable(getattr(observation_port, "append_observation_batch", None)):
        raise RuntimeContextCompositionError(
            "Runtime context requires the canonical Observation write authority"
        )
    if not callable(getattr(observation_port, "current_expiry_authority_ref", None)):
        raise RuntimeContextCompositionError(
            "Runtime context requires explicit World expiry authority"
        )
    if not callable(
        getattr(long_term_goal_authority, "list_active_world_facts", None)
    ) or not callable(
        getattr(long_term_goal_authority, "active_identity", None)
    ):
        raise RuntimeContextCompositionError(
            "Runtime context requires the durable long-term-goal authority"
        )
    if intent_authority_port is not None and intent_authority_port_factory is not None:
        raise RuntimeContextCompositionError(
            "Runtime context accepts one Intent authority composition path"
        )
    if intent_authority_port is None and not callable(intent_authority_port_factory):
        raise RuntimeContextCompositionError(
            "Runtime context requires the canonical IntentAuthorityPort"
        )
    if type(intent_policy_identity) is not IntentPolicyIdentityV1:
        raise RuntimeContextCompositionError(
            "Runtime context requires a typed composition-owned Intent policy identity"
        )
    environment_provider = (
        environment_observation_provider or build_environment_snapshot
    )
    if not callable(environment_provider):
        raise RuntimeContextCompositionError(
            "Runtime context requires a composition-owned environment authority"
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
    scene_resolver = SceneResolver(
        event_store=event_store,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
        guardian_snapshot_provider=guardian.snapshot,
    )
    if intent_authority_port_factory is not None:
        try:
            intent_authority_port = intent_authority_port_factory(scene_resolver)
        except Exception as exc:
            raise RuntimeContextCompositionError(
                "Runtime context Intent authority factory failed"
            ) from exc
    if not callable(
        getattr(intent_authority_port, "resolve_intent_authority", None)
    ):
        raise RuntimeContextCompositionError(
            "Runtime context requires the canonical IntentAuthorityPort"
        )
    world_resolver = WorldResolver(observation_port=observation_port)
    return RuntimeContextComposition(
        event_store=event_store,
        observation_port=observation_port,
        long_term_goal_authority=long_term_goal_authority,
        world_resolver=world_resolver,
        world_observation_application=RuntimeWorldObservationApplication(
            event_store=event_store,
            observation_port=observation_port,
            world_resolver=world_resolver,
            environment_observation_provider=environment_provider,
            long_term_goal_authority=long_term_goal_authority,
        ),
        scene_resolver=scene_resolver,
        intent_arbiter=IntentArbiter(
            authority_port=intent_authority_port,  # type: ignore[arg-type]
            policy_identity=intent_policy_identity,
        ),
        intent_authority_port=intent_authority_port,  # type: ignore[arg-type]
        guardian=guardian,
    )


__all__ = [
    "RuntimeContextComposition",
    "RuntimeContextCompositionError",
    "build_runtime_context_composition",
]
