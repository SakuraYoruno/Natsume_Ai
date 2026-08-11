"""Public attempt-bound child budget contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .budget_policy import BudgetAmount
from .contract_errors import (
    BUDGET_RESERVATION_INVALID,
    ContractViolation,
    require_version,
)


NODE_BUDGET_RESERVATION_SCHEMA_VERSION = (
    "Natsume.task_runtime.node_budget_reservation.v1"
)


class NodeBudgetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"
    EXCEEDED = "EXCEEDED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


@dataclass(frozen=True, slots=True)
class NodeBudgetReservationV1:
    node_budget_id: str
    parent_budget_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    scope: str
    limits: BudgetAmount
    reserved: BudgetAmount
    consumed: BudgetAmount
    attempted_consumed: BudgetAmount
    usage_version: int
    parent_version_at_claim: int
    claim_origin: str
    attempt_status: str
    status: NodeBudgetStatus
    schema_version: str = NODE_BUDGET_RESERVATION_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "NodeBudgetReservationV1":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=NODE_BUDGET_RESERVATION_SCHEMA_VERSION,
            contract_name="NodeBudgetReservationV1",
        )
        try:
            status = NodeBudgetStatus(
                str(data.get("status") or "").strip().upper()
            )
        except ValueError as exc:
            raise ContractViolation(
                BUDGET_RESERVATION_INVALID,
                "NodeBudgetReservationV1.status is invalid",
            ) from exc
        reservation = cls(
            node_budget_id=str(data.get("node_budget_id") or "").strip(),
            parent_budget_id=str(data.get("parent_budget_id") or "").strip(),
            graph_id=str(data.get("graph_id") or "").strip(),
            graph_revision=_non_negative_int(
                data.get("graph_revision"),
                "graph_revision",
                minimum=1,
            ),
            node_id=str(data.get("node_id") or "").strip(),
            attempt_id=str(data.get("attempt_id") or "").strip(),
            scope=str(data.get("scope") or "").strip(),
            limits=BudgetAmount.from_mapping(
                data.get("limits")
                if isinstance(data.get("limits"), Mapping)
                else {}
            ),
            reserved=BudgetAmount.from_mapping(
                data.get("reserved")
                if isinstance(data.get("reserved"), Mapping)
                else {}
            ),
            consumed=BudgetAmount.from_mapping(
                data.get("consumed")
                if isinstance(data.get("consumed"), Mapping)
                else {}
            ),
            attempted_consumed=BudgetAmount.from_mapping(
                data.get("attempted_consumed")
                if isinstance(data.get("attempted_consumed"), Mapping)
                else {}
            ),
            usage_version=_non_negative_int(
                data.get("usage_version"),
                "usage_version",
            ),
            parent_version_at_claim=_non_negative_int(
                data.get("parent_version_at_claim"),
                "parent_version_at_claim",
            ),
            claim_origin=str(data.get("claim_origin") or "").strip(),
            attempt_status=str(data.get("attempt_status") or "").strip(),
            status=status,
        )
        reservation.validate()
        return reservation

    def validate(self) -> None:
        if not all(
            (
                self.node_budget_id,
                self.parent_budget_id,
                self.graph_id,
                self.node_id,
                self.attempt_id,
                self.scope,
                self.claim_origin,
                self.attempt_status,
            )
        ):
            raise ContractViolation(
                BUDGET_RESERVATION_INVALID,
                "NodeBudgetReservationV1 identity and authority are required",
            )
        if self.reserved.exceeds(self.limits):
            raise ContractViolation(
                BUDGET_RESERVATION_INVALID,
                "node reserved budget exceeds its parent-derived limit",
            )
        if self.consumed.exceeds(self.reserved):
            raise ContractViolation(
                BUDGET_RESERVATION_INVALID,
                "node consumed budget exceeds its reserved envelope",
            )
        if any(
            self.consumed.to_dict()[key] > self.attempted_consumed.to_dict()[key]
            for key in self.consumed.to_dict()
        ):
            raise ContractViolation(
                BUDGET_RESERVATION_INVALID,
                "node attempted usage is below accepted consumption",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "node_budget_id": self.node_budget_id,
            "parent_budget_id": self.parent_budget_id,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "scope": self.scope,
            "limits": self.limits.to_dict(),
            "reserved": self.reserved.to_dict(),
            "consumed": self.consumed.to_dict(),
            "attempted_consumed": self.attempted_consumed.to_dict(),
            "remaining": {
                key: max(
                    0,
                    self.reserved.to_dict()[key]
                    - self.consumed.to_dict()[key],
                )
                for key in self.reserved.to_dict()
            },
            "usage_version": self.usage_version,
            "parent_version_at_claim": self.parent_version_at_claim,
            "claim_origin": self.claim_origin,
            "attempt_status": self.attempt_status,
            "status": self.status.value,
        }


def _non_negative_int(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool):
        raise ContractViolation(
            BUDGET_RESERVATION_INVALID,
            f"{field_name} must be an integer",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(
            BUDGET_RESERVATION_INVALID,
            f"{field_name} must be an integer",
        ) from exc
    if parsed < minimum:
        raise ContractViolation(
            BUDGET_RESERVATION_INVALID,
            f"{field_name} must be at least {minimum}",
        )
    return parsed


__all__ = [
    "NODE_BUDGET_RESERVATION_SCHEMA_VERSION",
    "NodeBudgetReservationV1",
    "NodeBudgetStatus",
]
