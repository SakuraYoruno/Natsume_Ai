"""Immutable contracts for controlled Code benchmark verification commands.

The D19 verifier is intentionally narrow.  It can only launch a fixed,
repository-relative ``python -m pytest -q`` command from a disposable D17
worktree.  These contracts record command identity and bounded digests, never
host paths or command output.  They do not claim a container, OS, tenant,
filesystem or network sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any

from host_brain.core.runtime_helpers import canonical_json_sha256 as _digest


CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA = (
    "natsume.code-benchmark-controlled-verifier.v1"
)
CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE = (
    "CONTROLLED_COMMAND_TRUSTED_WORKSPACE_ONLY"
)
CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY = (
    "SEPARATE_SUBPROCESS_NO_OS_ISOLATION"
)
CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION = "NOT_ENFORCED"
CODE_BENCHMARK_CONTROLLED_VERIFIER_POLICY_REVISION = "code-benchmark-pytest-argv-v1"
CODE_BENCHMARK_CONTROLLED_BUDGET_SCOPE = (
    "PAIRED_RUN_TEST_COMMAND_AND_WALL_TIME_ONLY"
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TEST_PATH_RE = re.compile(
    r"^[a-z0-9_][a-z0-9_./-]{0,511}\.py(?:::[a-zA-Z_][a-zA-Z0-9_]{0,119})?$"
)


class CodeBenchmarkVerificationContractError(ValueError):
    """Raised when a controlled verification receipt loses its narrow boundary."""


@dataclass(frozen=True, slots=True)
class CodeBenchmarkVerificationCommand:
    """One frozen pytest argv vector; shell syntax is not representable."""

    command_id: str
    argv: Sequence[str]
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        command_id = _identifier(self.command_id, "command_id")
        argv = _pytest_argv(self.argv)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
            or self.timeout_seconds > 300
        ):
            raise CodeBenchmarkVerificationContractError(
                "timeout_seconds must be an integer between 1 and 300"
            )
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "argv", argv)
        object.__setattr__(self, "timeout_seconds", int(self.timeout_seconds))

    @property
    def command_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "argv": list(self.argv),
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CodeBenchmarkVerificationCommand":
        if not isinstance(payload, Mapping):
            raise CodeBenchmarkVerificationContractError(
                "verification command payload must be an object"
            )
        return cls(
            command_id=payload.get("command_id"),
            argv=payload.get("argv"),
            timeout_seconds=payload.get("timeout_seconds", 120),
        )


@dataclass(frozen=True, slots=True)
class CodeBenchmarkVerificationPlan:
    """Server-owned bounded pytest plan for both arms of one paired run."""

    commands: Sequence[CodeBenchmarkVerificationCommand]
    policy_revision: str = CODE_BENCHMARK_CONTROLLED_VERIFIER_POLICY_REVISION
    plan_ref: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.commands, (str, bytes, bytearray)) or not isinstance(
            self.commands, Sequence
        ):
            raise CodeBenchmarkVerificationContractError(
                "commands must be an explicit sequence"
            )
        commands = tuple(self.commands)
        if not commands or len(commands) > 5:
            raise CodeBenchmarkVerificationContractError(
                "controlled verification requires between 1 and 5 commands"
            )
        if not all(isinstance(item, CodeBenchmarkVerificationCommand) for item in commands):
            raise CodeBenchmarkVerificationContractError(
                "commands must contain CodeBenchmarkVerificationCommand values"
            )
        ids = [item.command_id for item in commands]
        if len(ids) != len(set(ids)):
            raise CodeBenchmarkVerificationContractError("command ids must be unique")
        revision = _identifier(self.policy_revision, "policy_revision")
        identity = {
            "schema": CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA,
            "commands": [item.to_dict() for item in commands],
            "policy_revision": revision,
            "mode": CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE,
            "process_boundary": CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY,
            "network_isolation": CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION,
        }
        digest = _digest(identity)
        expected_ref = f"code-verification-plan://sha256/{digest}"
        supplied_ref = str(self.plan_ref or "").strip()
        if supplied_ref and supplied_ref != expected_ref:
            raise CodeBenchmarkVerificationContractError(
                "plan_ref does not bind the controlled verification plan"
            )
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "policy_revision", revision)
        object.__setattr__(self, "plan_ref", expected_ref)

    @property
    def plan_digest(self) -> str:
        return self.plan_ref.removeprefix("code-verification-plan://sha256/")

    @property
    def test_command_count_per_arm(self) -> int:
        return len(self.commands)

    @property
    def max_wall_time_ms_per_arm(self) -> int:
        return sum(command.timeout_seconds * 1_000 for command in self.commands)

    def paired_budget_requirements(self) -> dict[str, int]:
        """Return conservative two-arm upper bounds for registration checks."""

        return {
            "max_test_commands": self.test_command_count_per_arm * 2,
            "max_wall_time_ms": self.max_wall_time_ms_per_arm * 2,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA,
            "commands": [item.to_dict() for item in self.commands],
            "policy_revision": self.policy_revision,
            "mode": CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE,
            "process_boundary": CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY,
            "network_isolation": CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION,
            "plan_ref": self.plan_ref,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CodeBenchmarkVerificationPlan":
        if not isinstance(payload, Mapping):
            raise CodeBenchmarkVerificationContractError(
                "verification plan payload must be an object"
            )
        if payload.get("schema") != CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA:
            raise CodeBenchmarkVerificationContractError(
                "verification plan schema is invalid"
            )
        if (
            payload.get("mode") != CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE
            or payload.get("process_boundary")
            != CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY
            or payload.get("network_isolation")
            != CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION
        ):
            raise CodeBenchmarkVerificationContractError(
                "verification plan boundary fields are invalid"
            )
        return cls(
            commands=tuple(
                CodeBenchmarkVerificationCommand.from_dict(item)
                for item in _objects(payload.get("commands"), "commands")
            ),
            policy_revision=payload.get("policy_revision"),
            plan_ref=payload.get("plan_ref", ""),
        )


@dataclass(frozen=True, slots=True)
class CodeBenchmarkControlledVerificationBudget:
    """The only D19 hard gate: paired test-command count and wall time.

    This deliberately has no model, provider, tool, token or cost field.  It
    records only the two measurements that the controlled verifier itself can
    observe and reject.
    """

    max_test_commands: int
    max_wall_time_ms: int
    scope: str = CODE_BENCHMARK_CONTROLLED_BUDGET_SCOPE

    def __post_init__(self) -> None:
        for field_name in ("max_test_commands", "max_wall_time_ms"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise CodeBenchmarkVerificationContractError(
                    f"{field_name} must be a positive integer"
                )
            object.__setattr__(self, field_name, int(value))
        if str(self.scope or "").strip().upper() != CODE_BENCHMARK_CONTROLLED_BUDGET_SCOPE:
            raise CodeBenchmarkVerificationContractError(
                "controlled verifier budget has an invalid scope"
            )
        object.__setattr__(self, "scope", CODE_BENCHMARK_CONTROLLED_BUDGET_SCOPE)

    @property
    def budget_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_test_commands": self.max_test_commands,
            "max_wall_time_ms": self.max_wall_time_ms,
            "scope": CODE_BENCHMARK_CONTROLLED_BUDGET_SCOPE,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CodeBenchmarkControlledVerificationBudget":
        if not isinstance(payload, Mapping):
            raise CodeBenchmarkVerificationContractError(
                "controlled verifier budget payload must be an object"
            )
        return cls(
            max_test_commands=payload.get("max_test_commands"),
            max_wall_time_ms=payload.get("max_wall_time_ms"),
            scope=payload.get("scope"),
        )


@dataclass(frozen=True, slots=True)
class CodeBenchmarkVerificationCommandReceipt:
    """Bounded observation for one executed frozen command."""

    command_id: str
    command_digest: str
    executed_argv_digest: str
    status: str
    exit_code: int
    stdout_digest: str
    stderr_digest: str
    stdout_length: int
    stderr_length: int
    duration_ms: int

    def __post_init__(self) -> None:
        command_id = _identifier(self.command_id, "command_id")
        for field_name in (
            "command_digest",
            "executed_argv_digest",
            "stdout_digest",
            "stderr_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                _digest_text(getattr(self, field_name), field_name),
            )
        status = str(self.status or "").strip().upper()
        if status not in {"PASS", "FAIL"}:
            raise CodeBenchmarkVerificationContractError(
                "controlled command receipt status must be PASS or FAIL"
            )
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise CodeBenchmarkVerificationContractError("exit_code must be an integer")
        if (status == "PASS") != (self.exit_code == 0):
            raise CodeBenchmarkVerificationContractError(
                "controlled command receipt status must match its exit code"
            )
        for field_name in ("stdout_length", "stderr_length", "duration_ms"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CodeBenchmarkVerificationContractError(
                    f"{field_name} must be a non-negative integer"
                )
            object.__setattr__(self, field_name, int(value))
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "exit_code", int(self.exit_code))

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_digest": self.command_digest,
            "executed_argv_digest": self.executed_argv_digest,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout_digest": self.stdout_digest,
            "stderr_digest": self.stderr_digest,
            "stdout_length": self.stdout_length,
            "stderr_length": self.stderr_length,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CodeBenchmarkVerificationCommandReceipt":
        if not isinstance(payload, Mapping):
            raise CodeBenchmarkVerificationContractError(
                "controlled command receipt payload must be an object"
            )
        return cls(
            command_id=payload.get("command_id"),
            command_digest=payload.get("command_digest"),
            executed_argv_digest=payload.get("executed_argv_digest"),
            status=payload.get("status"),
            exit_code=payload.get("exit_code"),
            stdout_digest=payload.get("stdout_digest"),
            stderr_digest=payload.get("stderr_digest"),
            stdout_length=payload.get("stdout_length"),
            stderr_length=payload.get("stderr_length"),
            duration_ms=payload.get("duration_ms"),
        )


@dataclass(frozen=True, slots=True)
class CodeBenchmarkControlledVerificationReceipt:
    """Path-free D19 evidence for one subprocess verification of one arm."""

    run_id: str
    arm_id: str
    plan: CodeBenchmarkVerificationPlan
    budget: CodeBenchmarkControlledVerificationBudget
    commands: Sequence[CodeBenchmarkVerificationCommandReceipt]
    success: bool
    verification_wall_time_ms: int
    receipt_ref: str = ""

    def __post_init__(self) -> None:
        run_id = _identifier(self.run_id, "run_id")
        arm_id = _identifier(self.arm_id, "arm_id")
        if not isinstance(self.plan, CodeBenchmarkVerificationPlan):
            raise CodeBenchmarkVerificationContractError("plan is invalid")
        if not isinstance(self.budget, CodeBenchmarkControlledVerificationBudget):
            raise CodeBenchmarkVerificationContractError("budget is invalid")
        if isinstance(self.commands, (str, bytes, bytearray)) or not isinstance(
            self.commands, Sequence
        ):
            raise CodeBenchmarkVerificationContractError(
                "commands must be an explicit receipt sequence"
            )
        commands = tuple(self.commands)
        if len(commands) != len(self.plan.commands) or not all(
            isinstance(item, CodeBenchmarkVerificationCommandReceipt) for item in commands
        ):
            raise CodeBenchmarkVerificationContractError(
                "receipt must contain one bounded observation per planned command"
            )
        for planned, observed in zip(self.plan.commands, commands, strict=True):
            if (
                observed.command_id != planned.command_id
                or observed.command_digest != planned.command_digest
                or observed.executed_argv_digest != _digest(list(planned.argv))
            ):
                raise CodeBenchmarkVerificationContractError(
                    "command receipt does not bind the registered argv command"
                )
        if not isinstance(self.success, bool):
            raise CodeBenchmarkVerificationContractError("success must be boolean")
        expected_success = all(item.status == "PASS" for item in commands)
        if self.success != expected_success:
            raise CodeBenchmarkVerificationContractError(
                "controlled receipt success must equal all command outcomes"
            )
        if (
            isinstance(self.verification_wall_time_ms, bool)
            or not isinstance(self.verification_wall_time_ms, int)
            or self.verification_wall_time_ms < 0
            or self.verification_wall_time_ms > self.budget.max_wall_time_ms
        ):
            raise CodeBenchmarkVerificationContractError(
                "verification_wall_time_ms exceeds the controlled budget"
            )
        identity = {
            "schema": CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA,
            "run_id": run_id,
            "arm_id": arm_id,
            "plan": self.plan.to_dict(),
            "budget": self.budget.to_dict(),
            "commands": [item.to_dict() for item in commands],
            "success": self.success,
            "verification_wall_time_ms": int(self.verification_wall_time_ms),
            "mode": CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE,
            "process_boundary": CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY,
            "network_isolation": CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION,
        }
        digest = _digest(identity)
        expected_ref = f"code-verification://sha256/{digest}"
        supplied_ref = str(self.receipt_ref or "").strip()
        if supplied_ref and supplied_ref != expected_ref:
            raise CodeBenchmarkVerificationContractError(
                "receipt_ref does not bind controlled verification evidence"
            )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "arm_id", arm_id)
        object.__setattr__(self, "commands", commands)
        object.__setattr__(self, "verification_wall_time_ms", int(self.verification_wall_time_ms))
        object.__setattr__(self, "receipt_ref", expected_ref)

    @property
    def evidence_digest(self) -> str:
        return self.receipt_ref.removeprefix("code-verification://sha256/")

    @property
    def cost(self) -> Mapping[str, int]:
        return MappingProxyType(
            {
                "test_command_count": len(self.commands),
                "verification_wall_time_ms": self.verification_wall_time_ms,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA,
            "run_id": self.run_id,
            "arm_id": self.arm_id,
            "plan": self.plan.to_dict(),
            "budget": self.budget.to_dict(),
            "commands": [item.to_dict() for item in self.commands],
            "success": self.success,
            "verification_wall_time_ms": self.verification_wall_time_ms,
            "mode": CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE,
            "process_boundary": CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY,
            "network_isolation": CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION,
            "receipt_ref": self.receipt_ref,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "CodeBenchmarkControlledVerificationReceipt":
        if not isinstance(payload, Mapping):
            raise CodeBenchmarkVerificationContractError(
                "controlled verification receipt payload must be an object"
            )
        if payload.get("schema") != CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA:
            raise CodeBenchmarkVerificationContractError(
                "controlled verification receipt schema is invalid"
            )
        if (
            payload.get("mode") != CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE
            or payload.get("process_boundary")
            != CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY
            or payload.get("network_isolation")
            != CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION
        ):
            raise CodeBenchmarkVerificationContractError(
                "controlled verification receipt boundary fields are invalid"
            )
        return cls(
            run_id=payload.get("run_id"),
            arm_id=payload.get("arm_id"),
            plan=CodeBenchmarkVerificationPlan.from_dict(payload.get("plan")),
            budget=CodeBenchmarkControlledVerificationBudget.from_dict(payload.get("budget")),
            commands=tuple(
                CodeBenchmarkVerificationCommandReceipt.from_dict(item)
                for item in _objects(payload.get("commands"), "commands")
            ),
            success=payload.get("success"),
            verification_wall_time_ms=payload.get("verification_wall_time_ms"),
            receipt_ref=payload.get("receipt_ref", ""),
        )


def _pytest_argv(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise CodeBenchmarkVerificationContractError(
            "argv must be an explicit argument sequence, never a shell string"
        )
    argv: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, str) or not raw or raw != raw.strip() or "\x00" in raw:
            raise CodeBenchmarkVerificationContractError(
                f"argv[{index}] must be non-empty, trimmed text without NUL"
            )
        if raw.startswith("@"):
            raise CodeBenchmarkVerificationContractError(
                "response-file arguments are forbidden"
            )
        argv.append(raw)
    if len(argv) < 5 or tuple(argv[:4]) != ("python", "-m", "pytest", "-q"):
        raise CodeBenchmarkVerificationContractError(
            "controlled verification only permits 'python -m pytest -q <target...>'"
        )
    for target in argv[4:]:
        _test_target(target)
    return tuple(argv)


def _test_target(value: str) -> str:
    if not _TEST_PATH_RE.fullmatch(value):
        raise CodeBenchmarkVerificationContractError(
            "pytest target must be a bounded repository-relative test path"
        )
    file_part = value.split("::", 1)[0]
    if (
        "\\" in file_part
        or file_part.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:", file_part)
        or any(part in {"", ".", ".."} for part in file_part.split("/"))
    ):
        raise CodeBenchmarkVerificationContractError(
            "pytest target cannot escape the detached worktree"
        )
    return value


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkVerificationContractError(f"{field_name} must be text")
    text = value.strip().lower()
    if not _ID_RE.fullmatch(text):
        raise CodeBenchmarkVerificationContractError(
            f"{field_name} must be a lowercase opaque identifier"
        )
    return text


def _digest_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkVerificationContractError(f"{field_name} must be text")
    text = value.strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise CodeBenchmarkVerificationContractError(
            f"{field_name} must be a lowercase SHA-256"
        )
    return text


def _objects(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CodeBenchmarkVerificationContractError(
            f"{field_name} must be an explicit object sequence"
        )
    output: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CodeBenchmarkVerificationContractError(
                f"{field_name} must contain only objects"
            )
        output.append(item)
    return tuple(output)


__all__ = (
    "CODE_BENCHMARK_CONTROLLED_BUDGET_SCOPE",
    "CODE_BENCHMARK_CONTROLLED_VERIFIER_MODE",
    "CODE_BENCHMARK_CONTROLLED_VERIFIER_NETWORK_ISOLATION",
    "CODE_BENCHMARK_CONTROLLED_VERIFIER_POLICY_REVISION",
    "CODE_BENCHMARK_CONTROLLED_VERIFIER_PROCESS_BOUNDARY",
    "CODE_BENCHMARK_CONTROLLED_VERIFIER_SCHEMA",
    "CodeBenchmarkControlledVerificationBudget",
    "CodeBenchmarkControlledVerificationReceipt",
    "CodeBenchmarkVerificationCommand",
    "CodeBenchmarkVerificationCommandReceipt",
    "CodeBenchmarkVerificationContractError",
    "CodeBenchmarkVerificationPlan",
)
