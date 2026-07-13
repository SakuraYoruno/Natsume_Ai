"""Stable validation errors for canonical Agent runtime contracts."""

from __future__ import annotations


CONTRACT_FIELD_INVALID = "CONTRACT_FIELD_INVALID"
CONTRACT_VERSION_REQUIRED = "CONTRACT_VERSION_REQUIRED"
CONTRACT_VERSION_UNSUPPORTED = "CONTRACT_VERSION_UNSUPPORTED"
TASK_GRAPH_INVALID = "TASK_GRAPH_INVALID"
TASK_TRANSITION_INVALID = "TASK_TRANSITION_INVALID"
PLAN_CYCLE = "PLAN_CYCLE"
PLANNER_NEEDS_INPUT = "PLANNER_NEEDS_INPUT"
VERIFICATION_RESULT_INVALID = "VERIFICATION_RESULT_INVALID"
REVIEW_ITEM_INVALID = "REVIEW_ITEM_INVALID"
REVIEW_TRANSITION_INVALID = "REVIEW_TRANSITION_INVALID"
BUDGET_POLICY_INVALID = "BUDGET_POLICY_INVALID"
BUDGET_RESERVATION_INVALID = "BUDGET_RESERVATION_INVALID"
BUDGET_TRANSITION_INVALID = "BUDGET_TRANSITION_INVALID"
ACCEPTANCE_JUDGEMENT_INVALID = "ACCEPTANCE_JUDGEMENT_INVALID"
SUB_AGENT_ADAPTER_INVALID = "SUB_AGENT_ADAPTER_INVALID"
SUB_AGENT_LINEAGE_INVALID = "SUB_AGENT_LINEAGE_INVALID"


class ContractViolation(ValueError):
    """A fail-closed contract error with a stable machine-readable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CONTRACT_FIELD_INVALID)
        self.detail = str(detail or "contract validation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def require_version(
    value: object,
    *,
    expected: str,
    contract_name: str,
) -> str:
    """Return a normalized version or reject missing/unknown versions."""

    version = str(value or "").strip()
    if not version:
        raise ContractViolation(
            CONTRACT_VERSION_REQUIRED,
            f"{contract_name}.schema_version is required",
        )
    if version != expected:
        raise ContractViolation(
            CONTRACT_VERSION_UNSUPPORTED,
            f"unsupported {contract_name}.schema_version: {version}",
        )
    return version


__all__ = [
    "BUDGET_POLICY_INVALID",
    "BUDGET_RESERVATION_INVALID",
    "BUDGET_TRANSITION_INVALID",
    "ACCEPTANCE_JUDGEMENT_INVALID",
    "CONTRACT_FIELD_INVALID",
    "CONTRACT_VERSION_REQUIRED",
    "CONTRACT_VERSION_UNSUPPORTED",
    "ContractViolation",
    "PLAN_CYCLE",
    "PLANNER_NEEDS_INPUT",
    "REVIEW_ITEM_INVALID",
    "REVIEW_TRANSITION_INVALID",
    "SUB_AGENT_ADAPTER_INVALID",
    "SUB_AGENT_LINEAGE_INVALID",
    "TASK_GRAPH_INVALID",
    "TASK_TRANSITION_INVALID",
    "VERIFICATION_RESULT_INVALID",
    "require_version",
]
