"""Shared errors for governed Operator command runtimes."""

from __future__ import annotations


GOVERNED_OPERATOR_RUNTIME_INVALID = "GOVERNED_OPERATOR_RUNTIME_INVALID"
GOVERNED_OPERATOR_GOVERNOR_REJECTED = "GOVERNED_OPERATOR_GOVERNOR_REJECTED"


class GovernedOperatorRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_OPERATOR_RUNTIME_INVALID)
        self.detail = str(detail or "governed Operator command is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


__all__ = [
    "GOVERNED_OPERATOR_GOVERNOR_REJECTED",
    "GOVERNED_OPERATOR_RUNTIME_INVALID",
    "GovernedOperatorRuntimeError",
]
