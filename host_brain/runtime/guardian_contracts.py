"""Pure contracts and value helpers for the app-local RuntimeGuardian."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class RuntimeGuardianError(RuntimeError):
    """Guardian input, journal or replay is not safe to accept."""


class _GuardianStateReconciledError(RuntimeGuardianError):
    """A competing durable head replaced the attempted in-memory mutation."""


GUARDIAN_DEGRADATION_AUTHORITY_SCHEMA_VERSION = (
    "Natsume.runtime_guardian.degradation_authority.v1"
)


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
    result = tuple(
        sorted(
            {
                str(item or "").strip()
                for item in values
                if str(item or "").strip()
            }
        )
    )
    if any("://" not in item for item in result):
        raise RuntimeGuardianError("guardian evidence must be durable references")
    return result


def _risk_level(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 5


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
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise RuntimeGuardianError("guardian revision must be positive")
        if (
            isinstance(self.observed_at_ms, bool)
            or not isinstance(self.observed_at_ms, int)
            or self.observed_at_ms < 0
        ):
            raise RuntimeGuardianError("observed_at_ms must be non-negative")
        if not isinstance(self.payload, Mapping):
            raise RuntimeGuardianError("guardian payload must be a mapping")
        payload = json.loads(
            json.dumps(
                dict(self.payload),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
        )
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
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise RuntimeGuardianError(
                "guardian snapshot revision must be non-negative"
            )
        mode = _text(self.mode, "mode")
        status = _text(self.status, "status")
        active = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.active_dependencies
                    if str(item).strip()
                }
            )
        )
        pending = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.recovery_pending_dependencies
                    if str(item).strip()
                }
            )
        )
        slo = tuple(
            sorted(
                {str(item).strip() for item in self.slo_breaches if str(item).strip()}
            )
        )
        acknowledged = tuple(
            sorted(
                {
                    str(item).strip()
                    for item in self.acknowledged_events
                    if str(item).strip()
                }
            )
        )
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
            "recovery_pending_dependencies": list(
                self.recovery_pending_dependencies
            ),
            "maintenance_ref": self.maintenance_ref,
            "sleep_ref": self.sleep_ref,
            "slo_breaches": list(self.slo_breaches),
            "acknowledged_events": list(self.acknowledged_events),
            "last_event_digest": self.last_event_digest,
            "snapshot_digest": self.snapshot_digest,
        }


__all__ = [
    "GUARDIAN_DEGRADATION_AUTHORITY_SCHEMA_VERSION",
    "GuardianEventV1",
    "GuardianSnapshotV1",
    "RuntimeGuardianError",
]
