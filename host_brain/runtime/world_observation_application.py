"""Production writer for canonical, workspace-scoped World observations.

The request body is intentionally absent from this boundary.  The caller may
provide text, a desired Scene, or multimodal hints, but only server-owned
principal/relationship state and the composition-owned environment provider
can become World facts.  Every transition first becomes an independently
verifiable RuntimeEvent, then an append-only World batch, and is finally
reopened through WorldResolver before any Scene input is projected.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
from threading import RLock
from typing import Any

from host_brain.integrations.runtime_events import RuntimeEvent

from .context_contracts import (
    WorldFactExpiryV1,
    WorldFactV2,
    WorldSnapshotV2,
)
from .world_resolver import WorldResolver
from .world_source_verifier import (
    WorldObservationAuthorityProjectionV1,
    world_fact_semantic_digest,
    world_observation_source_identity_digest,
    world_observation_source_payload,
)


_MINUTE_MS = 60_000
_IDENTITY_TTL_MS = 24 * 60 * _MINUTE_MS
_RELATIONSHIP_TTL_MS = 30 * _MINUTE_MS
_USER_STATE_TTL_MS = 10 * _MINUTE_MS
_ENVIRONMENT_TTL_MS = 5 * _MINUTE_MS

_RELATIONSHIP_STATES = frozenset(
    {"TRUSTED", "NORMAL", "RESTRICTED", "UNTRUSTED"}
)
_USER_STATES = frozenset(
    {
        "NEUTRAL",
        "CALM",
        "HAPPY",
        "EXCITED",
        "SAD",
        "ANGRY",
        "FRUSTRATED",
        "ANXIOUS",
        "TIRED",
        "UPSET",
        "THINKING",
        "SURPRISED",
        "LOVING",
        "EMBARRASSED",
        "CONFUSED",
        "BORED",
        "PROUD",
        "GRATEFUL",
    }
)
_ACTIVITY_STATES = frozenset(
    {
        "NORMAL",
        "IDLE",
        "WORKING",
        "FOCUS",
        "MEETING",
        "GAME",
        "GAMING",
        "GAME_LOW_INTERRUPTION",
        "MAINTENANCE",
        "SLEEP",
    }
)
_DAY_PHASES = frozenset(
    {
        "DAWN",
        "MORNING",
        "LATE_MORNING",
        "NOON",
        "AFTERNOON",
        "EVENING",
        "NIGHT",
        "LATE_NIGHT",
    }
)


class RuntimeWorldObservationError(RuntimeError):
    """The canonical World transition could not be persisted and reopened."""


@dataclass(frozen=True, slots=True)
class RuntimeWorldObservationOutcomeV1:
    snapshot: WorldSnapshotV2
    relationship_state: str
    user_state: str
    activity_state: str
    local_hour: int


def _enum(value: object, allowed: frozenset[str], default: str) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if candidate in allowed else default


def _scope_digest(workspace_id: str, subject_id: str) -> str:
    return hashlib.sha256(
        f"{workspace_id}\x00{subject_id}".encode("utf-8")
    ).hexdigest()


def _principal_digest(principal_id: str) -> str:
    return "sha256:" + hashlib.sha256(principal_id.encode("utf-8")).hexdigest()


def _latest_values(snapshot: WorldSnapshotV2, key: str) -> tuple[str, ...]:
    candidates = tuple(item for item in snapshot.facts if item.key == key)
    if not candidates:
        return ()
    latest_ms = max(item.observed_at_ms for item in candidates)
    return tuple(
        sorted(
            {
                str(item.value).strip().upper()
                for item in candidates
                if item.observed_at_ms == latest_ms
            }
        )
    )


def _project_enum(
    snapshot: WorldSnapshotV2,
    *,
    key: str,
    allowed: frozenset[str],
    default: str,
    conflict_priority: Sequence[str],
) -> str:
    values = tuple(item for item in _latest_values(snapshot, key) if item in allowed)
    if not values:
        return default
    if len(values) == 1:
        return values[0]
    # Competing refs remain present in WorldSnapshot.  Scene projection does
    # not silently pick the highest confidence: it follows a frozen,
    # conservative priority for same-time conflicts.
    for candidate in conflict_priority:
        if candidate in values:
            return candidate
    return default


def _fact_semantics(fact: WorldFactV2) -> dict[str, Any]:
    """Fields whose equality means an observation adds no new knowledge."""

    return {
        "workspace_id": fact.workspace_id,
        "subject_id": fact.subject_id,
        "key": fact.key,
        "value": fact.value,
        "confidence": fact.confidence,
        "conflict_group": fact.conflict_group,
        "revision": fact.revision,
    }


def _has_active_semantic_match(
    snapshot: WorldSnapshotV2,
    candidate: WorldFactV2,
) -> bool:
    expected = _fact_semantics(candidate)
    same_key = tuple(item for item in snapshot.facts if item.key == candidate.key)
    if not same_key:
        return False
    latest_ms = max(item.observed_at_ms for item in same_key)
    return any(
        item.observed_at_ms == latest_ms and _fact_semantics(item) == expected
        for item in same_key
    )


def _project_local_hour(snapshot: WorldSnapshotV2) -> int:
    candidates = tuple(
        item for item in snapshot.facts if item.key == "environment.local_hour"
    )
    if not candidates:
        raise RuntimeWorldObservationError(
            "verified World has no environment local-hour fact"
        )
    latest_ms = max(item.observed_at_ms for item in candidates)
    try:
        values = {
            int(item.value)
            for item in candidates
            if item.observed_at_ms == latest_ms
        }
    except (TypeError, ValueError) as exc:
        raise RuntimeWorldObservationError(
            "verified World local-hour fact is invalid"
        ) from exc
    if len(values) != 1:
        raise RuntimeWorldObservationError(
            "verified World local-hour facts conflict at the latest revision"
        )
    value = values.pop()
    if not 0 <= value <= 23:
        raise RuntimeWorldObservationError(
            "verified World local-hour fact is invalid"
        )
    return value


class RuntimeWorldObservationApplication:
    """Append canonical observations and return only a verified projection."""

    def __init__(
        self,
        *,
        event_store: Any,
        observation_port: Any,
        world_resolver: WorldResolver,
        environment_observation_provider: Callable[..., Mapping[str, Any]],
    ) -> None:
        if not callable(getattr(event_store, "append_event", None)) or not callable(
            getattr(event_store, "get_event_by_id", None)
        ):
            raise RuntimeWorldObservationError(
                "durable RuntimeEvent append/read authority is required"
            )
        required_port_methods = (
            "append_observation_batch",
            "current_expiry_authority_ref",
            "read_observation_head",
            "list_observation_batches",
        )
        if any(
            not callable(getattr(observation_port, name, None))
            for name in required_port_methods
        ):
            raise RuntimeWorldObservationError(
                "durable World observation read/write authority is required"
            )
        if not isinstance(world_resolver, WorldResolver):
            raise RuntimeWorldObservationError("typed WorldResolver is required")
        if not callable(environment_observation_provider):
            raise RuntimeWorldObservationError(
                "composition-owned environment observation provider is required"
            )
        self._events = event_store
        self._journal = observation_port
        self._resolver = world_resolver
        self._environment = environment_observation_provider
        self._write_lock = RLock()

    def observe_and_resolve(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        principal_id: str,
        relationship_state: str,
        user_state: str,
        observed_at_ms: int,
        authority_projections: Sequence[
            WorldObservationAuthorityProjectionV1
        ] = (),
    ) -> RuntimeWorldObservationOutcomeV1:
        workspace = str(workspace_id or "").strip()
        subject = str(subject_id or "").strip()
        principal = str(principal_id or "").strip()
        if not workspace or not subject or not principal:
            raise RuntimeWorldObservationError(
                "workspace, subject, and server principal are required"
            )
        if (
            isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms < 0
        ):
            raise RuntimeWorldObservationError("observed_at_ms must be non-negative")
        relationship = _enum(
            relationship_state,
            _RELATIONSHIP_STATES,
            "NORMAL",
        )
        user = _enum(user_state, _USER_STATES, "NEUTRAL")
        environment = self._read_environment(observed_at_ms)
        projections = tuple(authority_projections)
        environment_projection = environment.pop("authority_projection", None)
        if environment_projection is not None:
            projections += (environment_projection,)
        if any(
            not isinstance(item, WorldObservationAuthorityProjectionV1)
            for item in projections
        ):
            raise RuntimeWorldObservationError(
                "World source authority projections must be typed"
            )
        projection_kinds = tuple(item.authority_kind for item in projections)
        if len(set(projection_kinds)) != len(projection_kinds):
            raise RuntimeWorldObservationError(
                "World source authority projections are ambiguous"
            )

        with self._write_lock:
            due = self._resolver.due_expiry_facts(
                workspace_id=workspace,
                subject_id=subject,
                now_ms=observed_at_ms,
            )
            if due:
                authority_ref = self._journal.current_expiry_authority_ref()
                expiries = tuple(
                    WorldFactExpiryV1(
                        fact_digest=item.fact_digest,
                        authority_ref=authority_ref,
                        effective_at_ms=item.expires_at_ms,
                    )
                    for item in due
                )
                self._append_transition(
                    workspace_id=workspace,
                    subject_id=subject,
                    observed_at_ms=observed_at_ms,
                    expiries=expiries,
                )

            active_snapshot = self._resolver.resolve(
                workspace_id=workspace,
                subject_id=subject,
                now_ms=observed_at_ms,
            )
            desired_facts = self._build_facts(
                workspace_id=workspace,
                subject_id=subject,
                principal_id=principal,
                relationship_state=relationship,
                user_state=user,
                environment=environment,
                observed_at_ms=observed_at_ms,
                source_ref="world-observation://pending/source",
            )
            projected_fact_digests = {
                digest
                for projection in projections
                for digest in projection.fact_semantic_digests
            }
            desired_fact_digests = {
                world_fact_semantic_digest(item) for item in desired_facts
            }
            if not projected_fact_digests.issubset(desired_fact_digests):
                raise RuntimeWorldObservationError(
                    "World source projection does not bind this observation"
                )
            facts = tuple(
                item
                for item in desired_facts
                if (
                    world_fact_semantic_digest(item)
                    in projected_fact_digests
                    or not _has_active_semantic_match(active_snapshot, item)
                )
            )
            if facts:
                source_identity = world_observation_source_identity_digest(
                    workspace_id=workspace,
                    subject_id=subject,
                    observed_at_ms=observed_at_ms,
                    upserts=facts,
                    authority_projections=projections,
                )
                draft = self._event(
                    workspace_id=workspace,
                    subject_id=subject,
                    observed_at_ms=observed_at_ms,
                    source_identity=source_identity,
                    upserts=facts,
                    authority_projections=projections,
                )
                exact_facts = tuple(
                    replace(item, source_ref=str(draft.event_id)) for item in facts
                )
                # source_ref is excluded from the pre-event semantic identity,
                # so binding the RuntimeEvent id must preserve source identity.
                if world_observation_source_identity_digest(
                    workspace_id=workspace,
                    subject_id=subject,
                    observed_at_ms=observed_at_ms,
                    upserts=exact_facts,
                    authority_projections=projections,
                ) != source_identity:
                    raise RuntimeWorldObservationError(
                        "World source identity changed after event binding"
                    )
                self._append_transition(
                    workspace_id=workspace,
                    subject_id=subject,
                    observed_at_ms=observed_at_ms,
                    upserts=exact_facts,
                    source_identity=source_identity,
                    event_identity=draft,
                    authority_projections=projections,
                )
                snapshot = self._resolver.resolve(
                    workspace_id=workspace,
                    subject_id=subject,
                    now_ms=observed_at_ms,
                )
            else:
                # A read never renews TTL and identical active semantics never
                # create a World or Scene revision.
                snapshot = active_snapshot

        relationship_projection = _project_enum(
            snapshot,
            key="relationship.state",
            allowed=_RELATIONSHIP_STATES,
            default="NORMAL",
            conflict_priority=("UNTRUSTED", "RESTRICTED", "NORMAL", "TRUSTED"),
        )
        user_projection = _project_enum(
            snapshot,
            key="user.affect_state",
            allowed=_USER_STATES,
            default="NEUTRAL",
            conflict_priority=(
                "ANGRY",
                "FRUSTRATED",
                "UPSET",
                "ANXIOUS",
                "TIRED",
                "SAD",
                "NEUTRAL",
                "CALM",
                "HAPPY",
            ),
        )
        activity_projection = _project_enum(
            snapshot,
            key="environment.activity_state",
            allowed=_ACTIVITY_STATES,
            default="NORMAL",
            conflict_priority=(
                "GAME_LOW_INTERRUPTION",
                "GAMING",
                "GAME",
                "MAINTENANCE",
                "MEETING",
                "FOCUS",
                "WORKING",
                "IDLE",
                "SLEEP",
                "NORMAL",
            ),
        )
        return RuntimeWorldObservationOutcomeV1(
            snapshot=snapshot,
            relationship_state=relationship_projection,
            user_state=user_projection,
            activity_state=activity_projection,
            local_hour=_project_local_hour(snapshot),
        )

    def _read_environment(self, observed_at_ms: int) -> dict[str, Any]:
        try:
            raw = self._environment(now_ms=observed_at_ms)
        except Exception as exc:
            raise RuntimeWorldObservationError(
                "environment observation authority is unavailable"
            ) from exc
        if not isinstance(raw, Mapping):
            raise RuntimeWorldObservationError(
                "environment observation authority returned an invalid value"
            )
        payload = dict(raw)
        raw_projection = payload.get("world_authority_projection_v1")
        claims_durable_source = any(
            str(payload.get(field) or "").strip()
            for field in (
                "source_ref",
                "source_digest",
                "event_ref",
                "payload_digest",
            )
        )
        authority_projection = None
        if raw_projection is None and claims_durable_source:
            raise RuntimeWorldObservationError(
                "environment source authority lacks a typed projection"
            )
        if raw_projection is not None:
            if not isinstance(raw_projection, Mapping):
                raise RuntimeWorldObservationError(
                    "environment source authority projection is invalid"
                )
            try:
                authority_projection = (
                    WorldObservationAuthorityProjectionV1.from_dict(
                        raw_projection
                    )
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeWorldObservationError(
                    "environment source authority projection is invalid"
                ) from exc
            if authority_projection.authority_kind != "ENVIRONMENT":
                raise RuntimeWorldObservationError(
                    "environment source authority kind drifted"
                )
        if "now_ms" in payload:
            try:
                environment_now_ms = int(payload["now_ms"])
            except (TypeError, ValueError) as exc:
                raise RuntimeWorldObservationError(
                    "environment observation timestamp is invalid"
                ) from exc
            if environment_now_ms != observed_at_ms:
                raise RuntimeWorldObservationError(
                    "environment observation timestamp drifted"
                )
        try:
            local_hour = int(payload.get("hour", payload.get("local_hour")))
        except (TypeError, ValueError) as exc:
            raise RuntimeWorldObservationError(
                "environment observation local hour is invalid"
            ) from exc
        if not 0 <= local_hour <= 23:
            raise RuntimeWorldObservationError(
                "environment observation local hour is invalid"
            )
        activity_raw = (
            payload.get("activity_state")
            or payload.get("activity")
            or payload.get("system_mode")
        )
        activity = _enum(activity_raw, _ACTIVITY_STATES, "")
        day_phase = _enum(payload.get("day_phase"), _DAY_PHASES, "")
        if not activity or not day_phase:
            raise RuntimeWorldObservationError(
                "environment observation state is unsupported"
            )
        return {
            "activity_state": activity,
            "day_phase": day_phase,
            "local_hour": local_hour,
            "authority_projection": authority_projection,
        }

    @staticmethod
    def _build_facts(
        *,
        workspace_id: str,
        subject_id: str,
        principal_id: str,
        relationship_state: str,
        user_state: str,
        environment: Mapping[str, Any],
        observed_at_ms: int,
        source_ref: str,
    ) -> tuple[WorldFactV2, ...]:
        scope_kind = "TENANT" if workspace_id.startswith("tenant:") else "PERSONAL"
        fact_specs = (
            (
                "authority.principal_digest",
                _principal_digest(principal_id),
                _IDENTITY_TTL_MS,
                "identity",
            ),
            ("workspace.scope_kind", scope_kind, _IDENTITY_TTL_MS, "workspace"),
            (
                "relationship.state",
                relationship_state,
                _RELATIONSHIP_TTL_MS,
                "relationship",
            ),
            ("user.affect_state", user_state, _USER_STATE_TTL_MS, "user-state"),
            (
                "environment.activity_state",
                environment["activity_state"],
                _ENVIRONMENT_TTL_MS,
                "environment-activity",
            ),
            (
                "environment.day_phase",
                environment["day_phase"],
                _ENVIRONMENT_TTL_MS,
                "environment-time",
            ),
            (
                "environment.local_hour",
                int(environment["local_hour"]),
                _ENVIRONMENT_TTL_MS,
                "environment-time",
            ),
        )
        return tuple(
            WorldFactV2(
                workspace_id=workspace_id,
                subject_id=subject_id,
                key=key,
                value=value,
                source_ref=source_ref,
                confidence=1.0,
                observed_at_ms=observed_at_ms,
                expires_at_ms=observed_at_ms + ttl_ms,
                conflict_group=conflict_group,
                revision=1,
            )
            for key, value, ttl_ms, conflict_group in fact_specs
        )

    def _event(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        observed_at_ms: int,
        source_identity: str,
        upserts: Sequence[WorldFactV2] = (),
        expiries: Sequence[WorldFactExpiryV1] = (),
        authority_projections: Sequence[
            WorldObservationAuthorityProjectionV1
        ] = (),
        event_identity: RuntimeEvent | None = None,
    ) -> RuntimeEvent:
        payload = world_observation_source_payload(
            workspace_id=workspace_id,
            subject_id=subject_id,
            observed_at_ms=observed_at_ms,
            upserts=tuple(upserts),
            expiries=tuple(expiries),
            authority_projections=tuple(authority_projections),
        )
        return RuntimeEvent.create(
            event_type="WORLD_OBSERVATION_CAPTURED",
            source_kind="SYSTEM",
            source_ref=f"world-observation://source/{source_identity}",
            aggregate_type="WORLD_OBSERVATION_SOURCE",
            aggregate_id="world-scope-" + _scope_digest(workspace_id, subject_id),
            aggregate_version=observed_at_ms,
            payload_ref=(
                "world-observation://transition/" + payload["transition_digest"]
            ),
            payload=payload,
            evidence_refs=tuple(
                sorted(item.event_ref for item in authority_projections)
            ),
            dedupe_key=(event_identity.dedupe_key if event_identity else None),
            event_id=(event_identity.event_id if event_identity else None),
            redaction_level="INTERNAL_REDACTED",
            ts_ms=observed_at_ms,
        )

    def _append_transition(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        observed_at_ms: int,
        upserts: Sequence[WorldFactV2] = (),
        expiries: Sequence[WorldFactExpiryV1] = (),
        source_identity: str | None = None,
        event_identity: RuntimeEvent | None = None,
        authority_projections: Sequence[
            WorldObservationAuthorityProjectionV1
        ] = (),
    ) -> None:
        identity = source_identity or world_observation_source_identity_digest(
            workspace_id=workspace_id,
            subject_id=subject_id,
            observed_at_ms=observed_at_ms,
            upserts=tuple(upserts),
            expiries=tuple(expiries),
            authority_projections=tuple(authority_projections),
        )
        event = self._event(
            workspace_id=workspace_id,
            subject_id=subject_id,
            observed_at_ms=observed_at_ms,
            source_identity=identity,
            upserts=tuple(upserts),
            expiries=tuple(expiries),
            authority_projections=tuple(authority_projections),
            event_identity=event_identity,
        )
        try:
            record = self._events.append_event(event)
            durable_event_id = str(record.get("event_id") or "")
            durable_digest = str(record.get("payload_digest") or "")
            if durable_event_id != event.event_id or not durable_digest.startswith(
                "sha256:"
            ):
                raise RuntimeWorldObservationError(
                    "RuntimeEvent append returned a different World source"
                )
            self._journal.append_observation_batch(
                workspace_id=workspace_id,
                subject_id=subject_id,
                source_event_ref=durable_event_id,
                source_event_digest=durable_digest.removeprefix("sha256:"),
                observed_at_ms=observed_at_ms,
                upserts=tuple(upserts),
                expiries=tuple(expiries),
            )
        except RuntimeWorldObservationError:
            raise
        except Exception as exc:
            raise RuntimeWorldObservationError(
                "canonical World observation append failed"
            ) from exc


__all__ = [
    "RuntimeWorldObservationApplication",
    "RuntimeWorldObservationError",
    "RuntimeWorldObservationOutcomeV1",
]
