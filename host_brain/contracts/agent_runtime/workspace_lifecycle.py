"""Neutral durable contracts for one governed workspace lifecycle.

The contract intentionally contains no filesystem, Git process, SQLite, or
application dependencies.  It freezes the evidence carried by the append-only
workspace lifecycle journal so persistence and future executors share one
canonical receipt shape without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping


WORKSPACE_LIFECYCLE_SCHEMA_VERSION = "Natsume.agent.workspace_lifecycle.v1"
WORKSPACE_LIFECYCLE_GENESIS_STATE = "ACTIVE"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")


class WorkspaceLifecycleContractError(ValueError):
    """Raised when durable lifecycle evidence is not canonical."""


class WorkspaceLifecycleState(StrEnum):
    ACTIVE = "ACTIVE"
    ACCEPTED = "ACCEPTED"
    PROMOTED = "PROMOTED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class WorkspaceLifecycleEventV1:
    """One immutable transition and its exact execution/evidence binding."""

    event_id: str
    workspace_id: str
    transition_revision: int
    from_state: WorkspaceLifecycleState
    to_state: WorkspaceLifecycleState
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    task_lease_id: str
    task_lease_fencing_token: int
    workspace_lease_ref: str
    workspace_lease_owner: str
    workspace_lease_fencing_token: int
    base_commit: str | None
    accepted_head: str | None
    tree_digest: str | None
    diff_digest: str | None
    verification_ref: str | None
    acceptance_ref: str | None
    acceptance_event_id: str | None
    terminal_ref: str | None
    reason_code: str | None
    previous_event_digest: str | None
    event_digest: str
    created_ts_ms: int
    schema_version: str = WORKSPACE_LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_event(self)
        expected = self.compute_event_digest()
        if self.event_digest != expected:
            raise WorkspaceLifecycleContractError(
                "event_digest does not match the canonical lifecycle payload"
            )

    @property
    def carries_acceptance(self) -> bool:
        return self.base_commit is not None

    def digest_payload(self) -> dict[str, Any]:
        """Return the canonical payload covered by ``event_digest``."""

        return {
            field.name: _json_value(getattr(self, field.name))
            for field in fields(self)
            if field.name != "event_digest"
        }

    def compute_event_digest(self) -> str:
        encoded = json.dumps(
            self.digest_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = self.digest_payload()
        payload["event_digest"] = self.event_digest
        return payload

    @classmethod
    def create(
        cls,
        **values: Any,
    ) -> "WorkspaceLifecycleEventV1":
        """Build one event and derive its digest from canonical fields."""

        normalized = dict(values)
        normalized.setdefault("schema_version", WORKSPACE_LIFECYCLE_SCHEMA_VERSION)
        normalized["from_state"] = WorkspaceLifecycleState(
            str(normalized["from_state"])
        )
        normalized["to_state"] = WorkspaceLifecycleState(str(normalized["to_state"]))
        payload = {
            field.name: _json_value(normalized[field.name])
            for field in fields(cls)
            if field.name != "event_digest"
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        normalized["event_digest"] = (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        )
        return cls(**normalized)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkspaceLifecycleEventV1":
        payload = dict(value)
        payload["from_state"] = WorkspaceLifecycleState(str(payload["from_state"]))
        payload["to_state"] = WorkspaceLifecycleState(str(payload["to_state"]))
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class WorkspaceLifecycleMutationV1:
    event: WorkspaceLifecycleEventV1
    current_state: WorkspaceLifecycleState
    current_revision: int
    replayed: bool = False

    def __post_init__(self) -> None:
        if type(self.current_revision) is not int or self.current_revision < 1:
            raise WorkspaceLifecycleContractError(
                "current_revision must be a positive integer"
            )
        if self.current_revision < self.event.transition_revision:
            raise WorkspaceLifecycleContractError(
                "current lifecycle revision cannot precede the returned event"
            )


@dataclass(frozen=True, slots=True)
class WorkspaceLifecycleSnapshotV1:
    workspace_id: str
    state: WorkspaceLifecycleState
    revision: int
    latest_event: WorkspaceLifecycleEventV1 | None
    accepted_event: WorkspaceLifecycleEventV1 | None

    def __post_init__(self) -> None:
        _canonical_text(self.workspace_id, field_name="workspace_id", maximum=120)
        if type(self.revision) is not int or self.revision < 0:
            raise WorkspaceLifecycleContractError(
                "workspace lifecycle revision must be non-negative"
            )
        if self.revision == 0:
            if (
                self.state is not WorkspaceLifecycleState.ACTIVE
                or self.latest_event is not None
                or self.accepted_event is not None
            ):
                raise WorkspaceLifecycleContractError(
                    "an empty lifecycle must be the ACTIVE genesis state"
                )
            return
        if (
            self.latest_event is None
            or self.latest_event.workspace_id != self.workspace_id
            or self.latest_event.transition_revision != self.revision
            or self.latest_event.to_state is not self.state
        ):
            raise WorkspaceLifecycleContractError(
                "lifecycle snapshot does not match its latest event"
            )
        if self.state in {
            WorkspaceLifecycleState.ACCEPTED,
            WorkspaceLifecycleState.PROMOTED,
        } or (
            self.state is WorkspaceLifecycleState.ABANDONED
            and self.latest_event.carries_acceptance
        ):
            if (
                self.accepted_event is None
                or self.accepted_event.to_state is not WorkspaceLifecycleState.ACCEPTED
                or self.accepted_event.workspace_id != self.workspace_id
            ):
                raise WorkspaceLifecycleContractError(
                    "lifecycle snapshot is missing its accepted receipt"
                )
        elif self.accepted_event is not None:
            raise WorkspaceLifecycleContractError(
                "an ACTIVE/direct-abandon lifecycle cannot expose acceptance"
            )


def lifecycle_event_identity(event: WorkspaceLifecycleEventV1) -> dict[str, Any]:
    """Project caller-controlled identity fields for exact replay checks."""

    payload = event.digest_payload()
    payload.pop("created_ts_ms")
    payload.pop("schema_version")
    payload.pop("previous_event_digest")
    return payload


def _validate_event(event: WorkspaceLifecycleEventV1) -> None:
    if event.schema_version != WORKSPACE_LIFECYCLE_SCHEMA_VERSION:
        raise WorkspaceLifecycleContractError("workspace lifecycle schema is invalid")
    for field_name, value, maximum in (
        ("event_id", event.event_id, 240),
        ("workspace_id", event.workspace_id, 120),
        ("graph_id", event.graph_id, 240),
        ("node_id", event.node_id, 240),
        ("attempt_id", event.attempt_id, 240),
        ("task_lease_id", event.task_lease_id, 240),
        ("workspace_lease_ref", event.workspace_lease_ref, 512),
        ("workspace_lease_owner", event.workspace_lease_owner, 240),
    ):
        _canonical_text(value, field_name=field_name, maximum=maximum)
    for field_name, value in (
        ("transition_revision", event.transition_revision),
        ("graph_revision", event.graph_revision),
        ("task_lease_fencing_token", event.task_lease_fencing_token),
        ("workspace_lease_fencing_token", event.workspace_lease_fencing_token),
    ):
        if type(value) is not int or value < 1:
            raise WorkspaceLifecycleContractError(
                f"{field_name} must be a positive integer"
            )
    if type(event.created_ts_ms) is not int or event.created_ts_ms < 0:
        raise WorkspaceLifecycleContractError(
            "created_ts_ms must be a non-negative integer"
        )
    if event.transition_revision == 1:
        if event.previous_event_digest is not None:
            raise WorkspaceLifecycleContractError(
                "the first lifecycle event cannot have a previous digest"
            )
    elif not _is_digest(event.previous_event_digest):
        raise WorkspaceLifecycleContractError(
            "a later lifecycle event requires a canonical previous digest"
        )
    if not _is_digest(event.event_digest):
        raise WorkspaceLifecycleContractError("event_digest is not canonical")

    allowed = {
        (WorkspaceLifecycleState.ACTIVE, WorkspaceLifecycleState.ACCEPTED),
        (WorkspaceLifecycleState.ACCEPTED, WorkspaceLifecycleState.PROMOTED),
        (WorkspaceLifecycleState.ACTIVE, WorkspaceLifecycleState.ABANDONED),
        (WorkspaceLifecycleState.ACCEPTED, WorkspaceLifecycleState.ABANDONED),
    }
    if (event.from_state, event.to_state) not in allowed:
        raise WorkspaceLifecycleContractError("workspace lifecycle transition is invalid")

    evidence = (
        event.base_commit,
        event.accepted_head,
        event.tree_digest,
        event.diff_digest,
        event.verification_ref,
        event.acceptance_ref,
    )
    carries_acceptance = all(value is not None for value in evidence)
    if any(value is not None for value in evidence) and not carries_acceptance:
        raise WorkspaceLifecycleContractError(
            "workspace acceptance evidence must be complete or absent"
        )
    if event.to_state is WorkspaceLifecycleState.ACCEPTED:
        if not carries_acceptance or event.acceptance_event_id is not None:
            raise WorkspaceLifecycleContractError(
                "ACCEPTED requires complete evidence and no parent acceptance event"
            )
    elif event.from_state is WorkspaceLifecycleState.ACCEPTED:
        if not carries_acceptance or not event.acceptance_event_id:
            raise WorkspaceLifecycleContractError(
                "a transition from ACCEPTED must preserve its accepted receipt"
            )
    elif carries_acceptance or event.acceptance_event_id is not None:
        raise WorkspaceLifecycleContractError(
            "a direct ACTIVE transition cannot manufacture acceptance evidence"
        )

    if carries_acceptance:
        if not _GIT_OBJECT_ID.fullmatch(str(event.base_commit)):
            raise WorkspaceLifecycleContractError("base_commit is not a Git object id")
        if not _GIT_OBJECT_ID.fullmatch(str(event.accepted_head)):
            raise WorkspaceLifecycleContractError(
                "accepted_head is not a Git object id"
            )
        if not _is_digest(event.tree_digest) or not _is_digest(event.diff_digest):
            raise WorkspaceLifecycleContractError(
                "tree_digest and diff_digest must be sha256 digests"
            )
        _canonical_text(
            event.verification_ref,
            field_name="verification_ref",
            maximum=512,
        )
        _canonical_text(
            event.acceptance_ref,
            field_name="acceptance_ref",
            maximum=512,
        )
    if event.acceptance_event_id is not None:
        _canonical_text(
            event.acceptance_event_id,
            field_name="acceptance_event_id",
            maximum=240,
        )

    if event.to_state is WorkspaceLifecycleState.PROMOTED:
        _canonical_text(event.terminal_ref, field_name="terminal_ref", maximum=512)
        if event.reason_code is not None:
            raise WorkspaceLifecycleContractError("PROMOTED cannot have a reason_code")
    elif event.to_state is WorkspaceLifecycleState.ABANDONED:
        _canonical_text(event.terminal_ref, field_name="terminal_ref", maximum=512)
        if not isinstance(event.reason_code, str) or not _REASON_CODE.fullmatch(
            event.reason_code
        ):
            raise WorkspaceLifecycleContractError(
                "ABANDONED requires a canonical reason_code"
            )
    elif event.terminal_ref is not None or event.reason_code is not None:
        raise WorkspaceLifecycleContractError(
            "ACCEPTED cannot carry terminal evidence"
        )


def _canonical_text(value: Any, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise WorkspaceLifecycleContractError(
            f"{field_name} must be non-empty canonical text"
        )
    if len(value) > maximum:
        raise WorkspaceLifecycleContractError(
            f"{field_name} exceeds {maximum} characters"
        )
    return value


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _json_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    return value


__all__ = [
    "WORKSPACE_LIFECYCLE_GENESIS_STATE",
    "WORKSPACE_LIFECYCLE_SCHEMA_VERSION",
    "WorkspaceLifecycleContractError",
    "WorkspaceLifecycleEventV1",
    "WorkspaceLifecycleMutationV1",
    "WorkspaceLifecycleSnapshotV1",
    "WorkspaceLifecycleState",
    "lifecycle_event_identity",
]
