"""Verification contract plus the unchanged Agent runtime v2 policy helper."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import subprocess
from pathlib import Path
from typing import Any, Mapping

from host_brain.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    VERIFICATION_RESULT_INVALID,
    ContractViolation,
    require_version,
)


VERIFICATION_RESULT_SCHEMA_VERSION = "Natsume.agent.verification_result.v1"


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
        try:
            status = VerificationStatus(str(data.get("status") or "").strip().upper())
        except ValueError as exc:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                f"unsupported VerificationResult.status: {data.get('status')}",
            ) from exc

        def optional_text(key: str) -> str | None:
            value = str(data.get(key) or "").strip()
            return value or None

        def optional_int(key: str) -> int | None:
            value = data.get(key)
            if value is None:
                return None
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be an integer") from exc
            if parsed < 0:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be non-negative")
            return parsed

        evidence_raw = data.get("evidence_refs")
        checks_raw = data.get("checks")
        result = cls(
            verification_id=str(data.get("verification_id") or "").strip(),
            work_order_id=str(data.get("work_order_id") or "").strip(),
            required=bool(data.get("required", False)),
            status=status,
            task_graph_id=optional_text("task_graph_id"),
            task_node_id=optional_text("task_node_id"),
            attempt_id=optional_text("attempt_id"),
            reason_code=optional_text("reason_code"),
            evidence_refs=tuple(
                str(item).strip()
                for item in (evidence_raw if isinstance(evidence_raw, list) else [])
                if str(item).strip()
            ),
            checks=tuple(
                dict(item)
                for item in (checks_raw if isinstance(checks_raw, list) else [])
                if isinstance(item, Mapping)
            ),
            started_at_ms=optional_int("started_at_ms"),
            finished_at_ms=optional_int("finished_at_ms"),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != VERIFICATION_RESULT_SCHEMA_VERSION:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "VerificationResult schema version is invalid",
            )
        if not self.verification_id or not self.work_order_id:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "verification_id and work_order_id are required",
            )
        if self.status is not VerificationStatus.PASS and not self.reason_code:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "non-PASS VerificationResult requires a stable reason_code",
            )
        if bool(self.task_graph_id) != bool(self.task_node_id):
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "task_graph_id and task_node_id must be set together",
            )
        if (
            self.started_at_ms is not None
            and self.finished_at_ms is not None
            and self.finished_at_ms < self.started_at_ms
        ):
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "finished_at_ms cannot precede started_at_ms",
            )

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


class VerificationPolicy:
    def run(self, plan: dict[str, Any], *, workspace: Path, request_id: str | None, intent_id: str) -> dict[str, Any]:
        code = str(plan.get("verification_code") or "").strip()
        if code:
            from host_brain.airlock import SandboxExecutor  # noqa: PLC0415

            result = SandboxExecutor().execute(
                code,
                request_id=request_id,
                intent_id=intent_id,
                language="python",
            )
            return {
                "status": result.status,
                "reason_code": result.reason_code,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
                "evidence_ref": result.evidence_ref,
                "code_digest": result.code_digest,
            }

        commands = plan.get("commands")
        if isinstance(commands, list) and commands:
            rows: list[dict[str, Any]] = []
            overall = "PASS"
            for command in commands[:5]:
                if isinstance(command, str):
                    args = command.split()
                elif isinstance(command, list):
                    args = [str(part) for part in command if str(part).strip()]
                else:
                    continue
                if not args:
                    continue
                completed = subprocess.run(
                    args,
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=int(plan.get("timeout_seconds") or 120),
                    check=False,
                )
                if completed.returncode != 0:
                    overall = "FAIL"
                rows.append(
                    {
                        "command": args,
                        "exit_code": completed.returncode,
                        "stdout": str(completed.stdout or "")[:4000],
                        "stderr": str(completed.stderr or "")[:4000],
                    }
                )
            return {"status": overall if rows else "NOT_RUN", "commands": rows}

        return {"status": "NOT_RUN", "reason": "verification plan did not request executable checks"}


__all__ = [
    "VERIFICATION_RESULT_SCHEMA_VERSION",
    "VerificationPolicy",
    "VerificationResult",
    "VerificationStatus",
]
