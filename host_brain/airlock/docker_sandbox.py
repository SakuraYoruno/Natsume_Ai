"""Docker 容器级沙箱隔离后端。

提供基于 Docker 的代码执行隔离环境：
- 最小化 Python 镜像（python:3.12-slim）
- 只读挂载代码文件
- 无网络（--network=none）
- CPU 与内存限制
- 超时强杀
- 输出采集与截断

Docker 不可用时由 SandboxExecutor 自动降级到 subprocess 后端。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

from host_brain.core.errors import (
    SANDBOX_CONTAINER_UNAVAILABLE,
    SANDBOX_EXEC_FAILED,
    SANDBOX_TIMEOUT,
)


# Docker 沙箱默认配置
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_MEMORY_LIMIT = "128m"
DEFAULT_CPU_QUOTA = 50000  # 0.5 CPU
DEFAULT_CPU_PERIOD = 100000
DOCKER_CMD = "docker"


def is_docker_available() -> bool:
    """检测 Docker 是否可用（docker info 可执行且返回 0）。"""
    try:
        result = subprocess.run(
            [DOCKER_CMD, "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def run_in_docker(
    *,
    code: str,
    sandbox_id: str,
    evidence_ref: str,
    code_digest: str,
    timeout_s: int,
    max_stdout_bytes: int = 65536,
    max_stderr_bytes: int = 16384,
    docker_image: str = DEFAULT_DOCKER_IMAGE,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    cpu_quota: int = DEFAULT_CPU_QUOTA,
    cpu_period: int = DEFAULT_CPU_PERIOD,
) -> Any:
    """在 Docker 容器中执行代码并返回 SandboxExecResult。

    安全保证：
    - --network=none 禁止网络
    - --read-only 只读根文件系统
    - /tmp 为 tmpfs（可写但无持久化）
    - 内存/CPU 限制
    - 超时强杀（docker kill）
    """
    # 延迟导入避免循环依赖
    from host_brain.airlock.sandbox import (
        SandboxExecResult,
        SANDBOX_STATUS_EXEC_FAILED,
        SANDBOX_STATUS_OK,
        SANDBOX_STATUS_TIMEOUT,
        _truncate,
    )

    tmpdir = None
    container_name = f"sandbox_{sandbox_id}"
    try:
        tmpdir = tempfile.mkdtemp(prefix=f"docker_sandbox_{sandbox_id}_")
        script_path = os.path.join(tmpdir, "sandbox_script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)

        docker_args = [
            DOCKER_CMD, "run",
            "--rm",
            "--name", container_name,
            "--network=none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            f"--memory={memory_limit}",
            f"--cpu-quota={cpu_quota}",
            f"--cpu-period={cpu_period}",
            "--pids-limit=32",
            "--security-opt=no-new-privileges",
            "-v", f"{script_path}:/sandbox/script.py:ro",
            "-w", "/sandbox",
            docker_image,
            "python", "/sandbox/script.py",
        ]

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                docker_args,
                capture_output=True,
                timeout=timeout_s + 5,  # 额外 5s 给 Docker 启动开销
                text=False,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int(round((time.perf_counter() - start_time) * 1000))
            # 强杀容器
            _kill_container(container_name)
            return SandboxExecResult(
                sandbox_id=sandbox_id,
                status=SANDBOX_STATUS_TIMEOUT,
                exit_code=None,
                stdout="",
                stderr=f"docker execution timed out after {timeout_s}s",
                duration_ms=duration_ms,
                reason_code=SANDBOX_TIMEOUT,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                resource_usage={
                    "timeout_s": timeout_s,
                    "isolation_backend": "docker",
                    "docker_image": docker_image,
                    "memory_limit": memory_limit,
                },
            )
        except FileNotFoundError:
            duration_ms = int(round((time.perf_counter() - start_time) * 1000))
            return SandboxExecResult(
                sandbox_id=sandbox_id,
                status=SANDBOX_STATUS_EXEC_FAILED,
                exit_code=None,
                stdout="",
                stderr=f"docker command not found: {DOCKER_CMD}",
                duration_ms=duration_ms,
                reason_code=SANDBOX_CONTAINER_UNAVAILABLE,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                resource_usage={"isolation_backend": "docker", "degraded_from": "docker"},
            )

        duration_ms = int(round((time.perf_counter() - start_time) * 1000))

        raw_stdout = proc.stdout or b""
        raw_stderr = proc.stderr or b""
        stdout_text = raw_stdout.decode("utf-8", errors="replace")
        stderr_text = raw_stderr.decode("utf-8", errors="replace")
        stdout_text, trunc_out = _truncate(stdout_text, max_stdout_bytes)
        stderr_text, trunc_err = _truncate(stderr_text, max_stderr_bytes)

        resource_usage = {
            "stdout_bytes": len(raw_stdout),
            "stderr_bytes": len(raw_stderr),
            "timeout_s": timeout_s,
            "isolation_backend": "docker",
            "docker_image": docker_image,
            "memory_limit": memory_limit,
        }

        if proc.returncode != 0:
            # Docker OOM kill 通常返回 137
            reason = SANDBOX_EXEC_FAILED
            status = SANDBOX_STATUS_EXEC_FAILED
            if proc.returncode == 137:
                resource_usage["oom_killed"] = True
            return SandboxExecResult(
                sandbox_id=sandbox_id,
                status=status,
                exit_code=proc.returncode,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_ms=duration_ms,
                reason_code=reason,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                resource_usage=resource_usage,
                truncated_stdout=trunc_out,
                truncated_stderr=trunc_err,
            )

        return SandboxExecResult(
            sandbox_id=sandbox_id,
            status=SANDBOX_STATUS_OK,
            exit_code=0,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=duration_ms,
            reason_code=None,
            evidence_ref=evidence_ref,
            code_digest=code_digest,
            resource_usage=resource_usage,
            truncated_stdout=trunc_out,
            truncated_stderr=trunc_err,
        )

    except Exception as exc:
        return SandboxExecResult(
            sandbox_id=sandbox_id,
            status=SANDBOX_STATUS_EXEC_FAILED,
            exit_code=None,
            stdout="",
            stderr=f"docker sandbox setup failed: {exc}",
            duration_ms=0,
            reason_code=SANDBOX_EXEC_FAILED,
            evidence_ref=evidence_ref,
            code_digest=code_digest,
            resource_usage={"isolation_backend": "docker"},
        )
    finally:
        if tmpdir is not None:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


def _kill_container(name: str) -> None:
    """尽力强杀容器（忽略错误）。"""
    try:
        subprocess.run(
            [DOCKER_CMD, "kill", name],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            [DOCKER_CMD, "rm", "-f", name],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass
