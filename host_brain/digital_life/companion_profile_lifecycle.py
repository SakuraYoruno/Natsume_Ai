"""Durable lifecycle for low-risk companion-expression preferences.

The service never edits Soul, relationship consent, or Natsume's self-affect.
It turns already-canonical helpful aftereffects into a five-interaction canary
and then an exact, journal-backed activation or rollback.  There is no
process-local experiment counter or mutable profile cache.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import RelationshipScope
from host_brain.digital_life.service import LifeCommandService
from natsume_contracts.life import (
    CompanionProfileCanaryInteractionV1,
    CompanionProfilePreferencesV1,
    CompanionProfileStateV1,
    CompanionProfileVersionV1,
    LifeCommand,
    LifeCommandReceipt,
    RelationalAftereffectV1,
    baseline_companion_profile,
    utc_day_from_ms,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, *, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _aftereffect_from_mapping(raw: Mapping[str, Any]) -> RelationalAftereffectV1:
    if not raw:
        raise ValueError("relational aftereffect is required")
    return RelationalAftereffectV1(
        aftereffect_id=raw.get("aftereffect_id"),
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_receipt_ref=raw.get("source_receipt_ref"),
        user_input_ref=raw.get("user_input_ref"),
        outcome=raw.get("outcome"),
        helpfulness=raw.get("helpfulness"),
        boundary_respected=raw.get("boundary_respected"),
        repair_acknowledged=raw.get("repair_acknowledged"),
        observed_at_ms=raw.get("observed_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        self_id=raw.get("self_id") or "natsume_ai",
    )


@dataclass(frozen=True, slots=True)
class CompanionProfileLifecycleResult:
    """Receipt plus the bounded profile head after one lifecycle transition."""

    scope: RelationshipScope
    receipt: LifeCommandReceipt | None
    state: CompanionProfileStateV1
    reason_code: str


class CompanionProfileLifecycleService:
    """Write profile transitions through the canonical Life command UoW."""

    def __init__(
        self,
        *,
        command_service_provider: Callable[[], LifeCommandService],
        snapshot_reader_provider: Callable[[], LifeSnapshotReader],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._command_service_provider = command_service_provider
        self._snapshot_reader_provider = snapshot_reader_provider
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def start_canary(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        preferences: CompanionProfilePreferencesV1 | Mapping[str, Any],
        evidence_aftereffect_ids: Sequence[Any],
    ) -> CompanionProfileLifecycleResult:
        """Create one evidence-qualified profile and begin its five-turn canary.

        Candidate evidence is reopened from the canonical relationship event
        chain.  Callers cannot claim an arbitrary confidence score, evidence
        day, or clean boundary record.
        """

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        self._require_established_relationship(scope)
        if current.canary_profile is not None:
            raise ValueError("COMPANION_PROFILE_CANARY_ALREADY_ACTIVE")
        preference_value = (
            preferences
            if isinstance(preferences, CompanionProfilePreferencesV1)
            else CompanionProfilePreferencesV1.from_mapping(_mapping(preferences))
        )
        evidence = self._validated_aftereffects(
            scope,
            aftereffect_ids=evidence_aftereffect_ids,
            require_after_profile_ms=None,
        )
        refs = tuple(f"relationship-aftereffect://{item.aftereffect.aftereffect_id}" for item in evidence)
        days = tuple(sorted({utc_day_from_ms(item.aftereffect.observed_at_ms) for item in evidence}))
        confidence_lower_bound = min(item.aftereffect.helpfulness for item in evidence)
        if len(evidence) < 5 or len(days) < 3 or confidence_lower_bound < 0.85:
            raise ValueError("COMPANION_PROFILE_EVIDENCE_THRESHOLD_NOT_MET")
        profile = CompanionProfileVersionV1.create(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            version=max(item.version for item in current.known_profiles) + 1,
            preferences=preference_value,
            source_aftereffect_refs=refs,
            evidence_days_utc=days,
            confidence_lower_bound=confidence_lower_bound,
            boundary_violation_count=0,
            rollback_profile_id=current.active_profile.profile_id,
            created_at_ms=max(0, int(self._clock_ms())),
        )
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-canary.v1",
                "scope": scope.aggregate_id,
                "profile_id": profile.profile_id,
            }
        )[:32]
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_canary_started",
            ordinal=0,
            payload={"profile": profile.as_dict()},
            created_at_ms=profile.created_at_ms,
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code="COMPANION_PROFILE_CANARY_STARTED",
        )

    def record_eligible_canary_aftereffect(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        aftereffect_id: Any,
    ) -> CompanionProfileLifecycleResult:
        """Record one helpful, boundary-safe, post-canary aftereffect.

        No active canary is a normal no-op.  Any attempt to use an earlier,
        failed, boundary-violating, or scope-drifted aftereffect fails closed.
        """

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        candidate = current.canary_profile
        if candidate is None:
            return CompanionProfileLifecycleResult(
                scope=scope,
                receipt=None,
                state=current,
                reason_code="COMPANION_PROFILE_NO_ACTIVE_CANARY",
            )
        evidence = self._validated_aftereffects(
            scope,
            aftereffect_ids=(aftereffect_id,),
            require_after_profile_ms=candidate.created_at_ms,
        )
        item = evidence[0]
        interaction = CompanionProfileCanaryInteractionV1.create(
            profile_id=candidate.profile_id,
            aftereffect_id=item.aftereffect.aftereffect_id,
            aftereffect_ref=f"relationship-aftereffect://{item.aftereffect.aftereffect_id}",
            observed_at_ms=item.aftereffect.observed_at_ms,
        )
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-canary-observation.v1",
                "scope": scope.aggregate_id,
                "interaction_id": interaction.interaction_id,
            }
        )[:32]
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_canary_interaction_recorded",
            ordinal=0,
            payload={
                "profile": candidate.as_dict(),
                "canary_interaction": interaction.as_dict(),
            },
            # The relationship aftereffect has already committed at the same
            # logical clock tick.  Advance the lifecycle event by one logical
            # millisecond when necessary so cursor pagination preserves the
            # causal order without changing the observed user timestamp.
            created_at_ms=max(
                max(0, int(self._clock_ms())),
                interaction.observed_at_ms + 1,
            ),
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code="COMPANION_PROFILE_CANARY_INTERACTION_RECORDED",
        )

    def activate_canary(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
    ) -> CompanionProfileLifecycleResult:
        """Promote exactly a completed five-interaction canary."""

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        candidate = current.canary_profile
        if candidate is None:
            raise ValueError("COMPANION_PROFILE_CANARY_NOT_ACTIVE")
        if len(current.canary_accepted_interaction_ids) != 5:
            raise ValueError("COMPANION_PROFILE_CANARY_NOT_COMPLETE")
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-activate.v1",
                "scope": scope.aggregate_id,
                "profile_id": candidate.profile_id,
            }
        )[:32]
        now_ms = self._next_created_at(scope)
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_activated",
            ordinal=0,
            payload={"profile": candidate.as_dict()},
            created_at_ms=now_ms,
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code="COMPANION_PROFILE_ACTIVATED",
        )

    def rollback_active(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
    ) -> CompanionProfileLifecycleResult:
        """Restore the exact version referenced by the current active profile."""

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        target_id = current.active_profile.rollback_profile_id
        if not target_id:
            raise ValueError("COMPANION_PROFILE_ROLLBACK_NOT_AVAILABLE")
        known = {profile.profile_id: profile for profile in current.known_profiles}
        target = known.get(target_id)
        if target is None:
            raise ValueError("COMPANION_PROFILE_ROLLBACK_TARGET_MISSING")
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-rollback.v1",
                "scope": scope.aggregate_id,
                "active_profile_id": current.active_profile.profile_id,
                "target_profile_id": target.profile_id,
            }
        )[:32]
        now_ms = self._next_created_at(scope)
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_rolled_back",
            ordinal=0,
            payload={
                "profile": target.as_dict(),
                "rollback_profile_id": current.active_profile.profile_id,
            },
            created_at_ms=now_ms,
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code="COMPANION_PROFILE_ROLLED_BACK",
        )

    def _scope(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
    ) -> RelationshipScope:
        return RelationshipScope.from_values(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )

    def _read_state(self, scope: RelationshipScope) -> CompanionProfileStateV1:
        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        raw_relationship = _mapping(snapshot.state.get("relationship_v2"))
        if raw_relationship and (
            _text(raw_relationship.get("workspace_id")) not in {
                "",
                "workspace::unbound",
                scope.workspace_id,
            }
            or _text(raw_relationship.get("relationship_subject_id"))
            not in {"", scope.relationship_subject_id}
        ):
            raise RuntimeError("COMPANION_PROFILE_RELATIONSHIP_SCOPE_DRIFT")
        raw = snapshot.state.get("companion_profile")
        if isinstance(raw, Mapping):
            return CompanionProfileStateV1.from_mapping(raw)
        baseline = baseline_companion_profile(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
        )
        return CompanionProfileStateV1(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            revision=0,
            active_profile=baseline,
            known_profiles=(baseline,),
        )

    def _require_established_relationship(self, scope: RelationshipScope) -> None:
        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        relationship = _mapping(snapshot.state.get("relationship_v2"))
        if (
            _text(relationship.get("workspace_id")) != scope.workspace_id
            or _text(relationship.get("relationship_subject_id"))
            != scope.relationship_subject_id
        ):
            raise ValueError("COMPANION_PROFILE_RELATIONSHIP_SCOPE_NOT_ESTABLISHED")

    def _next_created_at(self, scope: RelationshipScope) -> int:
        """Keep lifecycle cursor time strictly after its durable predecessor."""

        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        return max(
            max(0, int(self._clock_ms())),
            max(0, int(snapshot.updated_at_ms)) + 1,
        )

    def _validated_aftereffects(
        self,
        scope: RelationshipScope,
        *,
        aftereffect_ids: Sequence[Any],
        require_after_profile_ms: int | None,
    ) -> tuple["_AftereffectEvent", ...]:
        if isinstance(aftereffect_ids, (str, bytes, bytearray)) or not isinstance(
            aftereffect_ids, Sequence
        ):
            raise ValueError("evidence_aftereffect_ids must be a sequence")
        requested = tuple(_text(item) for item in aftereffect_ids)
        if not requested or any(not item for item in requested):
            raise ValueError("evidence_aftereffect_ids are required")
        if len(set(requested)) != len(requested):
            raise ValueError("evidence_aftereffect_ids must not contain duplicates")
        by_id: dict[str, _AftereffectEvent] = {}
        for event in self._require_snapshot_reader().list_events(
            scope.aggregate_id,
            after_sequence=0,
            limit=1_000,
        ):
            if event.event_type != "relational_aftereffect_recorded":
                continue
            payload = _mapping(event.payload)
            aftereffect = _aftereffect_from_mapping(
                _mapping(_mapping(payload.get("command_payload")).get("aftereffect"))
            )
            if (
                aftereffect.workspace_id != scope.workspace_id
                or aftereffect.relationship_subject_id != scope.relationship_subject_id
            ):
                raise RuntimeError("COMPANION_PROFILE_AFTEREFFECT_SCOPE_DRIFT")
            if aftereffect.aftereffect_id in requested:
                by_id[aftereffect.aftereffect_id] = _AftereffectEvent(
                    aftereffect=aftereffect,
                    event_sequence=event.sequence,
                    event_created_at_ms=event.created_at_ms,
                )
        missing = [item for item in requested if item not in by_id]
        if missing:
            raise ValueError("COMPANION_PROFILE_AFTEREFFECT_NOT_CANONICAL")
        selected = tuple(by_id[item] for item in requested)
        if any(
            item.aftereffect.outcome != "HELPFUL"
            or not item.aftereffect.boundary_respected
            or item.aftereffect.helpfulness < 0.85
            for item in selected
        ):
            raise ValueError("COMPANION_PROFILE_AFTEREFFECT_NOT_ELIGIBLE")
        user_refs = tuple(item.aftereffect.user_input_ref for item in selected)
        if len(set(user_refs)) != len(user_refs):
            raise ValueError("COMPANION_PROFILE_EVIDENCE_NOT_INDEPENDENT")
        if require_after_profile_ms is not None and any(
            item.aftereffect.observed_at_ms < require_after_profile_ms
            for item in selected
        ):
            raise ValueError("COMPANION_PROFILE_AFTEREFFECT_PRECEDES_CANARY")
        return selected

    def _require_command_service(self) -> LifeCommandService:
        service = self._command_service_provider()
        if not isinstance(service, LifeCommandService):
            raise RuntimeError("COMPANION_PROFILE_LIFE_COMMAND_SERVICE_NOT_INJECTED")
        return service

    def _require_snapshot_reader(self) -> LifeSnapshotReader:
        reader = self._snapshot_reader_provider()
        if not isinstance(reader, LifeSnapshotReader):
            raise RuntimeError("COMPANION_PROFILE_SNAPSHOT_READER_NOT_INJECTED")
        return reader

    def _submit(
        self,
        *,
        scope: RelationshipScope,
        request_id: str,
        command_type: str,
        ordinal: int,
        payload: Mapping[str, Any],
        created_at_ms: int,
    ) -> LifeCommandReceipt:
        command_seed = {
            "schema": "companion-profile-life-command.v1",
            "aggregate_id": scope.aggregate_id,
            "request_id": request_id,
            "command_type": command_type,
            "ordinal": ordinal,
            "payload": dict(payload),
        }
        command_id = f"companion_profile_{_digest(command_seed)[:40]}"

        def build(snapshot: Any) -> LifeCommand:
            return LifeCommand(
                command_id=command_id,
                person_id=scope.aggregate_id,
                command_type=command_type,
                payload=dict(payload),
                expected_version=int(snapshot.version),
                created_at_ms=max(0, int(created_at_ms)),
                correlation_id=request_id,
                metadata={
                    "source": "companion_profile_lifecycle",
                    "workspace_id": scope.workspace_id,
                    "relationship_subject_id": scope.relationship_subject_id,
                    "session_id": scope.session_id,
                },
            )

        return self._require_command_service().submit_next(scope.aggregate_id, build)


@dataclass(frozen=True, slots=True)
class _AftereffectEvent:
    aftereffect: RelationalAftereffectV1
    event_sequence: int
    event_created_at_ms: int


__all__ = [
    "CompanionProfileLifecycleResult",
    "CompanionProfileLifecycleService",
]
