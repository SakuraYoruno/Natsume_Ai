"""App-local RuntimeGuardian for NE-03.

The existing degradation reducer remains the low-level fail-closed policy
engine.  This facade gives Host composition one typed owner for health,
maintenance/sleep, SLO breaches and acknowledgements, while writing the
guardian-specific journal to the same durable Runtime Event store.  It has
no module-global singleton and never broadens a capability.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.observability.degradation_state import (
    DegradationStateMachine,
    DegradationTransition,
)


class RuntimeGuardianError(RuntimeError):
    """Guardian input, journal or replay is not safe to accept."""


class _GuardianStateReconciledError(RuntimeGuardianError):
    """A competing durable head replaced the attempted in-memory mutation."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise RuntimeGuardianError(f"{field} is required")
    return result


def _refs(values: Sequence[str]) -> tuple[str, ...]:
    result = tuple(sorted({str(item or "").strip() for item in values if str(item or "").strip()}))
    if any("://" not in item for item in result):
        raise RuntimeGuardianError("guardian evidence must be durable references")
    return result


@dataclass(frozen=True, slots=True)
class GuardianEventV1:
    event_type: str
    revision: int
    status: str
    payload: Mapping[str, Any]
    observed_at_ms: int
    event_digest: str = ""

    def __post_init__(self) -> None:
        event_type = _text(self.event_type, "event_type")
        status = _text(self.status, "status")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise RuntimeGuardianError("guardian revision must be positive")
        if isinstance(self.observed_at_ms, bool) or not isinstance(self.observed_at_ms, int) or self.observed_at_ms < 0:
            raise RuntimeGuardianError("observed_at_ms must be non-negative")
        if not isinstance(self.payload, Mapping):
            raise RuntimeGuardianError("guardian payload must be a mapping")
        payload = json.loads(json.dumps(dict(self.payload), ensure_ascii=False, sort_keys=True, allow_nan=False))
        computed = _digest(
            {
                "event_type": event_type,
                "revision": self.revision,
                "status": status,
                "payload": payload,
                "observed_at_ms": self.observed_at_ms,
            }
        )
        supplied = str(self.event_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise RuntimeGuardianError("guardian event digest drift")
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "event_digest", computed)

    @property
    def event_id(self) -> str:
        return f"guardian-event://sha256/{self.event_digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.runtime.guardian.event.v1",
            "event_type": self.event_type,
            "revision": self.revision,
            "status": self.status,
            "payload": dict(self.payload),
            "observed_at_ms": self.observed_at_ms,
            "event_digest": self.event_digest,
            "event_id": self.event_id,
        }


@dataclass(frozen=True, slots=True)
class GuardianSnapshotV1:
    runtime_scope_id: str
    runtime_owner_id: str
    runtime_lease_id: str
    fencing_token: int
    revision: int
    status: str
    mode: str
    active_dependencies: tuple[str, ...]
    recovery_pending_dependencies: tuple[str, ...]
    maintenance_ref: str | None
    sleep_ref: str | None
    slo_breaches: tuple[str, ...]
    acknowledged_events: tuple[str, ...]
    last_event_digest: str | None
    snapshot_digest: str = ""

    def __post_init__(self) -> None:
        runtime_scope_id = _text(self.runtime_scope_id, "runtime_scope_id")
        runtime_owner_id = _text(self.runtime_owner_id, "runtime_owner_id")
        runtime_lease_id = _text(self.runtime_lease_id, "runtime_lease_id")
        if (
            isinstance(self.fencing_token, bool)
            or not isinstance(self.fencing_token, int)
            or self.fencing_token < 1
        ):
            raise RuntimeGuardianError("guardian fencing_token must be positive")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise RuntimeGuardianError("guardian snapshot revision must be non-negative")
        mode = _text(self.mode, "mode")
        status = _text(self.status, "status")
        active = tuple(sorted({str(item).strip() for item in self.active_dependencies if str(item).strip()}))
        pending = tuple(sorted({str(item).strip() for item in self.recovery_pending_dependencies if str(item).strip()}))
        slo = tuple(sorted({str(item).strip() for item in self.slo_breaches if str(item).strip()}))
        acknowledged = tuple(sorted({str(item).strip() for item in self.acknowledged_events if str(item).strip()}))
        payload = {
            "runtime_scope_id": runtime_scope_id,
            "runtime_owner_id": runtime_owner_id,
            "runtime_lease_id": runtime_lease_id,
            "fencing_token": self.fencing_token,
            "revision": self.revision,
            "status": status,
            "mode": mode,
            "active_dependencies": active,
            "recovery_pending_dependencies": pending,
            "maintenance_ref": self.maintenance_ref,
            "sleep_ref": self.sleep_ref,
            "slo_breaches": slo,
            "acknowledged_events": acknowledged,
            "last_event_digest": self.last_event_digest,
        }
        computed = _digest(payload)
        supplied = str(self.snapshot_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise RuntimeGuardianError("guardian snapshot digest drift")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "runtime_scope_id", runtime_scope_id)
        object.__setattr__(self, "runtime_owner_id", runtime_owner_id)
        object.__setattr__(self, "runtime_lease_id", runtime_lease_id)
        object.__setattr__(self, "active_dependencies", active)
        object.__setattr__(self, "recovery_pending_dependencies", pending)
        object.__setattr__(self, "slo_breaches", slo)
        object.__setattr__(self, "acknowledged_events", acknowledged)
        object.__setattr__(self, "snapshot_digest", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.runtime.guardian.snapshot.v1",
            "runtime_scope_id": self.runtime_scope_id,
            "runtime_owner_id": self.runtime_owner_id,
            "runtime_lease_id": self.runtime_lease_id,
            "fencing_token": self.fencing_token,
            "revision": self.revision,
            "status": self.status,
            "mode": self.mode,
            "active_dependencies": list(self.active_dependencies),
            "recovery_pending_dependencies": list(self.recovery_pending_dependencies),
            "maintenance_ref": self.maintenance_ref,
            "sleep_ref": self.sleep_ref,
            "slo_breaches": list(self.slo_breaches),
            "acknowledged_events": list(self.acknowledged_events),
            "last_event_digest": self.last_event_digest,
            "snapshot_digest": self.snapshot_digest,
        }


class RuntimeGuardian:
    """One injected guardian object for an application process."""

    _EVENT_AGGREGATE_TYPE = "RUNTIME_GUARDIAN"
    _JOURNAL_EVENT_TYPE = "RUNTIME_GUARDIAN_STATE_ADVANCED"
    _PAGE_SIZE = 1000

    def __init__(
        self,
        *,
        event_store: Any | None = None,
        degradation_machine: DegradationStateMachine | None = None,
        runtime_scope_id: str,
        runtime_authority_provider: Any,
        clock: Any = _now_ms,
    ) -> None:
        self._event_store = event_store
        self._clock = clock
        self._lock = threading.RLock()
        self._runtime_scope_id = _text(runtime_scope_id, "runtime_scope_id")
        if not callable(runtime_authority_provider):
            raise RuntimeGuardianError(
                "runtime_authority_provider must be callable"
            )
        self._runtime_authority_provider = runtime_authority_provider
        authority = self._read_runtime_authority(require_initial=True)
        self._runtime_owner_id = authority["owner_id"]
        self._runtime_lease_id = authority["lease_id"]
        self._fencing_token = authority["fencing_token"]
        self._aggregate_id = hashlib.sha256(
            self._runtime_scope_id.encode("utf-8")
        ).hexdigest()
        if degradation_machine is not None:
            if not degradation_machine.is_pure_reducer():
                raise RuntimeGuardianError(
                    "RuntimeGuardian requires a pure degradation reducer without durable or notification ports"
                )
            self._degradation = degradation_machine
        else:
            self._degradation = DegradationStateMachine(restore_from_store=False)
        self._empty_degradation_state = self._degradation.export_reducer_state()
        self._revision = 0
        self._mode = "ACTIVE"
        self._maintenance_ref: str | None = None
        self._sleep_ref: str | None = None
        self._slo_breaches: set[str] = set()
        self._acknowledged_events: set[str] = set()
        self._history: list[GuardianEventV1] = []
        self._write_authority_status = "ACTIVE"
        self._restore_guardian_journal()

    def _read_runtime_authority(
        self,
        *,
        require_initial: bool = False,
    ) -> dict[str, Any]:
        try:
            raw = self._runtime_authority_provider()
        except Exception as exc:
            raise RuntimeGuardianError(
                "guardian runtime authority provider failed"
            ) from exc
        if not isinstance(raw, Mapping):
            raise RuntimeGuardianError("guardian runtime authority is unavailable")
        scope = str(raw.get("runtime_scope_id") or self._runtime_scope_id).strip()
        owner = _text(raw.get("owner_id"), "runtime owner_id")
        lease_id = _text(raw.get("lease_id"), "runtime lease_id")
        fence = raw.get("fencing_token")
        expires_at_ms = raw.get("expires_at_ms")
        if scope != self._runtime_scope_id:
            raise RuntimeGuardianError("guardian runtime authority scope drift")
        if (
            isinstance(fence, bool)
            or not isinstance(fence, int)
            or fence < 1
            or isinstance(expires_at_ms, bool)
            or not isinstance(expires_at_ms, int)
            or expires_at_ms <= int(self._clock())
            or raw.get("released_at_ms") is not None
        ):
            raise RuntimeGuardianError("guardian runtime authority is not active")
        authority = {
            "runtime_scope_id": scope,
            "owner_id": owner,
            "lease_id": lease_id,
            "fencing_token": fence,
            "expires_at_ms": expires_at_ms,
        }
        if not require_initial and (
            owner != self._runtime_owner_id
            or lease_id != self._runtime_lease_id
            or fence != self._fencing_token
        ):
            self._write_authority_status = "WRITE_FENCED"
            raise RuntimeGuardianError("guardian runtime authority was fenced")
        return authority

    def _require_writable(self) -> dict[str, Any]:
        if self._write_authority_status != "ACTIVE":
            raise RuntimeGuardianError(
                f"guardian is not writable: {self._write_authority_status}"
            )
        return self._read_runtime_authority()

    def _restore_guardian_journal(self) -> None:
        if self._event_store is None:
            return
        records: list[dict[str, Any]] = []
        cursor = 0
        try:
            while True:
                page = self._event_store.list_events_for_aggregates(
                    aggregate_refs=((self._EVENT_AGGREGATE_TYPE, self._aggregate_id),),
                    after_cursor=cursor,
                    limit=self._PAGE_SIZE,
                )
                if not page:
                    break
                next_cursor = int(page[-1].get("cursor") or 0)
                if next_cursor <= cursor:
                    raise RuntimeGuardianError(
                        "guardian journal cursor did not advance"
                    )
                records.extend(page)
                cursor = next_cursor
                if len(page) < self._PAGE_SIZE:
                    break
        except Exception as exc:  # pragma: no cover - store-specific failure
            if isinstance(exc, RuntimeGuardianError):
                raise
            raise RuntimeGuardianError("guardian journal unavailable") from exc
        expected = 1
        previous_cursor = 0
        previous_owner: str | None = None
        previous_lease_id: str | None = None
        previous_fence = 0
        for record in records:
            record_cursor = int(record.get("cursor") or 0)
            if record_cursor <= previous_cursor:
                raise RuntimeGuardianError("guardian journal cursor order is invalid")
            previous_cursor = record_cursor
            if (
                str(record.get("aggregate_type") or "")
                != self._EVENT_AGGREGATE_TYPE
                or str(record.get("aggregate_id") or "") != self._aggregate_id
                or str(record.get("event_type") or "")
                != self._JOURNAL_EVENT_TYPE
            ):
                raise RuntimeGuardianError("guardian journal aggregate identity mismatch")
            version = int(record.get("aggregate_version") or 0)
            if version != expected:
                raise RuntimeGuardianError("guardian journal revision is not contiguous")
            payload = dict(record.get("payload") or {})
            event = GuardianEventV1(
                event_type=str(payload.get("event_type") or record.get("event_type") or ""),
                revision=version,
                status=str(payload.get("status") or ""),
                payload=dict(payload.get("details") or {}),
                observed_at_ms=int(
                    payload["observed_at_ms"]
                    if "observed_at_ms" in payload
                    else record.get("ts_ms") or 0
                ),
                event_digest=str(payload.get("event_digest") or ""),
            )
            if event.event_id != str(record.get("event_id") or event.event_id):
                raise RuntimeGuardianError("guardian journal event identity mismatch")
            details = dict(event.payload)
            if str(details.get("runtime_scope_id") or "") != self._runtime_scope_id:
                raise RuntimeGuardianError("guardian runtime scope drift")
            durable_owner = _text(details.get("runtime_owner_id"), "runtime_owner_id")
            durable_lease_id = _text(
                details.get("runtime_lease_id"),
                "runtime_lease_id",
            )
            durable_fence = details.get("fencing_token")
            if (
                isinstance(durable_fence, bool)
                or not isinstance(durable_fence, int)
                or durable_fence < 1
                or durable_fence < previous_fence
                or (
                    durable_fence == previous_fence
                    and previous_owner is not None
                    and (
                        durable_owner != previous_owner
                        or durable_lease_id != previous_lease_id
                    )
                )
            ):
                raise RuntimeGuardianError("guardian owner/fence lineage is invalid")
            self._validate_event_delta(event)
            self._apply_event(event)
            self._history.append(event)
            self._revision = version
            previous_owner = durable_owner
            previous_lease_id = durable_lease_id
            previous_fence = durable_fence
            expected += 1
        if previous_fence > self._fencing_token or (
            previous_fence == self._fencing_token
            and previous_owner is not None
            and (
                previous_owner != self._runtime_owner_id
                or previous_lease_id != self._runtime_lease_id
            )
        ):
            raise RuntimeGuardianError(
                "guardian runtime owner does not hold the latest fencing token"
            )

    def _reload_guardian_journal(self) -> None:
        self._degradation.restore_reducer_state(self._empty_degradation_state)
        self._revision = 0
        self._mode = "ACTIVE"
        self._maintenance_ref = None
        self._sleep_ref = None
        self._slo_breaches = set()
        self._acknowledged_events = set()
        self._history = []
        self._restore_guardian_journal()

    def _status(self) -> str:
        if self._mode == "MAINTENANCE":
            return "MAINTENANCE"
        if self._mode == "SLEEP":
            return "SLEEP"
        if self._slo_breaches:
            return "SLO_BREACH"
        degradation = self._degradation.snapshot()
        if degradation.status == "DEGRADED":
            return "DEGRADED"
        if degradation.status == "RECOVERY_PENDING":
            return "RECOVERY_PENDING"
        return "READY"

    @staticmethod
    def _recovery_binding_identity(binding: Mapping[str, Any]) -> dict[str, Any]:
        """Return the immutable identity of one recovery Review saga.

        ``expected_reducer_revision`` is deliberately excluded.  It is an
        optimistic application precondition, not the identity of the Review,
        and unrelated dependency recoveries may advance it while a consumed
        PREPARED operation is waiting to be reconciled.
        """

        item = dict(binding)
        try:
            subject_version = int(item.get("subject_version"))
        except (TypeError, ValueError) as exc:
            raise RuntimeGuardianError(
                "guardian recovery binding subject_version is invalid"
            ) from exc
        if subject_version < 1:
            raise RuntimeGuardianError(
                "guardian recovery binding subject_version is invalid"
            )
        identity = {
            "dependency_id": _text(
                item.get("dependency_id"), "recovery dependency_id"
            ).upper(),
            "review_id": _text(item.get("review_id"), "recovery review_id"),
            "subject_ref": _text(
                item.get("subject_ref"), "recovery subject_ref"
            ),
            "subject_version": subject_version,
            "resume_phase": _text(
                item.get("resume_phase"), "recovery resume_phase"
            ).upper(),
            "checkpoint_id": _text(
                item.get("checkpoint_id"), "recovery checkpoint_id"
            ),
            "resume_result_ref": _text(
                item.get("resume_result_ref"), "recovery resume_result_ref"
            ),
            "evidence_refs": _refs(tuple(item.get("evidence_refs") or ())),
        }
        if identity["resume_phase"] != "PRE_EXECUTION":
            raise RuntimeGuardianError(
                "guardian recovery binding resume_phase is invalid"
            )
        return identity

    @classmethod
    def _same_recovery_binding(
        cls,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> bool:
        return _digest(cls._recovery_binding_identity(left)) == _digest(
            cls._recovery_binding_identity(right)
        )

    @staticmethod
    def _claimed_degradation_transition(
        next_degradation: Mapping[str, Any],
    ) -> dict[str, Any]:
        claimed = next_degradation.get("last_transition")
        if not isinstance(claimed, Mapping):
            raise RuntimeGuardianError(
                "guardian degradation delta lacks a typed transition"
            )
        result = dict(claimed)
        detail_digest = str(result.get("detail_digest") or "").strip().lower()
        if (
            len(detail_digest) != 71
            or not detail_digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in detail_digest[7:])
        ):
            raise RuntimeGuardianError(
                "guardian degradation transition detail digest is invalid"
            )
        return result

    @staticmethod
    def _assert_replayed_degradation_state(
        *,
        replayed: Mapping[str, Any],
        claimed_state: Mapping[str, Any],
        claimed_transition: Mapping[str, Any],
        allow_opaque_detail: bool,
    ) -> None:
        replayed_state = json.loads(
            json.dumps(
                dict(replayed),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
        replayed_transition = replayed_state.get("last_transition")
        if not isinstance(replayed_transition, dict):
            raise RuntimeGuardianError(
                "guardian replay did not produce a typed degradation transition"
            )
        if allow_opaque_detail:
            # Health observations persist only a digest of their free-form
            # detail.  The detail cannot affect reducer semantics, so copy the
            # already validated digest before comparing every persisted field.
            replayed_transition["detail_digest"] = str(
                claimed_transition["detail_digest"]
            )
        if _digest(replayed_state) != _digest(dict(claimed_state)):
            raise RuntimeGuardianError(
                "guardian degradation state is not the deterministic reducer result"
            )

    def _validate_health_reducer_delta(
        self,
        *,
        event: GuardianEventV1,
        current_degradation: Mapping[str, Any],
        next_degradation: Mapping[str, Any],
    ) -> None:
        details = dict(event.payload)
        claimed = self._claimed_degradation_transition(next_degradation)
        try:
            dependency_id = _text(
                details.get("dependency_id"), "guardian dependency_id"
            )
            dependency_status = _text(
                details.get("dependency_status"), "guardian dependency_status"
            )
            source_ref = _text(details.get("source_ref"), "guardian source_ref")
            evidence_refs = _refs(tuple(details.get("evidence_refs") or ()))
            if (
                str(claimed.get("transition_id") or "")
                != str(details.get("degradation_transition_id") or "")
                or str(claimed.get("event_type") or "")
                != str(details.get("degradation_event_type") or "")
                or str(claimed.get("dependency_id") or "") != dependency_id
                or str(claimed.get("dependency_status") or "")
                != dependency_status
                or str(claimed.get("source_ref") or "") != source_ref
                or tuple(claimed.get("evidence_refs") or ()) != evidence_refs
                or int(claimed.get("observed_at_ms") or -1)
                != event.observed_at_ms
                or bool(claimed.get("injected"))
            ):
                raise RuntimeGuardianError(
                    "guardian health transition evidence does not match its delta"
                )

            shadow = DegradationStateMachine(restore_from_store=False)
            shadow.restore_reducer_state(current_degradation)
            transition = shadow.observe(
                dependency_id=dependency_id,
                status=dependency_status,
                evidence_refs=evidence_refs,
                source_ref=source_ref,
                now_ms=event.observed_at_ms,
                # Raw health detail is intentionally not persisted.  Its digest
                # is normalized below and cannot influence reducer semantics.
                detail="",
                injected=False,
            )
            if transition is None:
                raise RuntimeGuardianError(
                    "guardian health event did not deterministically advance the reducer"
                )
            replayed = shadow.export_reducer_state()
            replayed_transition = transition.as_dict()
            replayed_transition["detail_digest"] = claimed["detail_digest"]
            if _digest(replayed_transition) != _digest(claimed):
                raise RuntimeGuardianError(
                    "guardian health transition is not the deterministic reducer result"
                )
            self._assert_replayed_degradation_state(
                replayed=replayed,
                claimed_state=next_degradation,
                claimed_transition=claimed,
                allow_opaque_detail=True,
            )
        except RuntimeGuardianError:
            raise
        except Exception as exc:
            raise RuntimeGuardianError(
                "guardian health degradation delta is invalid"
            ) from exc

    def _validate_recovery_commit_reducer_delta(
        self,
        *,
        event: GuardianEventV1,
        current_degradation: Mapping[str, Any],
        next_degradation: Mapping[str, Any],
    ) -> None:
        details = dict(event.payload)
        binding = details.get("binding")
        if not isinstance(binding, Mapping):
            raise RuntimeGuardianError(
                "guardian recovery commit lacks its exact binding"
            )
        binding = dict(binding)
        claimed = self._claimed_degradation_transition(next_degradation)
        try:
            review_id = _text(binding.get("review_id"), "recovery review_id")
            dependency_id = _text(
                binding.get("dependency_id"), "recovery dependency_id"
            )
            if (
                str(details.get("review_id") or "") != review_id
                or str(details.get("dependency_id") or "") != dependency_id
                or str(details.get("degradation_transition_id") or "")
                != str(claimed.get("transition_id") or "")
                or str(claimed.get("dependency_id") or "") != dependency_id
                or str(claimed.get("review_id") or "") != review_id
                or tuple(details.get("evidence_refs") or ())
                != tuple(claimed.get("evidence_refs") or ())
                or int(claimed.get("observed_at_ms") or -1)
                != event.observed_at_ms
            ):
                raise RuntimeGuardianError(
                    "guardian recovery commit evidence does not match its delta"
                )
            review_record = {
                "review": {
                    "review_id": review_id,
                    "status": "CONSUMED",
                    "subject_ref": binding["subject_ref"],
                    "subject_version": binding["subject_version"],
                    "resume_phase": binding["resume_phase"],
                    "checkpoint_id": binding["checkpoint_id"],
                    "resume_result_ref": binding["resume_result_ref"],
                    "decision_actor": claimed["source_ref"],
                }
            }
            shadow = DegradationStateMachine(restore_from_store=False)
            shadow.restore_reducer_state(current_degradation)
            transition = shadow.apply_consumed_recovery_acknowledgement(
                binding=binding,
                review_record=review_record,
                now_ms=event.observed_at_ms,
            )
            if _digest(transition.as_dict()) != _digest(claimed):
                raise RuntimeGuardianError(
                    "guardian recovery transition is not the deterministic reducer result"
                )
            self._assert_replayed_degradation_state(
                replayed=shadow.export_reducer_state(),
                claimed_state=next_degradation,
                claimed_transition=claimed,
                allow_opaque_detail=False,
            )
        except RuntimeGuardianError:
            raise
        except Exception as exc:
            raise RuntimeGuardianError(
                "guardian recovery degradation delta is invalid"
            ) from exc

    def _validate_event_delta(self, event: GuardianEventV1) -> None:
        details = dict(event.payload)
        event_type = event.event_type
        next_mode = str(details.get("mode") or "")
        next_maintenance = details.get("maintenance_ref")
        next_sleep = details.get("sleep_ref")
        next_slo = set(details.get("slo_breaches") or ())
        next_ack = set(details.get("acknowledged_events") or ())
        next_degradation = details.get("degradation_state")
        current_degradation = self._degradation.export_reducer_state()

        def unchanged_except_degradation() -> bool:
            return (
                next_mode == self._mode
                and next_maintenance == self._maintenance_ref
                and next_sleep == self._sleep_ref
                and next_slo == self._slo_breaches
                and next_ack == self._acknowledged_events
            )

        def degradation_advanced_once() -> bool:
            return (
                isinstance(next_degradation, Mapping)
                and int(next_degradation.get("revision") or -1)
                == int(current_degradation.get("revision") or 0) + 1
            )

        def degradation_unchanged() -> bool:
            return (
                isinstance(next_degradation, Mapping)
                and _digest(next_degradation) == _digest(current_degradation)
            )

        if event_type == "GUARDIAN_HEALTH_OBSERVED":
            valid = unchanged_except_degradation() and degradation_advanced_once()
            if valid:
                self._validate_health_reducer_delta(
                    event=event,
                    current_degradation=current_degradation,
                    next_degradation=next_degradation,
                )
        elif event_type == "GUARDIAN_RECOVERY_ACK_PREPARED":
            valid = (
                unchanged_except_degradation()
                and degradation_unchanged()
                and bool(details.get("operation_id"))
                and isinstance(details.get("binding"), Mapping)
            )
        elif event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED":
            valid = (
                unchanged_except_degradation()
                and degradation_advanced_once()
                and bool(details.get("operation_id"))
            )
            if valid:
                self._validate_recovery_commit_reducer_delta(
                    event=event,
                    current_degradation=current_degradation,
                    next_degradation=next_degradation,
                )
        elif event_type == "GUARDIAN_MAINTENANCE_ENTERED":
            valid = (
                next_mode == "MAINTENANCE"
                and next_maintenance == details.get("reason_ref")
                and next_sleep is None
                and next_slo == self._slo_breaches
                and next_ack == self._acknowledged_events
                and degradation_unchanged()
            )
        elif event_type == "GUARDIAN_MAINTENANCE_EXITED":
            valid = (
                self._mode == "MAINTENANCE"
                and next_mode == "ACTIVE"
                and next_maintenance is None
                and next_sleep is None
                and next_slo == self._slo_breaches
                and next_ack == self._acknowledged_events
                and degradation_unchanged()
            )
        elif event_type == "GUARDIAN_SLEEP_ENTERED":
            valid = (
                next_mode == "SLEEP"
                and next_sleep == details.get("reason_ref")
                and next_maintenance is None
                and next_slo == self._slo_breaches
                and next_ack == self._acknowledged_events
                and degradation_unchanged()
            )
        elif event_type == "GUARDIAN_SLEEP_EXITED":
            valid = (
                self._mode == "SLEEP"
                and next_mode == "ACTIVE"
                and next_maintenance is None
                and next_sleep is None
                and next_slo == self._slo_breaches
                and next_ack == self._acknowledged_events
                and degradation_unchanged()
            )
        elif event_type == "GUARDIAN_SLO_BREACH":
            breach_ref = str(details.get("breach_ref") or "")
            valid = (
                bool(breach_ref)
                and next_mode == self._mode
                and next_maintenance == self._maintenance_ref
                and next_sleep == self._sleep_ref
                and next_slo == self._slo_breaches | {breach_ref}
                and next_ack == self._acknowledged_events
                and degradation_unchanged()
            )
        elif event_type == "GUARDIAN_SLO_RESOLVED":
            breach_ref = str(details.get("breach_ref") or "")
            valid = (
                breach_ref in self._slo_breaches
                and next_mode == self._mode
                and next_maintenance == self._maintenance_ref
                and next_sleep == self._sleep_ref
                and next_slo == self._slo_breaches - {breach_ref}
                and next_ack == self._acknowledged_events
                and degradation_unchanged()
            )
        elif event_type == "GUARDIAN_EVENT_ACKNOWLEDGED":
            target = str(details.get("target_event_id") or "")
            known = {item.event_id for item in self._history}
            valid = (
                target in known
                and next_mode == self._mode
                and next_maintenance == self._maintenance_ref
                and next_sleep == self._sleep_ref
                and next_slo == self._slo_breaches
                and next_ack == self._acknowledged_events | {target}
                and degradation_unchanged()
            )
        else:
            valid = False
        if not valid:
            raise RuntimeGuardianError(
                f"guardian event delta is invalid for {event_type}"
            )

    def _apply_event(self, event: GuardianEventV1) -> None:
        details = dict(event.payload)
        mode = str(details.get("mode") or "")
        if mode not in {"ACTIVE", "MAINTENANCE", "SLEEP"}:
            raise RuntimeGuardianError("guardian journal mode is invalid")
        maintenance_ref = details.get("maintenance_ref")
        sleep_ref = details.get("sleep_ref")
        if mode == "MAINTENANCE" and (not maintenance_ref or sleep_ref):
            raise RuntimeGuardianError("guardian maintenance identity is invalid")
        if mode == "SLEEP" and (not sleep_ref or maintenance_ref):
            raise RuntimeGuardianError("guardian sleep identity is invalid")
        if mode == "ACTIVE" and (maintenance_ref or sleep_ref):
            raise RuntimeGuardianError("active guardian cannot retain mode references")
        degradation_state = details.get("degradation_state")
        if not isinstance(degradation_state, Mapping):
            raise RuntimeGuardianError("guardian journal lacks degradation reducer state")
        try:
            self._degradation.restore_reducer_state(degradation_state)
        except Exception as exc:
            raise RuntimeGuardianError(
                "guardian journal degradation state is invalid"
            ) from exc
        self._mode = mode
        self._maintenance_ref = maintenance_ref
        self._sleep_ref = sleep_ref
        self._slo_breaches = {
            _text(item, "slo_breaches[]")
            for item in details.get("slo_breaches") or ()
        }
        self._acknowledged_events = {
            _text(item, "acknowledged_events[]")
            for item in details.get("acknowledged_events") or ()
        }
        if self._status() != event.status:
            raise RuntimeGuardianError("guardian journal status does not match state")

    def _capture_mutable_state(self) -> dict[str, Any]:
        return {
            "mode": self._mode,
            "maintenance_ref": self._maintenance_ref,
            "sleep_ref": self._sleep_ref,
            "slo_breaches": set(self._slo_breaches),
            "acknowledged_events": set(self._acknowledged_events),
            "degradation_state": self._degradation.export_reducer_state(),
        }

    def _restore_mutable_state(self, state: Mapping[str, Any]) -> None:
        self._mode = str(state["mode"])
        self._maintenance_ref = state.get("maintenance_ref")
        self._sleep_ref = state.get("sleep_ref")
        self._slo_breaches = set(state.get("slo_breaches") or ())
        self._acknowledged_events = set(state.get("acknowledged_events") or ())
        self._degradation.restore_reducer_state(state["degradation_state"])

    def _append(
        self,
        *,
        event_type: str,
        details: Mapping[str, Any],
        observed_at_ms: int,
    ) -> GuardianEventV1:
        self._require_writable()
        if (
            isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms <= 0
        ):
            raise RuntimeGuardianError(
                "guardian observed_at_ms must be a positive integer"
            )
        next_revision = self._revision + 1
        event = GuardianEventV1(
            event_type=event_type,
            revision=next_revision,
            status=self._status(),
            payload={
                **dict(details),
                "runtime_scope_id": self._runtime_scope_id,
                "runtime_owner_id": self._runtime_owner_id,
                "runtime_lease_id": self._runtime_lease_id,
                "fencing_token": self._fencing_token,
                "mode": self._mode,
                "maintenance_ref": self._maintenance_ref,
                "sleep_ref": self._sleep_ref,
                "slo_breaches": sorted(self._slo_breaches),
                "acknowledged_events": sorted(self._acknowledged_events),
                "degradation_state": self._degradation.export_reducer_state(),
            },
            observed_at_ms=observed_at_ms,
        )
        if self._event_store is not None:
            from host_brain.integrations.runtime_events import RuntimeEvent  # noqa: PLC0415

            evidence = _refs(tuple(details.get("evidence_refs") or ())) if details.get("evidence_refs") else ()
            runtime_event = RuntimeEvent.create(
                # One stable outer event type/source makes RuntimeEvent's
                # unique dedupe key a compare-and-swap on aggregate revision.
                # The typed logical event remains inside the digest-bound payload.
                event_type=self._JOURNAL_EVENT_TYPE,
                source_kind="SYSTEM",
                source_ref=f"runtime-guardian://{self._aggregate_id}",
                aggregate_type=self._EVENT_AGGREGATE_TYPE,
                aggregate_id=self._aggregate_id,
                aggregate_version=event.revision,
                payload_ref=f"guardian://event/{event.event_digest}",
                payload={
                    "event_type": event.event_type,
                    "status": event.status,
                    "details": dict(event.payload),
                    "observed_at_ms": event.observed_at_ms,
                    "event_digest": event.event_digest,
                },
                evidence_refs=evidence,
                event_id=event.event_id,
                ts_ms=event.observed_at_ms,
            )
            append_error: Exception | None = None
            for _attempt in range(2):
                try:
                    record = self._event_store.append_event(runtime_event)
                    if (
                        int(record.get("aggregate_version") or 0)
                        != event.revision
                        or str(record.get("event_id") or "") != event.event_id
                    ):
                        raise RuntimeGuardianError(
                            "guardian durable append returned a different event"
                        )
                    append_error = None
                    break
                except Exception as exc:  # pragma: no cover - store-specific failure
                    append_error = exc
            if append_error is not None:
                try:
                    self._reload_guardian_journal()
                except Exception as replay_exc:
                    self._write_authority_status = "RECONCILIATION_REQUIRED"
                    raise _GuardianStateReconciledError(
                        "guardian append outcome requires reconciliation"
                    ) from replay_exc
                durable = next(
                    (
                        item
                        for item in self._history
                        if item.revision == event.revision
                    ),
                    None,
                )
                if durable is not None and durable.event_id == event.event_id:
                    return durable
                if durable is not None:
                    self._write_authority_status = "WRITE_FENCED"
                    raise _GuardianStateReconciledError(
                        "guardian aggregate revision was won by another event"
                    ) from append_error
                raise RuntimeGuardianError(
                    "guardian event could not be durably appended"
                ) from append_error
        self._revision = event.revision
        self._history.append(event)
        return event

    def snapshot(self) -> GuardianSnapshotV1:
        with self._lock:
            if self._write_authority_status == "RECONCILIATION_REQUIRED":
                raise RuntimeGuardianError(
                    "guardian state requires reconciliation before it can be read"
                )
            self._read_runtime_authority()
            degradation = self._degradation.snapshot()
            active = tuple(
                str(row.get("dependency_id"))
                for row in degradation.active_dependencies
                if row.get("dependency_id")
            )
            pending = tuple(
                str(row.get("dependency_id"))
                for row in degradation.recovery_pending_dependencies
                if row.get("dependency_id")
            )
            return GuardianSnapshotV1(
                runtime_scope_id=self._runtime_scope_id,
                runtime_owner_id=self._runtime_owner_id,
                runtime_lease_id=self._runtime_lease_id,
                fencing_token=self._fencing_token,
                revision=self._revision,
                status=self._status(),
                mode=self._mode,
                active_dependencies=active,
                recovery_pending_dependencies=pending,
                maintenance_ref=self._maintenance_ref,
                sleep_ref=self._sleep_ref,
                slo_breaches=tuple(self._slo_breaches),
                acknowledged_events=tuple(self._acknowledged_events),
                last_event_digest=self._history[-1].event_digest if self._history else None,
            )

    def degradation_snapshot(self) -> Any:
        """Expose the pure reducer read model through the Guardian owner."""

        with self._lock:
            if self._write_authority_status == "RECONCILIATION_REQUIRED":
                raise RuntimeGuardianError(
                    "guardian state requires reconciliation before it can be read"
                )
            self._read_runtime_authority()
            return self._degradation.snapshot()

    def observe_dependency(
        self,
        *,
        dependency_id: str,
        status: str,
        evidence_refs: Sequence[str],
        source_ref: str,
        observed_at_ms: int | None = None,
        detail: str = "",
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        refs = _refs(evidence_refs)
        with self._lock:
            before = self._capture_mutable_state()
            try:
                transition = self._degradation.observe(
                    dependency_id=dependency_id,
                    status=status,
                    evidence_refs=refs,
                    source_ref=_text(source_ref, "source_ref"),
                    now_ms=observed,
                    detail=detail,
                )
                if transition is not None:
                    self._append(
                        event_type="GUARDIAN_HEALTH_OBSERVED",
                        details={
                            "dependency_id": transition.dependency_id,
                            "dependency_status": transition.dependency_status,
                            "degradation_event_type": transition.event_type,
                            "degradation_transition_id": transition.transition_id,
                            "source_ref": transition.source_ref,
                            "evidence_refs": refs,
                        },
                        observed_at_ms=observed,
                    )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def acknowledge_recovery(
        self,
        *,
        dependency_id: str,
        review_store: Any,
        review_id: str,
        resume_token_ref: str,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        with self._lock:
            binding = self._degradation.prepare_recovery_acknowledgement(
                dependency_id=dependency_id,
                review_id=_text(review_id, "review_id"),
            )
            binding_identity = self._recovery_binding_identity(binding)
            operation_id = (
                "guardian-recovery-ack://sha256/"
                f"{_digest(binding_identity)}"
            )
            committed = next(
                (
                    item
                    for item in self._history
                    if item.event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED"
                    and isinstance(item.payload.get("binding"), Mapping)
                    and self._same_recovery_binding(
                        item.payload["binding"],
                        binding,
                    )
                ),
                None,
            )
            if committed is not None:
                return self.snapshot()
            prepared = next(
                (
                    item
                    for item in self._history
                    if item.event_type == "GUARDIAN_RECOVERY_ACK_PREPARED"
                    and isinstance(item.payload.get("binding"), Mapping)
                    and self._same_recovery_binding(
                        item.payload["binding"],
                        binding,
                    )
                ),
                None,
            )
            if prepared is not None:
                operation_id = _text(
                    prepared.payload.get("operation_id"),
                    "prepared recovery operation_id",
                )
            if prepared is None:
                self._append(
                    event_type="GUARDIAN_RECOVERY_ACK_PREPARED",
                    details={
                        "operation_id": operation_id,
                        "binding": binding,
                        "evidence_refs": tuple(binding.get("evidence_refs") or ()),
                    },
                    observed_at_ms=observed,
                )
            try:
                resumed = review_store.resume_review(
                    str(binding["review_id"]),
                    resume_token_ref=_text(
                        resume_token_ref,
                        "resume_token_ref",
                    ),
                    subject_ref=binding["subject_ref"],
                    subject_version=binding["subject_version"],
                    resume_phase=binding["resume_phase"],
                    checkpoint_id=binding["checkpoint_id"],
                    resume_result_ref=binding["resume_result_ref"],
                )
                if bool(resumed.get("replay_side_effects")):
                    raise RuntimeGuardianError(
                        "ReviewStore attempted to replay recovery side effects"
                    )
                review_record = dict(resumed.get("review") or {})
            except Exception as resume_exc:
                try:
                    review_record = dict(
                        review_store.get_review(str(binding["review_id"]))
                    )
                except Exception:
                    raise RuntimeGuardianError(
                        "recovery review consume outcome is unknown"
                    ) from resume_exc
                review_item = dict(review_record.get("review") or {})
                if (
                    str(review_item.get("status") or "") != "CONSUMED"
                    or str(review_item.get("resume_result_ref") or "")
                    != str(binding["resume_result_ref"])
                ):
                    raise RuntimeGuardianError(
                        "recovery review was not consumed by this operation"
                    ) from resume_exc

            before = self._capture_mutable_state()
            try:
                transition = (
                    self._degradation.apply_consumed_recovery_acknowledgement(
                        binding=binding,
                        review_record=review_record,
                        now_ms=observed,
                    )
                )
                self._append(
                    event_type="GUARDIAN_RECOVERY_ACK_COMMITTED",
                    details={
                        "operation_id": operation_id,
                        "binding": binding,
                        "dependency_id": transition.dependency_id,
                        "degradation_transition_id": transition.transition_id,
                        "review_id": binding["review_id"],
                        "evidence_refs": tuple(transition.evidence_refs),
                    },
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def reconcile_prepared_recoveries(
        self,
        *,
        review_store: Any,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        """Finish only exact PREPARED operations whose Review is already consumed."""

        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        with self._lock:
            committed_ids = {
                str(item.payload.get("operation_id") or "")
                for item in self._history
                if item.event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED"
            }
            committed_binding_digests = {
                _digest(
                    self._recovery_binding_identity(item.payload["binding"])
                )
                for item in self._history
                if item.event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED"
                and isinstance(item.payload.get("binding"), Mapping)
            }
            prepared = tuple(
                item
                for item in self._history
                if item.event_type == "GUARDIAN_RECOVERY_ACK_PREPARED"
                and str(item.payload.get("operation_id") or "")
                not in committed_ids
            )
            for item in prepared:
                prepared_binding = dict(item.payload.get("binding") or {})
                prepared_identity_digest = _digest(
                    self._recovery_binding_identity(prepared_binding)
                )
                if prepared_identity_digest in committed_binding_digests:
                    continue
                operation_id = str(item.payload.get("operation_id") or "")
                try:
                    review_record = dict(
                        review_store.get_review(str(prepared_binding["review_id"]))
                    )
                except Exception:
                    continue
                review_item = dict(review_record.get("review") or {})
                if (
                    str(review_item.get("status") or "") != "CONSUMED"
                    or str(review_item.get("resume_result_ref") or "")
                    != str(prepared_binding.get("resume_result_ref") or "")
                ):
                    continue
                binding = self._degradation.prepare_recovery_acknowledgement(
                    dependency_id=str(prepared_binding["dependency_id"]),
                    review_id=str(prepared_binding["review_id"]),
                )
                if not self._same_recovery_binding(prepared_binding, binding):
                    raise RuntimeGuardianError(
                        "prepared recovery binding drifted before reconciliation"
                    )
                before = self._capture_mutable_state()
                try:
                    transition = self._degradation.apply_consumed_recovery_acknowledgement(
                        binding=binding,
                        review_record=review_record,
                        now_ms=observed,
                    )
                    self._append(
                        event_type="GUARDIAN_RECOVERY_ACK_COMMITTED",
                        details={
                            "operation_id": operation_id,
                            "binding": binding,
                            "dependency_id": transition.dependency_id,
                            "degradation_transition_id": transition.transition_id,
                            "review_id": binding["review_id"],
                            "evidence_refs": tuple(transition.evidence_refs),
                        },
                        observed_at_ms=observed,
                    )
                    committed_binding_digests.add(prepared_identity_digest)
                except _GuardianStateReconciledError:
                    raise
                except Exception:
                    self._restore_mutable_state(before)
                    raise
            return self.snapshot()

    def enter_maintenance(self, *, reason_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        reason = _text(reason_ref, "reason_ref")
        with self._lock:
            if self._mode == "MAINTENANCE" and self._maintenance_ref == reason:
                return self.snapshot()
            before = self._capture_mutable_state()
            try:
                self._mode = "MAINTENANCE"
                self._maintenance_ref = reason
                self._sleep_ref = None
                self._append(
                    event_type="GUARDIAN_MAINTENANCE_ENTERED",
                    details={"reason_ref": reason, "evidence_refs": (reason,)},
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def exit_maintenance(self, *, acknowledgement_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        ack = _text(acknowledgement_ref, "acknowledgement_ref")
        with self._lock:
            if self._mode != "MAINTENANCE":
                return self.snapshot()
            before = self._capture_mutable_state()
            try:
                self._mode = "ACTIVE"
                self._maintenance_ref = None
                self._sleep_ref = None
                self._append(
                    event_type="GUARDIAN_MAINTENANCE_EXITED",
                    details={"acknowledgement_ref": ack, "evidence_refs": (ack,)},
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def enter_sleep(self, *, reason_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        reason = _text(reason_ref, "reason_ref")
        with self._lock:
            if self._mode == "SLEEP" and self._sleep_ref == reason:
                return self.snapshot()
            before = self._capture_mutable_state()
            try:
                self._mode = "SLEEP"
                self._sleep_ref = reason
                self._maintenance_ref = None
                self._append(
                    event_type="GUARDIAN_SLEEP_ENTERED",
                    details={"reason_ref": reason, "evidence_refs": (reason,)},
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def exit_sleep(self, *, acknowledgement_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        ack = _text(acknowledgement_ref, "acknowledgement_ref")
        with self._lock:
            if self._mode != "SLEEP":
                return self.snapshot()
            before = self._capture_mutable_state()
            try:
                self._mode = "ACTIVE"
                self._sleep_ref = None
                self._maintenance_ref = None
                self._append(
                    event_type="GUARDIAN_SLEEP_EXITED",
                    details={"acknowledgement_ref": ack, "evidence_refs": (ack,)},
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def record_slo_breach(
        self,
        *,
        slo_id: str,
        observed_value: float,
        threshold: float,
        evidence_ref: str,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        slo = _text(slo_id, "slo_id")
        evidence = _text(evidence_ref, "evidence_ref")
        if "://" not in evidence:
            raise RuntimeGuardianError("evidence_ref must be durable")
        breach_ref = f"slo-breach://{_digest({'slo_id': slo, 'observed': observed_value, 'threshold': threshold, 'evidence': evidence})}"
        with self._lock:
            if breach_ref in self._slo_breaches:
                return self.snapshot()
            before = self._capture_mutable_state()
            try:
                self._slo_breaches.add(breach_ref)
                self._append(
                    event_type="GUARDIAN_SLO_BREACH",
                    details={
                        "slo_id": slo,
                        "observed_value": float(observed_value),
                        "threshold": float(threshold),
                        "breach_ref": breach_ref,
                        "evidence_refs": (evidence,),
                    },
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def resolve_slo_breach(
        self,
        *,
        breach_ref: str,
        evidence_ref: str,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        breach = _text(breach_ref, "breach_ref")
        evidence = _text(evidence_ref, "evidence_ref")
        if "://" not in breach or "://" not in evidence:
            raise RuntimeGuardianError("SLO resolution requires durable references")
        with self._lock:
            if breach not in self._slo_breaches:
                raise RuntimeGuardianError("cannot resolve an unknown SLO breach")
            before = self._capture_mutable_state()
            try:
                self._slo_breaches.remove(breach)
                self._append(
                    event_type="GUARDIAN_SLO_RESOLVED",
                    details={
                        "breach_ref": breach,
                        "evidence_refs": (evidence,),
                    },
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def acknowledge_event(self, *, event_id: str, acknowledgement_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        target = _text(event_id, "event_id")
        ack = _text(acknowledgement_ref, "acknowledgement_ref")
        with self._lock:
            known = {item.event_id for item in self._history}
            if target not in known:
                raise RuntimeGuardianError("cannot acknowledge an unknown guardian event")
            if target in self._acknowledged_events:
                return self.snapshot()
            before = self._capture_mutable_state()
            try:
                self._acknowledged_events.add(target)
                self._append(
                    event_type="GUARDIAN_EVENT_ACKNOWLEDGED",
                    details={
                        "target_event_id": target,
                        "acknowledgement_ref": ack,
                        "evidence_refs": (ack,),
                    },
                    observed_at_ms=observed,
                )
            except _GuardianStateReconciledError:
                raise
            except Exception:
                self._restore_mutable_state(before)
                raise
            return self.snapshot()

    def event_history(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(item.to_dict() for item in self._history)


__all__ = [
    "GuardianEventV1",
    "GuardianSnapshotV1",
    "RuntimeGuardian",
    "RuntimeGuardianError",
]
