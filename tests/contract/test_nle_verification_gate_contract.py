from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import pytest

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.contract_errors import ContractViolation
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.verification_policy import (
    VERIFICATION_COMMAND_FORBIDDEN,
    VERIFICATION_REQUIRED_NOT_PASSED,
    VERIFICATION_TIMEOUT,
    VERIFICATION_WORKSPACE_ESCAPE,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
)


class FakeRunner:
    def __init__(self, *, exc: Exception | None = None, returncode: int = 0) -> None:
        self.exc = exc
        self.returncode = returncode
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            {
                "args": list(args),
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "env": dict(env),
            }
        )
        if self.exc is not None:
            raise self.exc
        return subprocess.CompletedProcess(
            args,
            self.returncode,
            stdout="ok",
            stderr="" if self.returncode == 0 else "failed",
        )


def _result(
    *,
    status: VerificationStatus,
    work_order_id: str = "wo-required",
) -> dict[str, Any]:
    verification = VerificationResult(
        verification_id=f"verify-{status.value.lower()}",
        work_order_id=work_order_id,
        required=True,
        status=status,
        reason_code=None if status is VerificationStatus.PASS else f"VERIFICATION_{status.value}",
        evidence_refs=(f"verification://verify-{status.value.lower()}/check/1/result",),
        checks=({"status": status.value},),
        started_at_ms=1,
        finished_at_ms=2,
    )
    return verification.to_dict()


def test_t_nle_08_forbidden_commands_return_structured_error(tmp_path: Path) -> None:
    runner = FakeRunner()
    result = VerificationPolicy(runner=runner).run(
        {"required": True, "commands": [["powershell", "-NoProfile", "-Command", "Get-ChildItem"]]},
        workspace=tmp_path,
        request_id="req-1",
        intent_id="intent-1",
        work_order_id="wo-required",
    )

    assert result["status"] == "ERROR"
    assert result["reason_code"] == VERIFICATION_COMMAND_FORBIDDEN
    assert result["checks"][0]["reason_code"] == VERIFICATION_COMMAND_FORBIDDEN
    assert runner.calls == []


def test_t_nle_08_timeout_returns_structured_error(tmp_path: Path) -> None:
    timeout = subprocess.TimeoutExpired(cmd=["python"], timeout=1, output="partial", stderr="late")
    result = VerificationPolicy(runner=FakeRunner(exc=timeout)).run(
        {"required": True, "timeout_seconds": 1, "commands": [["python", "-m", "pytest", "--version"]]},
        workspace=tmp_path,
        request_id="req-1",
        intent_id="intent-1",
        work_order_id="wo-required",
    )

    assert result["status"] == "ERROR"
    assert result["reason_code"] == VERIFICATION_TIMEOUT
    assert result["checks"][0]["stdout"] == "partial"
    assert result["checks"][0]["stderr"] == "late"


def test_t_nle_08_workspace_escape_returns_structured_error(tmp_path: Path) -> None:
    result = VerificationPolicy(runner=FakeRunner()).run(
        {"required": True, "commands": [["python", "-m", "pytest", "../outside.py"]]},
        workspace=tmp_path,
        request_id="req-1",
        intent_id="intent-1",
        work_order_id="wo-required",
    )

    assert result["status"] == "ERROR"
    assert result["reason_code"] == VERIFICATION_WORKSPACE_ESCAPE
    assert result["checks"][0]["reason_code"] == VERIFICATION_WORKSPACE_ESCAPE


def test_t_nle_08_allowed_command_uses_safe_runner_and_schema(tmp_path: Path) -> None:
    runner = FakeRunner(returncode=0)
    result = VerificationPolicy(runner=runner).run(
        {"required": True, "commands": [["python", "-m", "pytest", "--version"]]},
        workspace=tmp_path,
        request_id="req-1",
        intent_id="intent-1",
        work_order_id="wo-required",
    )

    restored = VerificationResult.from_mapping(result)
    assert restored.status is VerificationStatus.PASS
    assert restored.required is True
    assert runner.calls[0]["args"][1:3] == ["-m", "pytest"]
    assert "PYTHONPATH" not in runner.calls[0]["env"]


def test_t_nle_08_verification_result_rejects_bool_and_float_timestamps() -> None:
    payload = _result(status=VerificationStatus.PASS)
    payload["required"] = 1
    with pytest.raises(ContractViolation):
        VerificationResult.from_mapping(payload)

    payload = _result(status=VerificationStatus.PASS)
    payload["started_at_ms"] = 1.5
    with pytest.raises(ContractViolation):
        VerificationResult.from_mapping(payload)


def test_t_nle_08_executor_blocks_required_verification_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeAgentExecutor:
        def execute(self, order, *, workspace: Path) -> AgentResult:
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="done",
                result_text="done",
                review_required=False,
            )

    class FakeVerificationPolicy:
        def run(self, *_args, **kwargs) -> dict[str, Any]:
            return _result(status=VerificationStatus.FAIL, work_order_id=str(kwargs["work_order_id"]))

    monkeypatch.setattr("host_brain.agent_runtime.executor.AgentExecutor", FakeAgentExecutor)

    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path / "runs"),
        verification_policy=FakeVerificationPolicy(),
    ).execute(
        {
            "work_order_id": "wo-required",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path / "workspaces")},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files", "modify_secret_files"],
            "verification_plan": {"required": True},
            "review_policy": {"review_required": False},
        }
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == VERIFICATION_REQUIRED_NOT_PASSED
    assert result["verification"]["status"] == "FAIL"
    assert "required_verification_failed" in result["risk_flags"]
