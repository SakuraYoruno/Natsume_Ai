"""Pure verification result and state-transition contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    VERIFICATION_RESULT_INVALID,
    ContractViolation,
    require_version,
)


VERIFICATION_RESULT_SCHEMA_VERSION = "Natsume.agent.verification_result.v1"
VERIFICATION_EVIDENCE_REQUIRED = "VERIFICATION_EVIDENCE_REQUIRED"
VERIFICATION_PLAN_INVALID = "VERIFICATION_PLAN_INVALID"
VERIFICATION_REQUIRED_NOT_PASSED = "VERIFICATION_REQUIRED_NOT_PASSED"
_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)


class VerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_id: str
    work_order_id: str
    required: bool
    status: VerificationStatus
    task_graph_id: str | None = None
    task_node_id: str | None = None
    attempt_id: str | None = None
    reason_code: str | None = None
    evidence_refs: tuple[str, ...] = ()
    checks: tuple[dict[str, Any], ...] = ()
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    schema_version: str = VERIFICATION_RESULT_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "VerificationResult":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=VERIFICATION_RESULT_SCHEMA_VERSION,
            contract_name="VerificationResult",
        )

        def optional_text(key: str) -> str | None:
            value = data.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be a string")
            return value.strip() or None

        def required_text(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str):
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be a string")
            return value.strip()

        def optional_int(key: str) -> int | None:
            value = data.get(key)
            if value is None:
                return None
            if type(value) is not int or value < 0:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be a non-negative integer")
            return value

        try:
            status = VerificationStatus(str(data.get("status") or "").strip().upper())
        except ValueError as exc:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                f"unsupported VerificationResult.status: {data.get('status')}",
            ) from exc
        required = data.get("required", False)
        evidence = data.get("evidence_refs")
        checks = data.get("checks")
        if type(required) is not bool:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "required must be a boolean")
        if not isinstance(evidence, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "evidence_refs must be a list")
        if not isinstance(checks, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "checks must be a list")
        result = cls(
            verification_id=required_text("verification_id"),
            work_order_id=required_text("work_order_id"),
            required=required,
            status=status,
            task_graph_id=optional_text("task_graph_id"),
            task_node_id=optional_text("task_node_id"),
            attempt_id=optional_text("attempt_id"),
            reason_code=optional_text("reason_code"),
            evidence_refs=tuple(item.strip() for item in evidence if isinstance(item, str) and item.strip()),
            checks=tuple(dict(item) for item in checks if isinstance(item, Mapping)),
            started_at_ms=optional_int("started_at_ms"),
            finished_at_ms=optional_int("finished_at_ms"),
        )
        if len(result.evidence_refs) != len(evidence):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "evidence_refs must contain only non-empty strings")
        if len(result.checks) != len(checks):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "checks must contain only objects")
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != VERIFICATION_RESULT_SCHEMA_VERSION:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "VerificationResult schema version is invalid")
        if not self.verification_id or not self.work_order_id:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "verification_id and work_order_id are required")
        if self.status is not VerificationStatus.PASS and not self.reason_code:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "non-PASS VerificationResult requires a stable reason_code")
        if self.status is VerificationStatus.PASS and self.reason_code:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "PASS VerificationResult cannot carry a failure reason_code")
        if bool(self.task_graph_id) != bool(self.task_node_id):
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "task_graph_id and task_node_id must be set together")
        if any(not _DURABLE_REFERENCE_PATTERN.fullmatch(ref) for ref in self.evidence_refs):
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "evidence_refs must contain only bounded durable references")
        if self.started_at_ms is not None and self.finished_at_ms is not None and self.finished_at_ms < self.started_at_ms:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "finished_at_ms cannot precede started_at_ms")

    @property
    def allows_verified_transition(self) -> bool:
        return self.status is VerificationStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "verification_id": self.verification_id,
            "work_order_id": self.work_order_id,
            "task_graph_id": self.task_graph_id,
            "task_node_id": self.task_node_id,
            "attempt_id": self.attempt_id,
            "required": self.required,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "checks": [dict(item) for item in self.checks],
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
        }


class VerificationGate:
    @staticmethod
    def required_plan_not_passed(
        plan: Mapping[str, Any],
        result: Mapping[str, Any] | VerificationResult,
    ) -> bool:
        required = plan.get("required", False)
        if type(required) is not bool:
            return True
        if not required:
            return False
        try:
            verification = result if isinstance(result, VerificationResult) else VerificationResult.from_mapping(result)
        except ContractViolation:
            return True
        return verification.status is not VerificationStatus.PASS

    @staticmethod
    def validate_node_transition(
        result: Mapping[str, Any] | VerificationResult | None,
        *,
        target_status: str,
        graph_id: str,
        node_id: str,
        root_work_order_id: str,
        outbox_evidence_refs: tuple[str, ...] = (),
    ) -> VerificationResult | None:
        if target_status not in {VerificationStatus.PASS.value, "VERIFIED", "VERIFICATION_FAILED"}:
            return None
        if target_status == VerificationStatus.PASS.value:
            target_status = "VERIFIED"
        if result is None:
            raise ContractViolation(VERIFICATION_REQUIRED_NOT_PASSED, f"{target_status} transition requires a VerificationResult")
        verification = result if isinstance(result, VerificationResult) else VerificationResult.from_mapping(result)
        if verification.work_order_id != root_work_order_id:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "VerificationResult.work_order_id must match TaskGraph.root_work_order_id")
        if verification.task_graph_id != graph_id or verification.task_node_id != node_id:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "VerificationResult task graph/node identity must match the state transition")
        if not verification.evidence_refs:
            raise ContractViolation(VERIFICATION_EVIDENCE_REQUIRED, "verification state transitions require durable evidence refs")
        if set(verification.evidence_refs) - set(outbox_evidence_refs):
            raise ContractViolation(VERIFICATION_EVIDENCE_REQUIRED, "outbox evidence_refs must include VerificationResult evidence refs")
        if target_status == "VERIFIED" and verification.status is not VerificationStatus.PASS:
            raise ContractViolation(VERIFICATION_REQUIRED_NOT_PASSED, f"VERIFIED transition requires PASS verification, got {verification.status.value}")
        if target_status == "VERIFICATION_FAILED" and verification.status is VerificationStatus.PASS:
            raise ContractViolation(VERIFICATION_RESULT_INVALID, "VERIFICATION_FAILED transition cannot use PASS verification")
        return verification


__all__ = [
    "VERIFICATION_EVIDENCE_REQUIRED",
    "VERIFICATION_PLAN_INVALID",
    "VERIFICATION_REQUIRED_NOT_PASSED",
    "VERIFICATION_RESULT_SCHEMA_VERSION",
    "VerificationGate",
    "VerificationResult",
    "VerificationStatus",
]
