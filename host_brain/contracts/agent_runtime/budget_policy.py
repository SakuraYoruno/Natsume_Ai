"""Pure budget policy and reservation contracts for the canonical task runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.contract_errors import (
    BUDGET_POLICY_INVALID,
    BUDGET_RESERVATION_INVALID,
    BUDGET_TRANSITION_INVALID,
    ContractViolation,
    require_version,
)


BUDGET_POLICY_SCHEMA_VERSION = "Natsume.agent.budget_policy.v1"
BUDGET_RESERVATION_SCHEMA_VERSION = "Natsume.agent.budget_reservation.v1"


class BudgetExceededAction(StrEnum):
    BLOCK = "BLOCK"
    DEGRADE = "DEGRADE"
    REVIEW = "REVIEW"


class BudgetReservationStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXCEEDED = "EXCEEDED"
    CANCELLED = "CANCELLED"


def _non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(BUDGET_POLICY_INVALID, f"{field_name} must be an integer") from exc
    if parsed < 0:
        raise ContractViolation(BUDGET_POLICY_INVALID, f"{field_name} must be non-negative")
    return parsed


@dataclass(frozen=True, slots=True)
class BudgetAmount:
    token_count: int = 0
    tool_call_count: int = 0
    time_ms: int = 0
    concurrency: int = 0
    retry_count: int = 0
    model_cost_micros: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "BudgetAmount":
        data = dict(payload or {})
        return cls(
            token_count=_non_negative_int(data.get("token_count"), field_name="token_count"),
            tool_call_count=_non_negative_int(data.get("tool_call_count"), field_name="tool_call_count"),
            time_ms=_non_negative_int(data.get("time_ms"), field_name="time_ms"),
            concurrency=_non_negative_int(data.get("concurrency"), field_name="concurrency"),
            retry_count=_non_negative_int(data.get("retry_count"), field_name="retry_count"),
            model_cost_micros=_non_negative_int(
                data.get("model_cost_micros"),
                field_name="model_cost_micros",
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "token_count": self.token_count,
            "tool_call_count": self.tool_call_count,
            "time_ms": self.time_ms,
            "concurrency": self.concurrency,
            "retry_count": self.retry_count,
            "model_cost_micros": self.model_cost_micros,
        }

    def exceeds(self, limit: "BudgetAmount") -> bool:
        return any(
            value > ceiling
            for value, ceiling in zip(self.to_dict().values(), limit.to_dict().values(), strict=True)
        )


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    scope: str
    limits: BudgetAmount
    on_exceeded: BudgetExceededAction = BudgetExceededAction.BLOCK
    schema_version: str = BUDGET_POLICY_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BudgetPolicy":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=BUDGET_POLICY_SCHEMA_VERSION,
            contract_name="BudgetPolicy",
        )
        try:
            on_exceeded = BudgetExceededAction(str(data.get("on_exceeded") or "BLOCK").strip().upper())
        except ValueError as exc:
            raise ContractViolation(
                BUDGET_POLICY_INVALID,
                f"unsupported on_exceeded action: {data.get('on_exceeded')}",
            ) from exc
        policy = cls(
            scope=str(data.get("scope") or "").strip(),
            limits=BudgetAmount.from_mapping(data.get("limits") if isinstance(data.get("limits"), Mapping) else {}),
            on_exceeded=on_exceeded,
            metadata=dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), Mapping) else {},
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.schema_version != BUDGET_POLICY_SCHEMA_VERSION:
            raise ContractViolation(BUDGET_POLICY_INVALID, "BudgetPolicy schema version is invalid")
        if not self.scope:
            raise ContractViolation(BUDGET_POLICY_INVALID, "BudgetPolicy.scope is required")
        if not isinstance(self.on_exceeded, BudgetExceededAction):
            raise ContractViolation(
                BUDGET_POLICY_INVALID,
                "BudgetPolicy.on_exceeded must be a BudgetExceededAction",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope,
            "limits": self.limits.to_dict(),
            "on_exceeded": self.on_exceeded.value,
            "metadata": dict(self.metadata),
        }


_BUDGET_TRANSITIONS: dict[BudgetReservationStatus, frozenset[BudgetReservationStatus]] = {
    BudgetReservationStatus.PENDING: frozenset(
        {BudgetReservationStatus.ACTIVE, BudgetReservationStatus.CANCELLED}
    ),
    BudgetReservationStatus.ACTIVE: frozenset(
        {
            BudgetReservationStatus.CONSUMED,
            BudgetReservationStatus.RELEASED,
            BudgetReservationStatus.EXCEEDED,
            BudgetReservationStatus.CANCELLED,
        }
    ),
    BudgetReservationStatus.CONSUMED: frozenset(),
    BudgetReservationStatus.RELEASED: frozenset(),
    BudgetReservationStatus.EXCEEDED: frozenset({BudgetReservationStatus.RELEASED}),
    BudgetReservationStatus.CANCELLED: frozenset(),
}


def validate_budget_transition(
    current: BudgetReservationStatus | str,
    target: BudgetReservationStatus | str,
) -> BudgetReservationStatus:
    try:
        current_status = BudgetReservationStatus(str(current))
        target_status = BudgetReservationStatus(str(target))
    except ValueError as exc:
        raise ContractViolation(BUDGET_TRANSITION_INVALID, "unknown budget reservation status") from exc
    if target_status not in _BUDGET_TRANSITIONS[current_status]:
        raise ContractViolation(
            BUDGET_TRANSITION_INVALID,
            f"illegal BudgetReservation transition: {current_status.value}->{target_status.value}",
        )
    return target_status


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    budget_id: str
    scope: str
    limits: BudgetAmount
    reserved: BudgetAmount
    consumed: BudgetAmount = field(default_factory=BudgetAmount)
    status: BudgetReservationStatus = BudgetReservationStatus.PENDING
    status_version: int = 0
    schema_version: str = BUDGET_RESERVATION_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "BudgetReservation":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=BUDGET_RESERVATION_SCHEMA_VERSION,
            contract_name="BudgetReservation",
        )
        try:
            status = BudgetReservationStatus(str(data.get("status") or "PENDING").strip().upper())
        except ValueError as exc:
            raise ContractViolation(
                BUDGET_RESERVATION_INVALID,
                f"unsupported BudgetReservation.status: {data.get('status')}",
            ) from exc
        reservation = cls(
            budget_id=str(data.get("budget_id") or "").strip(),
            scope=str(data.get("scope") or "").strip(),
            limits=BudgetAmount.from_mapping(data.get("limits") if isinstance(data.get("limits"), Mapping) else {}),
            reserved=BudgetAmount.from_mapping(data.get("reserved") if isinstance(data.get("reserved"), Mapping) else {}),
            consumed=BudgetAmount.from_mapping(data.get("consumed") if isinstance(data.get("consumed"), Mapping) else {}),
            status=status,
            status_version=_non_negative_int(data.get("status_version"), field_name="status_version"),
        )
        reservation.validate()
        return reservation

    def validate(self) -> None:
        if self.schema_version != BUDGET_RESERVATION_SCHEMA_VERSION:
            raise ContractViolation(BUDGET_RESERVATION_INVALID, "BudgetReservation schema version is invalid")
        if not self.budget_id or not self.scope:
            raise ContractViolation(BUDGET_RESERVATION_INVALID, "budget_id and scope are required")
        if self.reserved.exceeds(self.limits):
            raise ContractViolation(BUDGET_RESERVATION_INVALID, "reserved budget exceeds limits")
        if self.consumed.exceeds(self.reserved):
            raise ContractViolation(BUDGET_RESERVATION_INVALID, "consumed budget exceeds reservation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "budget_id": self.budget_id,
            "scope": self.scope,
            "limits": self.limits.to_dict(),
            "reserved": self.reserved.to_dict(),
            "consumed": self.consumed.to_dict(),
            "status": self.status.value,
            "status_version": self.status_version,
        }


__all__ = [
    "BUDGET_POLICY_SCHEMA_VERSION",
    "BUDGET_RESERVATION_SCHEMA_VERSION",
    "BudgetAmount",
    "BudgetExceededAction",
    "BudgetPolicy",
    "BudgetReservation",
    "BudgetReservationStatus",
    "validate_budget_transition",
]
