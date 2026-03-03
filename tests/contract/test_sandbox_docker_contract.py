"""T-Sandbox-Docker-01: Docker 容器级沙箱隔离契约测试。

验收断言：
- Docker 可用性检测（is_docker_available）返回 bool
- Docker 不可用时自动降级到 subprocess 后端
- 降级结果 resource_usage 含 degraded_from=docker
- isolation_backend=docker 配置正确传播
- isolation_backend=subprocess（默认）行为不变
- SandboxConfig 新增 isolation_backend 字段
- SANDBOX_CONTAINER_UNAVAILABLE 错误码存在
- Docker 命令参数构建正确（--network=none, --read-only, 资源限制）
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from host_brain.airlock.docker_sandbox import (
    DEFAULT_DOCKER_IMAGE,
    DEFAULT_MEMORY_LIMIT,
    DOCKER_CMD,
    is_docker_available,
    run_in_docker,
    _kill_container,
)
from host_brain.airlock.sandbox import (
    SandboxConfig,
    SandboxExecResult,
    SandboxExecutor,
    SANDBOX_STATUS_EXEC_FAILED,
    SANDBOX_STATUS_OK,
    SANDBOX_STATUS_TIMEOUT,
    SANDBOX_STATUS_UNAVAILABLE,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    SANDBOX_CONTAINER_UNAVAILABLE,
    SANDBOX_EXEC_FAILED,
    SANDBOX_TIMEOUT,
    SANDBOX_UNAVAILABLE,
)


NOW_MS = 1_800_400_000_000


# ---------------------------------------------------------------------------
# Docker 可用性检测
# ---------------------------------------------------------------------------

class TestDockerAvailability:
    """is_docker_available 检测行为。"""

    def test_docker_available_when_info_succeeds(self) -> None:
        with patch("host_brain.airlock.docker_sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert is_docker_available() is True

    def test_docker_unavailable_when_info_fails(self) -> None:
        with patch("host_brain.airlock.docker_sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert is_docker_available() is False

    def test_docker_unavailable_when_not_found(self) -> None:
        with patch("host_brain.airlock.docker_sandbox.subprocess.run", side_effect=FileNotFoundError):
            assert is_docker_available() is False

    def test_docker_unavailable_when_timeout(self) -> None:
        with patch("host_brain.airlock.docker_sandbox.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 5)):
            assert is_docker_available() is False


# ---------------------------------------------------------------------------
# Docker 不可用降级
# ---------------------------------------------------------------------------

class TestDockerDegradation:
    """Docker 不可用时自动降级到 subprocess 后端。"""

    def test_docker_backend_degrades_to_subprocess(self) -> None:
        """isolation_backend=docker + Docker 不可用 → 降级到 subprocess。"""
        config = SandboxConfig(isolation_backend="docker", timeout_s=5)
        ledger = SQLiteLedger(db_path=":memory:")
        executor = SandboxExecutor(config=config, ledger=ledger)

        with patch("host_brain.airlock.docker_sandbox.is_docker_available", return_value=False):
            result = executor.execute(
                'print("hello")',
                request_id="req_docker_001",
                ts_ms=NOW_MS,
            )

        # 降级后仍能正常执行
        assert result.status == SANDBOX_STATUS_OK
        assert result.resource_usage.get("degraded_from") == "docker"
        assert result.resource_usage.get("isolation_backend") == "subprocess"

    def test_docker_backend_degrades_audit_logged(self) -> None:
        """降级事件写入 sandbox_exec_log。"""
        config = SandboxConfig(isolation_backend="docker", timeout_s=5)
        ledger = SQLiteLedger(db_path=":memory:")
        executor = SandboxExecutor(config=config, ledger=ledger)

        with patch("host_brain.airlock.docker_sandbox.is_docker_available", return_value=False):
            executor.execute(
                'print("audit")',
                request_id="req_docker_002",
                ts_ms=NOW_MS,
            )

        rows = ledger.sandbox_exec_log
        assert len(rows) == 1
        assert rows[0]["request_id"] == "req_docker_002"

    def test_subprocess_backend_unaffected(self) -> None:
        """默认 isolation_backend=subprocess 不走 Docker 路径。"""
        config = SandboxConfig(timeout_s=5)
        ledger = SQLiteLedger(db_path=":memory:")
        executor = SandboxExecutor(config=config, ledger=ledger)

        result = executor.execute(
            'print("default")',
            request_id="req_subprocess_001",
            ts_ms=NOW_MS,
        )
        assert result.status == SANDBOX_STATUS_OK
        assert "degraded_from" not in result.resource_usage


# ---------------------------------------------------------------------------
# Docker 执行后端（模拟 Docker 命令）
# ---------------------------------------------------------------------------

class TestDockerExecution:
    """Docker 容器执行行为（通过 mock 验证命令构建）。"""

    def test_run_in_docker_success(self) -> None:
        """Docker 执行成功返回 OK。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=b"hello world\n",
                stderr=b"",
            )
            result = run_in_docker(
                code='print("hello world")',
                sandbox_id="sbx_test_001",
                evidence_ref="sandbox://sbx_test_001",
                code_digest="sha256:abc",
                timeout_s=10,
            )
            assert result.status == SANDBOX_STATUS_OK
            assert result.exit_code == 0
            assert "hello world" in result.stdout
            assert result.resource_usage["isolation_backend"] == "docker"
            assert result.resource_usage["docker_image"] == DEFAULT_DOCKER_IMAGE

    def test_run_in_docker_timeout(self) -> None:
        """Docker 执行超时返回 TIMEOUT。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 10)):
            with patch("host_brain.airlock.docker_sandbox._kill_container"):
                result = run_in_docker(
                    code='import time; time.sleep(999)',
                    sandbox_id="sbx_test_002",
                    evidence_ref="sandbox://sbx_test_002",
                    code_digest="sha256:def",
                    timeout_s=10,
                )
                assert result.status == SANDBOX_STATUS_TIMEOUT
                assert result.reason_code == SANDBOX_TIMEOUT

    def test_run_in_docker_oom_exit_137(self) -> None:
        """Docker OOM kill（exit 137）写入 oom_killed。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=137,
                stdout=b"",
                stderr=b"Killed",
            )
            result = run_in_docker(
                code='x = [0]*10**9',
                sandbox_id="sbx_test_003",
                evidence_ref="sandbox://sbx_test_003",
                code_digest="sha256:ghi",
                timeout_s=10,
            )
            assert result.status == SANDBOX_STATUS_EXEC_FAILED
            assert result.exit_code == 137
            assert result.resource_usage.get("oom_killed") is True

    def test_run_in_docker_nonzero_exit(self) -> None:
        """Docker 执行非零退出码返回 EXEC_FAILED。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=b"",
                stderr=b"SyntaxError",
            )
            result = run_in_docker(
                code='invalid syntax!!!',
                sandbox_id="sbx_test_004",
                evidence_ref="sandbox://sbx_test_004",
                code_digest="sha256:jkl",
                timeout_s=10,
            )
            assert result.status == SANDBOX_STATUS_EXEC_FAILED
            assert result.reason_code == SANDBOX_EXEC_FAILED

    def test_docker_command_has_security_flags(self) -> None:
        """Docker 命令包含安全标志（--network=none, --read-only, 资源限制）。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"ok", stderr=b"")
            run_in_docker(
                code='print("ok")',
                sandbox_id="sbx_sec_001",
                evidence_ref="sandbox://sbx_sec_001",
                code_digest="sha256:sec",
                timeout_s=10,
            )
            args = mock_run.call_args[0][0]
            assert "--network=none" in args
            assert "--read-only" in args
            assert "--pids-limit=32" in args
            assert "--security-opt=no-new-privileges" in args
            assert any("--memory=" in a for a in args)

    def test_docker_command_not_found(self) -> None:
        """Docker 命令不存在返回 CONTAINER_UNAVAILABLE。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run", side_effect=FileNotFoundError):
            result = run_in_docker(
                code='print("nope")',
                sandbox_id="sbx_nf_001",
                evidence_ref="sandbox://sbx_nf_001",
                code_digest="sha256:nf",
                timeout_s=10,
            )
            assert result.reason_code == SANDBOX_CONTAINER_UNAVAILABLE


# ---------------------------------------------------------------------------
# 配置与常量
# ---------------------------------------------------------------------------

class TestDockerSandboxConfig:
    """Docker 沙箱配置正确性。"""

    def test_config_has_isolation_backend(self) -> None:
        config = SandboxConfig()
        assert config.isolation_backend == "subprocess"

    def test_config_docker_backend(self) -> None:
        config = SandboxConfig(isolation_backend="docker")
        assert config.isolation_backend == "docker"

    def test_container_unavailable_error_code(self) -> None:
        assert SANDBOX_CONTAINER_UNAVAILABLE == "SANDBOX_CONTAINER_UNAVAILABLE"

    def test_default_docker_image(self) -> None:
        assert DEFAULT_DOCKER_IMAGE == "python:3.12-slim"

    def test_kill_container_ignores_errors(self) -> None:
        """_kill_container 失败时不抛异常。"""
        with patch("host_brain.airlock.docker_sandbox.subprocess.run", side_effect=Exception("fail")):
            _kill_container("nonexistent")  # 不应抛异常


# ---------------------------------------------------------------------------
# subprocess 后端无回归
# ---------------------------------------------------------------------------

class TestSubprocessRegression:
    """subprocess 后端行为完全不变。"""

    def test_subprocess_normal_execution(self) -> None:
        config = SandboxConfig(timeout_s=5)
        ledger = SQLiteLedger(db_path=":memory:")
        executor = SandboxExecutor(config=config, ledger=ledger)
        result = executor.execute('print("hello")', request_id="reg_001", ts_ms=NOW_MS)
        assert result.status == SANDBOX_STATUS_OK
        assert "hello" in result.stdout

    def test_subprocess_disabled_sandbox(self) -> None:
        config = SandboxConfig(enabled=False)
        executor = SandboxExecutor(config=config)
        result = executor.execute('print("hello")', ts_ms=NOW_MS)
        assert result.status == SANDBOX_STATUS_UNAVAILABLE
        assert result.reason_code == SANDBOX_UNAVAILABLE

    def test_subprocess_code_too_large(self) -> None:
        config = SandboxConfig(max_code_bytes=10, timeout_s=5)
        executor = SandboxExecutor(config=config)
        result = executor.execute('x' * 100, ts_ms=NOW_MS)
        assert result.reason_code == "SANDBOX_RESOURCE_EXCEEDED"
