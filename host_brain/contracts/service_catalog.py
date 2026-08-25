"""Runtime-independent contracts for governed Service Catalog rollout."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Protocol


class CatalogMode(str, Enum):
    """Ordered activation stages shared by every Service Catalog layer."""

    AUDIT = "AUDIT"
    SHADOW = "SHADOW"
    PREFLIGHT = "PREFLIGHT"
    CANARY = "CANARY"
    ENFORCE = "ENFORCE"


_STAGE_RANK = {
    CatalogMode.AUDIT: 0,
    CatalogMode.SHADOW: 1,
    CatalogMode.PREFLIGHT: 2,
    CatalogMode.CANARY: 3,
    CatalogMode.ENFORCE: 4,
}
_TERMINAL_EVENTS = frozenset(
    {"ACTIVATION_STARTED", "ACTIVATION_FAILED", "ACTIVATION_ROLLED_BACK"}
)
_EVENT_STATUSES = {
    "ROLLOUT_PREPARED": frozenset({"PREPARED", "BLOCKED"}),
    "ACTIVATION_STARTING": frozenset({"STARTING"}),
    "ACTIVATION_STARTED": frozenset({"STARTED"}),
    "ACTIVATION_FAILED": frozenset({"FAILED"}),
    "ACTIVATION_ROLLED_BACK": frozenset({"ROLLED_BACK"}),
}


class ServiceCatalogRolloutError(RuntimeError):
    """Fail-closed rollout authority failure."""


def _text(value: object, field: str, maximum: int = 512) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum or any(ord(ch) < 32 for ch in result):
        raise ServiceCatalogRolloutError(f"{field} is invalid")
    return result


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def catalog_operation_identity_digest(operation_id: str) -> str:
    """Return the transport-safe digest bound to one immutable operation id."""

    return _digest({"operation_id": _text(operation_id, "operation_id")})


def _stage(value: CatalogMode | str) -> CatalogMode:
    try:
        return (
            value
            if isinstance(value, CatalogMode)
            else CatalogMode(str(value).strip().upper())
        )
    except ValueError as exc:
        raise ServiceCatalogRolloutError(
            "service catalog rollout stage is invalid"
        ) from exc


def _validate_receipt_transition(
    *,
    event: str,
    status: str,
    previous_event: str | None,
    previous_status: str | None,
) -> None:
    allowed_statuses = _EVENT_STATUSES.get(event)
    if allowed_statuses is None or status not in allowed_statuses:
        raise ServiceCatalogRolloutError(
            "service catalog rollout event/status pair is invalid"
        )
    previous = (previous_event, previous_status)
    if event == "ROLLOUT_PREPARED":
        allowed = previous == (None, None)
    elif event == "ACTIVATION_STARTING":
        allowed = previous == ("ROLLOUT_PREPARED", "PREPARED")
    elif event in {"ACTIVATION_STARTED", "ACTIVATION_ROLLED_BACK"}:
        allowed = previous == ("ACTIVATION_STARTING", "STARTING")
    else:
        allowed = previous in {
            ("ROLLOUT_PREPARED", "BLOCKED"),
            ("ACTIVATION_STARTING", "STARTING"),
        }
    if not allowed:
        raise ServiceCatalogRolloutError(
            "service catalog rollout receipt transition is invalid"
        )


@dataclass(frozen=True, slots=True)
class CatalogCoordinatorAuthorityV1:
    runtime_scope_id: str
    owner_id: str
    lease_id: str
    fencing_token: int
    issued_at_ms: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_scope_id",
            _text(self.runtime_scope_id, "runtime_scope_id"),
        )
        object.__setattr__(self, "owner_id", _text(self.owner_id, "owner_id"))
        object.__setattr__(self, "lease_id", _text(self.lease_id, "lease_id"))
        if type(self.fencing_token) is not int or self.fencing_token < 1:
            raise ServiceCatalogRolloutError("fencing_token is invalid")
        if (
            type(self.issued_at_ms) is not int
            or type(self.expires_at_ms) is not int
            or self.issued_at_ms < 0
            or self.expires_at_ms <= self.issued_at_ms
        ):
            raise ServiceCatalogRolloutError("coordinator lease interval is invalid")


@dataclass(frozen=True, slots=True)
class CatalogRolloutOperationV1:
    operation_id: str
    catalog_snapshot_digest: str
    candidate_owner_id: str
    stage: CatalogMode
    endpoint_ids: tuple[str, ...]
    rollback_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _text(self.operation_id, "operation_id")
        )
        object.__setattr__(
            self,
            "catalog_snapshot_digest",
            _text(self.catalog_snapshot_digest, "catalog_snapshot_digest"),
        )
        object.__setattr__(
            self,
            "candidate_owner_id",
            _text(self.candidate_owner_id, "candidate_owner_id"),
        )
        object.__setattr__(self, "stage", _stage(self.stage))
        endpoints = tuple(
            sorted(_text(item, "endpoint_id", 128) for item in self.endpoint_ids)
        )
        if not endpoints or len(endpoints) != len(set(endpoints)):
            raise ServiceCatalogRolloutError("endpoint_ids are invalid")
        object.__setattr__(self, "endpoint_ids", endpoints)
        object.__setattr__(
            self,
            "rollback_ref",
            _text(self.rollback_ref, "rollback_ref", 1024),
        )

    def immutable_payload(
        self,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        created_at_ms: int,
    ) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "runtime_scope_id": authority.runtime_scope_id,
            "coordinator_owner_id": authority.owner_id,
            "coordinator_lease_id": authority.lease_id,
            "coordinator_fencing_token": authority.fencing_token,
            "catalog_snapshot_digest": self.catalog_snapshot_digest,
            "candidate_owner_id": self.candidate_owner_id,
            "stage": self.stage.value,
            "stage_rank": _STAGE_RANK[self.stage],
            "endpoint_ids": list(self.endpoint_ids),
            "rollback_ref": self.rollback_ref,
            "created_at_ms": created_at_ms,
        }


class ServiceCatalogRolloutRepository(Protocol):
    """Behavioral port used by rollout applications and observers."""

    def acquire_authority(
        self,
        *,
        runtime_scope_id: str,
        owner_id: str,
        lease_ms: int = 15_000,
        now_ms: int | None = None,
    ) -> CatalogCoordinatorAuthorityV1 | None: ...

    def renew_authority(
        self,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        lease_ms: int = 15_000,
        now_ms: int | None = None,
    ) -> CatalogCoordinatorAuthorityV1 | None: ...

    def release_authority(
        self,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        now_ms: int | None = None,
    ) -> bool: ...

    def register_operation(
        self,
        operation: CatalogRolloutOperationV1,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        now_ms: int | None = None,
    ) -> dict[str, Any]: ...

    def get_operation(self, operation_id: str) -> dict[str, Any] | None: ...

    def append_receipt(
        self,
        operation_id: str,
        *,
        event_type: str,
        status: str,
        payload: Mapping[str, Any],
        authority: CatalogCoordinatorAuthorityV1 | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]: ...

    def list_receipts(self, operation_id: str) -> tuple[dict[str, Any], ...]: ...

    def operation_state(self, operation_id: str) -> str: ...

    def find_pending(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
        catalog_snapshot_digest: str,
        stage: CatalogMode | str,
    ) -> dict[str, Any] | None: ...

    def latest_terminal_receipt(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
    ) -> dict[str, Any] | None: ...

    def terminal_receipt(self, operation_id: str) -> dict[str, Any] | None: ...

    def rollout_head(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
    ) -> dict[str, Any] | None: ...


__all__ = [
    "CatalogMode",
    "CatalogCoordinatorAuthorityV1",
    "CatalogRolloutOperationV1",
    "ServiceCatalogRolloutError",
    "ServiceCatalogRolloutRepository",
    "catalog_operation_identity_digest",
]
