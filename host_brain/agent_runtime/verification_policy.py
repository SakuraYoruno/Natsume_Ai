"""Verification contract plus fail-closed command verification policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any, Mapping, Protocol

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    VERIFICATION_RESULT_INVALID,
    ContractViolation,
    require_version,
)
from host_brain.core.ids import now_ms
from host_brain.core.subprocess_utils import run as _subprocess_run
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationGate as _ContractVerificationGate,
    VerificationResult as _ContractVerificationResult,
    VerificationStatus as _ContractVerificationStatus,
)


VERIFICATION_RESULT_SCHEMA_VERSION = "Natsume.agent.verification_result.v1"

VERIFICATION_COMMAND_FAILED = "VERIFICATION_COMMAND_FAILED"
VERIFICATION_COMMAND_FORBIDDEN = "VERIFICATION_COMMAND_FORBIDDEN"
VERIFICATION_EVIDENCE_REQUIRED = "VERIFICATION_EVIDENCE_REQUIRED"
VERIFICATION_NOT_RUN = "VERIFICATION_NOT_RUN"
VERIFICATION_PLAN_INVALID = "VERIFICATION_PLAN_INVALID"
VERIFICATION_PROVIDER_ERROR = "VERIFICATION_PROVIDER_ERROR"
VERIFICATION_REQUIRED_NOT_PASSED = "VERIFICATION_REQUIRED_NOT_PASSED"
VERIFICATION_TIMEOUT = "VERIFICATION_TIMEOUT"
VERIFICATION_WORKSPACE_ESCAPE = "VERIFICATION_WORKSPACE_ESCAPE"

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_MAX_COMMANDS = 5
_MAX_OUTPUT_CHARS = 4000
_MAX_TIMEOUT_SECONDS = 300
_ALLOWED_PYTHON_MODULES = frozenset({"pytest"})
_ALLOWED_GIT_SUBCOMMANDS = frozenset({"diff", "ls-files", "rev-parse", "status"})
_FORBIDDEN_GIT_ARGS = frozenset(
    {
        "-C",
        "-c",
        "--config-env",
        "--exec-path",
        "--ext-diff",
        "--git-dir",
        "--work-tree",
    }
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
        try:
            status = VerificationStatus(str(data.get("status") or "").strip().upper())
        except ValueError as exc:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                f"unsupported VerificationResult.status: {data.get('status')}",
            ) from exc

        def optional_text(key: str) -> str | None:
            value = data.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be a string")
            stripped = value.strip()
            return stripped or None

        def required_text(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str):
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be a string")
            return value.strip()

        def optional_int(key: str) -> int | None:
            value = data.get(key)
            if value is None:
                return None
            if type(value) is not int:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be an integer")
            if value < 0:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be non-negative")
            return value

        required_raw = data.get("required", False)
        if type(required_raw) is not bool:
            raise ContractViolation(CONTRACT_FIELD_INVALID, "required must be a boolean")

        evidence_raw = data.get("evidence_refs")
        checks_raw = data.get("checks")
        if not isinstance(evidence_raw, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "evidence_refs must be a list")
        if not isinstance(checks_raw, list):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "checks must be a list")

        result = cls(
            verification_id=required_text("verification_id"),
            work_order_id=required_text("work_order_id"),
            required=required_raw,
            status=status,
            task_graph_id=optional_text("task_graph_id"),
            task_node_id=optional_text("task_node_id"),
            attempt_id=optional_text("attempt_id"),
            reason_code=optional_text("reason_code"),
            evidence_refs=tuple(
                item.strip()
                for item in evidence_raw
                if isinstance(item, str) and item.strip()
            ),
            checks=tuple(dict(item) for item in checks_raw if isinstance(item, Mapping)),
            started_at_ms=optional_int("started_at_ms"),
            finished_at_ms=optional_int("finished_at_ms"),
        )
        if len(result.evidence_refs) != len(evidence_raw):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "evidence_refs must contain only non-empty strings")
        if len(result.checks) != len(checks_raw):
            raise ContractViolation(CONTRACT_FIELD_INVALID, "checks must contain only objects")
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
        if self.status is VerificationStatus.PASS and self.reason_code:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "PASS VerificationResult cannot carry a failure reason_code",
            )
        if bool(self.task_graph_id) != bool(self.task_node_id):
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "task_graph_id and task_node_id must be set together",
            )
        if any(
            not _DURABLE_REFERENCE_PATTERN.fullmatch(reference)
            for reference in self.evidence_refs
        ):
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "evidence_refs must contain only bounded durable references",
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


class VerificationCommandRunner(Protocol):
    def run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        ...


class SubprocessVerificationCommandRunner:
    def run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return _subprocess_run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=env,
            check=False,
        )


class _VerificationRejected(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class _CommandSpec:
    raw: Any
    argv: list[str]
    cwd: Path
    timeout_seconds: int


def _is_bool(value: Any) -> bool:
    return type(value) is bool


def _required_from_plan(plan: Mapping[str, Any]) -> bool:
    value = plan.get("required", False)
    if value is None:
        return False
    if not _is_bool(value):
        raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "verification_plan.required must be a boolean")
    return value


def _timeout_seconds(value: Any, *, default: int = 120) -> int:
    if value is None:
        return default
    if type(value) is not int:
        raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "timeout_seconds must be an integer")
    if value < 1 or value > _MAX_TIMEOUT_SECONDS:
        raise _VerificationRejected(
            VERIFICATION_PLAN_INVALID,
            f"timeout_seconds must be between 1 and {_MAX_TIMEOUT_SECONDS}",
        )
    return value


def _verification_id(*, work_order_id: str, intent_id: str, request_id: str | None) -> str:
    seed = json.dumps(
        {"work_order_id": work_order_id, "intent_id": intent_id, "request_id": request_id},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "verify_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _digest_payload(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _evidence_ref(verification_id: str, index: int, payload: Mapping[str, Any]) -> str:
    return f"verification://{verification_id}/check/{index}/{_digest_payload(payload)}"


def _normalize_workspace(workspace: Path) -> Path:
    try:
        resolved = workspace.resolve(strict=True)
    except OSError as exc:
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, "workspace must exist") from exc
    if not resolved.is_dir():
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, "workspace must be a directory")
    return resolved


def _resolve_cwd(raw_cwd: Any, *, workspace: Path) -> Path:
    if raw_cwd in (None, ""):
        return workspace
    if not isinstance(raw_cwd, str):
        raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "command cwd must be a string")
    candidate = Path(raw_cwd)
    if candidate.is_absolute():
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, "command cwd must be relative to workspace")
    try:
        resolved = (workspace / candidate).resolve(strict=True)
    except OSError as exc:
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, "command cwd must exist inside workspace") from exc
    if not _path_is_relative_to(resolved, workspace):
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, "command cwd escapes workspace")
    return resolved


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_argv(raw: Any) -> list[str]:
    if isinstance(raw, str):
        try:
            parsed = shlex.split(raw, posix=os.name != "nt")
        except ValueError as exc:
            raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "command string is not parseable") from exc
        return [part.strip() for part in parsed if part.strip()]
    if isinstance(raw, list):
        argv = []
        for part in raw:
            if not isinstance(part, str):
                raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "command argv entries must be strings")
            stripped = part.strip()
            if stripped:
                argv.append(stripped)
        return argv
    raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "command must be a string, argv list, or object")


def _command_specs(plan: Mapping[str, Any], *, workspace: Path) -> list[_CommandSpec]:
    raw_commands = plan.get("commands")
    if raw_commands in (None, ""):
        return []
    if not isinstance(raw_commands, list):
        raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "verification_plan.commands must be a list")

    default_timeout = _timeout_seconds(plan.get("timeout_seconds"), default=120)
    default_cwd = _resolve_cwd(plan.get("cwd"), workspace=workspace)
    specs: list[_CommandSpec] = []
    for raw_command in raw_commands[:_MAX_COMMANDS]:
        raw_argv = raw_command
        raw_cwd = None
        raw_timeout = None
        if isinstance(raw_command, Mapping):
            raw_argv = raw_command.get("argv", raw_command.get("cmd", raw_command.get("command")))
            raw_cwd = raw_command.get("cwd")
            raw_timeout = raw_command.get("timeout_seconds")
        argv = _parse_argv(raw_argv)
        if not argv:
            continue
        specs.append(
            _CommandSpec(
                raw=raw_command,
                argv=argv,
                cwd=_resolve_cwd(raw_cwd, workspace=default_cwd),
                timeout_seconds=_timeout_seconds(raw_timeout, default=default_timeout),
            )
        )
    return specs


def _looks_path_like(value: str) -> bool:
    lowered = value.lower()
    return (
        "/" in value
        or "\\" in value
        or lowered.endswith((".py", ".json", ".toml", ".ini", ".cfg", ".txt", ".md", ".yml", ".yaml"))
        or value in {".", ".."}
        or value.startswith(".")
    )


def _validate_path_token(token: str, *, workspace: Path, cwd: Path) -> None:
    if not token or "://" in token:
        return
    path_part = token.split("::", 1)[0].strip().strip("\"'")
    if not path_part or not _looks_path_like(path_part):
        return
    candidate = Path(path_part)
    if any(part == ".." for part in candidate.parts):
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, f"path escapes workspace: {token}")
    base = candidate if candidate.is_absolute() else cwd / candidate
    try:
        resolved = base.resolve(strict=False)
    except OSError as exc:
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, f"path is not safe: {token}") from exc
    if not _path_is_relative_to(resolved, workspace):
        raise _VerificationRejected(VERIFICATION_WORKSPACE_ESCAPE, f"path escapes workspace: {token}")


def _validate_argv_paths(argv: list[str], *, workspace: Path, cwd: Path) -> None:
    for arg in argv:
        if "\x00" in arg:
            raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, "command arguments cannot contain NUL bytes")
        if arg.startswith("@"):
            raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, "response-file arguments are forbidden")
        if arg.startswith("-") and "=" in arg:
            _validate_path_token(arg.split("=", 1)[1], workspace=workspace, cwd=cwd)
        elif not arg.startswith("-"):
            _validate_path_token(arg, workspace=workspace, cwd=cwd)


def _is_python_binary(value: str) -> bool:
    name = Path(value).name.lower()
    if name in {"python", "python.exe"}:
        return True
    try:
        return Path(value).resolve(strict=False) == Path(sys.executable).resolve(strict=True)
    except OSError:
        return False


def _is_git_binary(value: str) -> bool:
    name = Path(value).name.lower()
    if name in {"git", "git.exe"}:
        return True
    git_path = shutil.which("git")
    if not git_path:
        return False
    try:
        return Path(value).resolve(strict=False) == Path(git_path).resolve(strict=True)
    except OSError:
        return False


def _normalize_python(argv: list[str], *, workspace: Path, cwd: Path) -> list[str]:
    if len(argv) < 3 or argv[1] != "-m":
        raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, "python verification must use '-m <allowed-module>'")
    module = argv[2].strip()
    if module not in _ALLOWED_PYTHON_MODULES:
        raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, f"python module is not allowlisted: {module}")
    tail = argv[3:]
    _validate_argv_paths(tail, workspace=workspace, cwd=cwd)
    return [sys.executable, "-m", module, *tail]


def _normalize_git(argv: list[str], *, workspace: Path, cwd: Path) -> list[str]:
    if len(argv) < 2:
        raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, "git verification requires a read-only subcommand")
    for arg in argv[1:]:
        option = arg.split("=", 1)[0]
        if option in _FORBIDDEN_GIT_ARGS:
            raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, f"git argument is forbidden: {option}")
    subcommand = argv[1].strip()
    if subcommand not in _ALLOWED_GIT_SUBCOMMANDS:
        raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, f"git subcommand is not allowlisted: {subcommand}")
    tail = argv[2:]
    _validate_argv_paths(tail, workspace=workspace, cwd=cwd)
    git = shutil.which("git") or "git"
    return [git, subcommand, *tail]


def _normalize_argv(argv: list[str], *, workspace: Path, cwd: Path) -> list[str]:
    if not argv:
        raise _VerificationRejected(VERIFICATION_PLAN_INVALID, "empty verification command")
    executable = argv[0]
    if _is_python_binary(executable):
        return _normalize_python(argv, workspace=workspace, cwd=cwd)
    if Path(executable).name.lower() in {"pytest", "pytest.exe"}:
        tail = argv[1:]
        _validate_argv_paths(tail, workspace=workspace, cwd=cwd)
        return [sys.executable, "-m", "pytest", *tail]
    if _is_git_binary(executable):
        return _normalize_git(argv, workspace=workspace, cwd=cwd)
    raise _VerificationRejected(VERIFICATION_COMMAND_FORBIDDEN, f"command is not allowlisted: {Path(executable).name}")


def _verification_env() -> dict[str, str]:
    keep = {
        "COMSPEC",
        "HOME",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in keep and value}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _status_from_rows(rows: list[dict[str, Any]]) -> VerificationStatus:
    statuses = [str(row.get("status") or "") for row in rows]
    if not rows:
        return VerificationStatus.NOT_RUN
    if VerificationStatus.ERROR.value in statuses:
        return VerificationStatus.ERROR
    if VerificationStatus.FAIL.value in statuses:
        return VerificationStatus.FAIL
    return VerificationStatus.PASS


def _reason_from_status(status: VerificationStatus, rows: list[dict[str, Any]]) -> str | None:
    if status is VerificationStatus.PASS:
        return None
    for row in rows:
        reason = str(row.get("reason_code") or "").strip()
        if reason:
            return reason
    if status is VerificationStatus.NOT_RUN:
        return VERIFICATION_NOT_RUN
    if status is VerificationStatus.FAIL:
        return VERIFICATION_COMMAND_FAILED
    return VERIFICATION_PROVIDER_ERROR


def _bounded(value: Any) -> str:
    return str(value or "")[:_MAX_OUTPUT_CHARS]


class VerificationGate:
    @staticmethod
    def required_plan_not_passed(plan: Mapping[str, Any], result: Mapping[str, Any] | VerificationResult) -> bool:
        try:
            required = _required_from_plan(plan)
            if not required:
                return False
            verification = (
                result if isinstance(result, VerificationResult) else VerificationResult.from_mapping(result)
            )
        except (_VerificationRejected, ContractViolation):
            return True
        return required and verification.status is not VerificationStatus.PASS

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
            raise ContractViolation(
                VERIFICATION_REQUIRED_NOT_PASSED,
                f"{target_status} transition requires a VerificationResult",
            )
        verification = result if isinstance(result, VerificationResult) else VerificationResult.from_mapping(result)
        if verification.work_order_id != root_work_order_id:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "VerificationResult.work_order_id must match TaskGraph.root_work_order_id",
            )
        if verification.task_graph_id != graph_id or verification.task_node_id != node_id:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "VerificationResult task graph/node identity must match the state transition",
            )
        if not verification.evidence_refs:
            raise ContractViolation(
                VERIFICATION_EVIDENCE_REQUIRED,
                "verification state transitions require durable evidence refs",
            )
        missing = sorted(set(verification.evidence_refs) - set(outbox_evidence_refs))
        if missing:
            raise ContractViolation(
                VERIFICATION_EVIDENCE_REQUIRED,
                "outbox evidence_refs must include VerificationResult evidence refs",
            )
        if target_status == "VERIFIED" and verification.status is not VerificationStatus.PASS:
            raise ContractViolation(
                VERIFICATION_REQUIRED_NOT_PASSED,
                f"VERIFIED transition requires PASS verification, got {verification.status.value}",
            )
        if target_status == "VERIFICATION_FAILED" and verification.status is VerificationStatus.PASS:
            raise ContractViolation(
                VERIFICATION_RESULT_INVALID,
                "VERIFICATION_FAILED transition cannot use PASS verification",
            )
        return verification


# Runtime execution and persistence must share one contract type.  The policy
# below owns command execution only; canonical DTO/transition authority lives
# in verification_contract.
VerificationGate = _ContractVerificationGate
VerificationResult = _ContractVerificationResult
VerificationStatus = _ContractVerificationStatus


class VerificationPolicy:
    def __init__(
        self,
        *,
        runner: VerificationCommandRunner | None = None,
        clock: Any = now_ms,
    ) -> None:
        self._runner = runner or SubprocessVerificationCommandRunner()
        self._clock = clock

    def run(
        self,
        plan: Mapping[str, Any],
        *,
        workspace: Path,
        request_id: str | None,
        intent_id: str,
        work_order_id: str | None = None,
        task_graph_id: str | None = None,
        task_node_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_plan = dict(plan or {})
        stable_work_order_id = str(work_order_id or request_id or intent_id or "work-order-unknown").strip()
        verification_id = _verification_id(
            work_order_id=stable_work_order_id,
            intent_id=str(intent_id or ""),
            request_id=request_id,
        )
        started_at = int(self._clock())
        rows: list[dict[str, Any]] = []
        required = False
        workspace_path = Path(workspace)
        try:
            required = _required_from_plan(normalized_plan)
            workspace_path = _normalize_workspace(Path(workspace))
            code = str(normalized_plan.get("verification_code") or "").strip()
            if code:
                rows = self._run_sandbox_code(
                    code,
                    request_id=request_id,
                    intent_id=intent_id,
                    verification_id=verification_id,
                )
            else:
                rows = self._run_commands(
                    normalized_plan,
                    workspace=workspace_path,
                    verification_id=verification_id,
                )
        except _VerificationRejected as exc:
            rows = [
                {
                    "status": VerificationStatus.ERROR.value,
                    "reason_code": exc.reason_code,
                    "detail": exc.detail,
                    "command": [],
                    "evidence_ref": _evidence_ref(
                        verification_id,
                        1,
                        {"status": "ERROR", "reason_code": exc.reason_code, "detail": exc.detail},
                    ),
                }
            ]
        except Exception as exc:  # Fail closed on provider and platform failures.
            rows = [
                {
                    "status": VerificationStatus.ERROR.value,
                    "reason_code": VERIFICATION_PROVIDER_ERROR,
                    "detail": str(exc)[:1024],
                    "command": [],
                    "evidence_ref": _evidence_ref(
                        verification_id,
                        1,
                        {"status": "ERROR", "reason_code": VERIFICATION_PROVIDER_ERROR, "detail": str(exc)[:1024]},
                    ),
                }
            ]

        status = _status_from_rows(rows)
        if status is VerificationStatus.NOT_RUN:
            rows = [
                {
                    "status": VerificationStatus.NOT_RUN.value,
                    "reason_code": VERIFICATION_NOT_RUN,
                    "detail": "verification plan did not request executable checks",
                    "command": [],
                    "evidence_ref": _evidence_ref(
                        verification_id,
                        1,
                        {"status": "NOT_RUN", "reason_code": VERIFICATION_NOT_RUN},
                    ),
                }
            ]
            status = VerificationStatus.NOT_RUN
        finished_at = int(self._clock())
        result = VerificationResult(
            verification_id=verification_id,
            work_order_id=stable_work_order_id,
            required=required,
            status=status,
            task_graph_id=str(task_graph_id or "").strip() or None,
            task_node_id=str(task_node_id or "").strip() or None,
            attempt_id=str(attempt_id or "").strip() or None,
            reason_code=_reason_from_status(status, rows),
            evidence_refs=tuple(str(row.get("evidence_ref") or "").strip() for row in rows if row.get("evidence_ref")),
            checks=tuple(rows),
            started_at_ms=started_at,
            finished_at_ms=finished_at,
        )
        result.validate()
        return result.to_dict()

    def _run_sandbox_code(
        self,
        code: str,
        *,
        request_id: str | None,
        intent_id: str,
        verification_id: str,
    ) -> list[dict[str, Any]]:
        from host_brain.airlock import SandboxExecutor  # noqa: PLC0415

        try:
            result = SandboxExecutor().execute(
                code,
                request_id=request_id,
                intent_id=intent_id,
                language="python",
            )
        except Exception as exc:
            payload = {"status": "ERROR", "reason_code": VERIFICATION_PROVIDER_ERROR, "detail": str(exc)[:1024]}
            return [
                {
                    **payload,
                    "command": ["sandbox:python"],
                    "evidence_ref": _evidence_ref(verification_id, 1, payload),
                }
            ]
        raw_status = str(getattr(result, "status", "") or "").strip().upper()
        status = raw_status if raw_status in {item.value for item in VerificationStatus} else VerificationStatus.ERROR.value
        reason = None if status == VerificationStatus.PASS.value else str(getattr(result, "reason_code", "") or VERIFICATION_PROVIDER_ERROR)
        payload = {
            "status": status,
            "reason_code": reason,
            "exit_code": getattr(result, "exit_code", None),
            "stdout": _bounded(getattr(result, "stdout", "")),
            "stderr": _bounded(getattr(result, "stderr", "")),
            "duration_ms": getattr(result, "duration_ms", None),
            "evidence_ref": getattr(result, "evidence_ref", None),
            "code_digest": getattr(result, "code_digest", None),
        }
        evidence = str(payload.get("evidence_ref") or "").strip()
        if not evidence or not _DURABLE_REFERENCE_PATTERN.fullmatch(evidence):
            evidence = _evidence_ref(verification_id, 1, payload)
        return [{**payload, "command": ["sandbox:python"], "evidence_ref": evidence}]

    def _run_commands(
        self,
        plan: Mapping[str, Any],
        *,
        workspace: Path,
        verification_id: str,
    ) -> list[dict[str, Any]]:
        specs = _command_specs(plan, workspace=workspace)
        rows: list[dict[str, Any]] = []
        env = _verification_env()
        for index, spec in enumerate(specs, start=1):
            try:
                command = _normalize_argv(spec.argv, workspace=workspace, cwd=spec.cwd)
                completed = self._runner.run(
                    command,
                    cwd=spec.cwd,
                    timeout_seconds=spec.timeout_seconds,
                    env=env,
                )
                row = {
                    "status": VerificationStatus.PASS.value
                    if int(completed.returncode) == 0
                    else VerificationStatus.FAIL.value,
                    "reason_code": None if int(completed.returncode) == 0 else VERIFICATION_COMMAND_FAILED,
                    "command": command,
                    "cwd": str(spec.cwd),
                    "timeout_seconds": spec.timeout_seconds,
                    "exit_code": int(completed.returncode),
                    "stdout": _bounded(completed.stdout),
                    "stderr": _bounded(completed.stderr),
                }
            except _VerificationRejected as exc:
                row = {
                    "status": VerificationStatus.ERROR.value,
                    "reason_code": exc.reason_code,
                    "detail": exc.detail,
                    "command": list(spec.argv),
                    "cwd": str(spec.cwd),
                    "timeout_seconds": spec.timeout_seconds,
                }
            except subprocess.TimeoutExpired as exc:
                row = {
                    "status": VerificationStatus.ERROR.value,
                    "reason_code": VERIFICATION_TIMEOUT,
                    "detail": f"verification command timed out after {spec.timeout_seconds}s",
                    "command": list(spec.argv),
                    "cwd": str(spec.cwd),
                    "timeout_seconds": spec.timeout_seconds,
                    "stdout": _bounded(getattr(exc, "output", "")),
                    "stderr": _bounded(getattr(exc, "stderr", "")),
                }
            except Exception as exc:
                row = {
                    "status": VerificationStatus.ERROR.value,
                    "reason_code": VERIFICATION_PROVIDER_ERROR,
                    "detail": str(exc)[:1024],
                    "command": list(spec.argv),
                    "cwd": str(spec.cwd),
                    "timeout_seconds": spec.timeout_seconds,
                }
            row["evidence_ref"] = _evidence_ref(verification_id, index, row)
            rows.append(row)
            if row["status"] == VerificationStatus.ERROR.value:
                break
        return rows


__all__ = [
    "VERIFICATION_COMMAND_FAILED",
    "VERIFICATION_COMMAND_FORBIDDEN",
    "VERIFICATION_EVIDENCE_REQUIRED",
    "VERIFICATION_NOT_RUN",
    "VERIFICATION_PLAN_INVALID",
    "VERIFICATION_PROVIDER_ERROR",
    "VERIFICATION_REQUIRED_NOT_PASSED",
    "VERIFICATION_RESULT_SCHEMA_VERSION",
    "VERIFICATION_TIMEOUT",
    "VERIFICATION_WORKSPACE_ESCAPE",
    "SubprocessVerificationCommandRunner",
    "VerificationCommandRunner",
    "VerificationGate",
    "VerificationPolicy",
    "VerificationResult",
    "VerificationStatus",
]
