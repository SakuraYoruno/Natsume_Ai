"""Typed contracts for the NE-03 restrictive long-term-goal authority."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from host_brain.contracts.runtime_context import ContextConstraintV1


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,239}$")
_GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,159}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class LongTermGoalContractError(ValueError):
    """A long-term goal cannot be used as restrictive runtime authority."""


def _text(value: Any, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if (
        not result
        or len(result.encode("utf-8")) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in result)
    ):
        raise LongTermGoalContractError(f"{field} is invalid")
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, 240)
    if _IDENTIFIER_RE.fullmatch(result) is None:
        raise LongTermGoalContractError(f"{field} is not canonical")
    return result


@dataclass(frozen=True, slots=True)
class LongTermGoalConstraintSpecV1:
    """Constraint input before the durable goal reference is assigned."""

    code: str
    disposition: str
    reason: str = ""

    def __post_init__(self) -> None:
        code = str(self.code or "").strip().upper()
        disposition = str(self.disposition or "").strip().upper()
        reason = str(self.reason or "").strip()
        # Reuse ContextConstraintV1 as the canonical code/disposition parser.
        try:
            ContextConstraintV1(
                code=code,
                disposition=disposition,
                origin="LONG_TERM_GOAL",
                source_ref="long-term-goal://validation/spec/v1",
                reason=reason,
            )
        except Exception as exc:
            raise LongTermGoalContractError(
                "long-term goal constraint is invalid"
            ) from exc
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "reason", reason)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "LongTermGoalConstraintSpecV1":
        if not isinstance(value, Mapping) or set(value) != {
            "code",
            "disposition",
            "reason",
        }:
            raise LongTermGoalContractError(
                "long-term goal constraint fields drifted"
            )
        return cls(
            code=str(value["code"]),
            disposition=str(value["disposition"]),
            reason=str(value["reason"]),
        )

    def bind(self, goal_ref: str) -> ContextConstraintV1:
        return ContextConstraintV1(
            code=self.code,
            disposition=self.disposition,
            origin="LONG_TERM_GOAL",
            source_ref=goal_ref,
            reason=self.reason,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "disposition": self.disposition,
            "reason": self.reason,
        }


def normalize_long_term_goal_constraint_specs(
    values: object,
) -> tuple[LongTermGoalConstraintSpecV1, ...]:
    if not isinstance(values, (list, tuple)):
        raise LongTermGoalContractError("constraints must be a list")
    items = tuple(
        item
        if isinstance(item, LongTermGoalConstraintSpecV1)
        else LongTermGoalConstraintSpecV1.from_mapping(item)
        for item in values
    )
    if not items or len(items) > 16:
        raise LongTermGoalContractError(
            "constraints must contain between one and sixteen items"
        )
    canonical = {
        (item.code, item.disposition, item.reason): item for item in items
    }
    if len(canonical) != len(items):
        raise LongTermGoalContractError("constraints contain duplicates")
    return tuple(
        sorted(
            canonical.values(),
            key=lambda item: (item.code, item.disposition, item.reason),
        )
    )


@dataclass(frozen=True, slots=True)
class LongTermGoalSnapshotV1:
    """Canonical current head for one long-term goal."""

    runtime_scope_id: str
    workspace_id: str
    subject_id: str
    goal_id: str
    version: int
    status: str
    goal_ref: str
    constraints: tuple[ContextConstraintV1, ...]
    operation_id: str
    event_digest: str
    updated_at_ms: int

    def __post_init__(self) -> None:
        for field in ("runtime_scope_id", "workspace_id", "subject_id"):
            object.__setattr__(
                self,
                field,
                _identifier(getattr(self, field), field),
            )
        goal_id = _text(self.goal_id, "goal_id", 160)
        if _GOAL_ID_RE.fullmatch(goal_id) is None:
            raise LongTermGoalContractError("goal_id is not canonical")
        object.__setattr__(self, "goal_id", goal_id)
        if isinstance(self.version, bool) or type(self.version) is not int or self.version < 1:
            raise LongTermGoalContractError("version must be positive")
        status = str(self.status or "").strip().upper()
        if status not in {"ACTIVE", "REVOKED"}:
            raise LongTermGoalContractError("status is invalid")
        object.__setattr__(self, "status", status)
        goal_ref = _text(self.goal_ref, "goal_ref", 1024)
        expected_suffix = f"/{goal_id}/v{self.version}"
        if (
            not goal_ref.startswith("long-term-goal://")
            or not goal_ref.endswith(expected_suffix)
        ):
            raise LongTermGoalContractError("goal_ref is not version-bound")
        object.__setattr__(self, "goal_ref", goal_ref)
        constraints = tuple(self.constraints)
        if status == "ACTIVE" and not constraints:
            raise LongTermGoalContractError(
                "active long-term goal requires constraints"
            )
        if status == "REVOKED" and constraints:
            raise LongTermGoalContractError(
                "revoked long-term goal cannot remain constraining"
            )
        if (
            len(constraints) > 16
            or any(
                type(item) is not ContextConstraintV1
                or item.origin != "LONG_TERM_GOAL"
                or item.source_ref != goal_ref
                for item in constraints
            )
        ):
            raise LongTermGoalContractError("constraints are not goal-bound")
        object.__setattr__(
            self,
            "constraints",
            tuple(sorted(constraints, key=lambda item: item.constraint_digest)),
        )
        object.__setattr__(
            self,
            "operation_id",
            _text(self.operation_id, "operation_id", 512),
        )
        digest = str(self.event_digest or "").strip().lower()
        if _DIGEST_RE.fullmatch(digest) is None:
            raise LongTermGoalContractError("event_digest is invalid")
        object.__setattr__(self, "event_digest", digest)
        if (
            isinstance(self.updated_at_ms, bool)
            or type(self.updated_at_ms) is not int
            or self.updated_at_ms < 0
        ):
            raise LongTermGoalContractError("updated_at_ms is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.long-term-goal.snapshot.v1",
            "runtime_scope_id": self.runtime_scope_id,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "goal_id": self.goal_id,
            "version": self.version,
            "status": self.status,
            "goal_ref": self.goal_ref,
            "constraints": [item.to_dict() for item in self.constraints],
            "operation_id": self.operation_id,
            "event_digest": self.event_digest,
            "updated_at_ms": self.updated_at_ms,
        }


__all__ = [
    "LongTermGoalConstraintSpecV1",
    "LongTermGoalContractError",
    "LongTermGoalSnapshotV1",
    "normalize_long_term_goal_constraint_specs",
]
