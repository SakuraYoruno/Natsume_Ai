"""AIRLOCK DRAFT_CODE sandbox execution with isolation and audit logging."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    SANDBOX_CONTAINER_UNAVAILABLE,
    SANDBOX_EXEC_FAILED,
    SANDBOX_RESOURCE_EXCEEDED,
    SANDBOX_TIMEOUT,
    SANDBOX_UNAVAILABLE,
)
from host_brain.core.ids import now_ms


SANDBOX_STATUS_OK = "OK"
SANDBOX_STATUS_TIMEOUT = "TIMEOUT"
SANDBOX_STATUS_RESOURCE_EXCEEDED = "RESOURCE_EXCEEDED"
SANDBOX_STATUS_EXEC_FAILED = "EXEC_FAILED"
SANDBOX_STATUS_UNAVAILABLE = "UNAVAILABLE"

DEFAULT_TIMEOUT_S = 10
MAX_TIMEOUT_S = 30
DEFAULT_MAX_STDOUT_BYTES = 64 * 1024
DEFAULT_MAX_STDERR_BYTES = 16 * 1024
DEFAULT_MAX_CODE_BYTES = 32 * 1024

ISOLATION_BACKEND_SUBPROCESS = "subprocess"
ISOLATION_BACKEND_DOCKER = "docker"
ISOLATION_BACKEND_AUTO = "auto"
_SUPPORTED_ISOLATION_BACKENDS = frozenset(
    {
        ISOLATION_BACKEND_SUBPROCESS,
        ISOLATION_BACKEND_DOCKER,
        ISOLATION_BACKEND_AUTO,
    }
)
_PROD_ENV_VALUES = frozenset({"prod", "production"})


@dataclass(frozen=True)
class SandboxConfig:
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    max_code_bytes: int = DEFAULT_MAX_CODE_BYTES
    python_executable: str = "python"
    allow_network: bool = False
    allow_fs_write: bool = False
    enabled: bool = True
    isolation_backend: str = ISOLATION_BACKEND_SUBPROCESS
    require_container: bool = False


@dataclass(frozen=True)
class SandboxExecResult:
    sandbox_id: str
    status: str
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: int
    reason_code: Optional[str]
    evidence_ref: str
    resource_usage: dict[str, Any] = field(default_factory=dict)
    code_digest: str = ""
    truncated_stdout: bool = False
    truncated_stderr: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "reason_code": self.reason_code,
            "evidence_ref": self.evidence_ref,
            "resource_usage": self.resource_usage,
            "code_digest": self.code_digest,
            "truncated_stdout": self.truncated_stdout,
            "truncated_stderr": self.truncated_stderr,
        }


def _generate_sandbox_id() -> str:
    return f"sbx_{uuid.uuid4().hex[:16]}"


def _digest_code(code: str) -> str:
    return f"sha256:{hashlib.sha256(code.encode('utf-8')).hexdigest()}"


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated, True


def _is_production_env() -> bool:
    env = str(os.environ.get("Nastume_ENV", "")).strip().lower()
    return env in _PROD_ENV_VALUES


class SandboxExecutor:
    def __init__(
        self,
        *,
        config: Optional[SandboxConfig] = None,
        ledger: Optional[SQLiteLedger] = None,
    ) -> None:
        self.config = config or SandboxConfig()
        self.ledger = ledger

    def execute(
        self,
        code: str,
        *,
        request_id: Optional[str] = None,
        intent_id: Optional[str] = None,
        language: str = "python",
        ts_ms: Optional[int] = None,
    ) -> SandboxExecResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        sandbox_id = _generate_sandbox_id()
        code_digest = _digest_code(code)
        evidence_ref = f"sandbox://{sandbox_id}"

        if not self.config.enabled:
            result = SandboxExecResult(
                sandbox_id=sandbox_id,
                status=SANDBOX_STATUS_UNAVAILABLE,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=0,
                reason_code=SANDBOX_UNAVAILABLE,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
            )
            self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
            return result

        code_bytes = len(code.encode("utf-8", errors="replace"))
        if code_bytes > int(self.config.max_code_bytes):
            result = SandboxExecResult(
                sandbox_id=sandbox_id,
                status=SANDBOX_STATUS_RESOURCE_EXCEEDED,
                exit_code=None,
                stdout="",
                stderr=f"code size {code_bytes} bytes exceeds limit {self.config.max_code_bytes}",
                duration_ms=0,
                reason_code=SANDBOX_RESOURCE_EXCEEDED,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                resource_usage={"code_bytes": code_bytes, "max_code_bytes": int(self.config.max_code_bytes)},
            )
            self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
            return result

        if language != "python":
            result = SandboxExecResult(
                sandbox_id=sandbox_id,
                status=SANDBOX_STATUS_UNAVAILABLE,
                exit_code=None,
                stdout="",
                stderr=f"unsupported language: {language}",
                duration_ms=0,
                reason_code=SANDBOX_UNAVAILABLE,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
            )
            self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
            return result

        timeout_s = max(1, min(int(self.config.timeout_s), MAX_TIMEOUT_S))
        backend = self._resolve_backend()
        container_required = self._container_is_required()

        if container_required and backend != ISOLATION_BACKEND_DOCKER:
            result = SandboxExecResult(
                sandbox_id=sandbox_id,
                status=SANDBOX_STATUS_UNAVAILABLE,
                exit_code=None,
                stdout="",
                stderr=f"container isolation required, backend={backend}",
                duration_ms=0,
                reason_code=SANDBOX_CONTAINER_UNAVAILABLE,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                resource_usage={
                    "isolation_backend": backend,
                    "container_required": True,
                    "platform": os.name,
                },
            )
            self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
            return result

        if backend == ISOLATION_BACKEND_DOCKER:
            result = self._run_docker(
                code=code,
                sandbox_id=sandbox_id,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                timeout_s=timeout_s,
            )
        else:
            result = self._run_subprocess(
                code=code,
                sandbox_id=sandbox_id,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                timeout_s=timeout_s,
                backend=backend,
            )
        self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
        return result

    def _resolve_backend(self) -> str:
        backend = str(self.config.isolation_backend or ISOLATION_BACKEND_SUBPROCESS).strip().lower()
        if backend not in _SUPPORTED_ISOLATION_BACKENDS:
            backend = ISOLATION_BACKEND_SUBPROCESS
        if backend == ISOLATION_BACKEND_AUTO:
            if self._container_is_required():
                return ISOLATION_BACKEND_DOCKER
            return ISOLATION_BACKEND_SUBPROCESS
        return backend

    def _container_is_required(self) -> bool:
        if self.config.require_container:
            return True
        return _is_production_env()

    def _run_docker(
        self,
        *,
        code: str,
        sandbox_id: str,
        evidence_ref: str,
        code_digest: str,
        timeout_s: int,
    ) -> SandboxExecResult:
        from host_brain.airlock.docker_sandbox import is_docker_available, run_in_docker

        if not is_docker_available():
            if self._container_is_required():
                return SandboxExecResult(
                    sandbox_id=sandbox_id,
                    status=SANDBOX_STATUS_UNAVAILABLE,
                    exit_code=None,
                    stdout="",
                    stderr="docker backend required but unavailable",
                    duration_ms=0,
                    reason_code=SANDBOX_CONTAINER_UNAVAILABLE,
                    evidence_ref=evidence_ref,
                    code_digest=code_digest,
                    resource_usage={
                        "isolation_backend": ISOLATION_BACKEND_DOCKER,
                        "container_required": True,
                    },
                )

            fallback_result = self._run_subprocess(
                code=code,
                sandbox_id=sandbox_id,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
                timeout_s=timeout_s,
                backend=ISOLATION_BACKEND_SUBPROCESS,
            )
            return SandboxExecResult(
                sandbox_id=fallback_result.sandbox_id,
                status=fallback_result.status,
                exit_code=fallback_result.exit_code,
                stdout=fallback_result.stdout,
                stderr=fallback_result.stderr,
                duration_ms=fallback_result.duration_ms,
                reason_code=fallback_result.reason_code or SANDBOX_CONTAINER_UNAVAILABLE,
                evidence_ref=fallback_result.evidence_ref,
                code_digest=fallback_result.code_digest,
                resource_usage={
                    **fallback_result.resource_usage,
                    "isolation_backend": ISOLATION_BACKEND_SUBPROCESS,
                    "degraded_from": ISOLATION_BACKEND_DOCKER,
                },
                truncated_stdout=fallback_result.truncated_stdout,
                truncated_stderr=fallback_result.truncated_stderr,
            )

        return run_in_docker(
            code=code,
            sandbox_id=sandbox_id,
            evidence_ref=evidence_ref,
            code_digest=code_digest,
            timeout_s=timeout_s,
            max_stdout_bytes=self.config.max_stdout_bytes,
            max_stderr_bytes=self.config.max_stderr_bytes,
        )

    def _run_subprocess(
        self,
        *,
        code: str,
        sandbox_id: str,
        evidence_ref: str,
        code_digest: str,
        timeout_s: int,
        backend: str = ISOLATION_BACKEND_SUBPROCESS,
    ) -> SandboxExecResult:
        tmpdir = None
        proc: Optional[subprocess.Popen[bytes]] = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"sandbox_{sandbox_id}_")
            script_path = os.path.join(tmpdir, "sandbox_script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            env = os.environ.copy()
            if not self.config.allow_network:
                env["SANDBOX_NO_NETWORK"] = "1"
            if not self.config.allow_fs_write:
                env["SANDBOX_NO_FS_WRITE"] = "1"

            start_time = time.perf_counter()
            try:
                popen_kwargs: dict[str, Any] = {
                    "cwd": tmpdir,
                    "env": env,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                else:
                    popen_kwargs["start_new_session"] = True

                proc = subprocess.Popen(
                    [self.config.python_executable, script_path],
                    **popen_kwargs,
                )

                try:
                    raw_stdout, raw_stderr = proc.communicate(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    self._terminate_subprocess(proc)
                    duration_ms = int(round((time.perf_counter() - start_time) * 1000))
                    return SandboxExecResult(
                        sandbox_id=sandbox_id,
                        status=SANDBOX_STATUS_TIMEOUT,
                        exit_code=None,
                        stdout="",
                        stderr=f"execution timed out after {timeout_s}s",
                        duration_ms=duration_ms,
                        reason_code=SANDBOX_TIMEOUT,
                        evidence_ref=evidence_ref,
                        code_digest=code_digest,
                        resource_usage={
                            "timeout_s": timeout_s,
                            "timeout_enforcement": "popen_communicate",
                            "platform": os.name,
                            "isolation_backend": backend,
                        },
                    )
            except FileNotFoundError:
                duration_ms = int(round((time.perf_counter() - start_time) * 1000))
                return SandboxExecResult(
                    sandbox_id=sandbox_id,
                    status=SANDBOX_STATUS_UNAVAILABLE,
                    exit_code=None,
                    stdout="",
                    stderr=f"python executable not found: {self.config.python_executable}",
                    duration_ms=duration_ms,
                    reason_code=SANDBOX_UNAVAILABLE,
                    evidence_ref=evidence_ref,
                    code_digest=code_digest,
                )
            except Exception as exc:
                duration_ms = int(round((time.perf_counter() - start_time) * 1000))
                return SandboxExecResult(
                    sandbox_id=sandbox_id,
                    status=SANDBOX_STATUS_EXEC_FAILED,
                    exit_code=None,
                    stdout="",
                    stderr=str(exc),
                    duration_ms=duration_ms,
                    reason_code=SANDBOX_EXEC_FAILED,
                    evidence_ref=evidence_ref,
                    code_digest=code_digest,
                )

            duration_ms = int(round((time.perf_counter() - start_time) * 1000))

            raw_stdout = raw_stdout or b""
            raw_stderr = raw_stderr or b""
            stdout_text = raw_stdout.decode("utf-8", errors="replace")
            stderr_text = raw_stderr.decode("utf-8", errors="replace")
            stdout_text, trunc_out = _truncate(stdout_text, self.config.max_stdout_bytes)
            stderr_text, trunc_err = _truncate(stderr_text, self.config.max_stderr_bytes)

            resource_usage = {
                "stdout_bytes": len(raw_stdout),
                "stderr_bytes": len(raw_stderr),
                "timeout_s": timeout_s,
                "timeout_enforcement": "popen_communicate",
                "platform": os.name,
                "isolation_backend": backend,
            }

            return_code = int(proc.returncode or 0)
            if return_code != 0:
                return SandboxExecResult(
                    sandbox_id=sandbox_id,
                    status=SANDBOX_STATUS_EXEC_FAILED,
                    exit_code=return_code,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    duration_ms=duration_ms,
                    reason_code=SANDBOX_EXEC_FAILED,
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
                stderr=f"sandbox setup failed: {exc}",
                duration_ms=0,
                reason_code=SANDBOX_EXEC_FAILED,
                evidence_ref=evidence_ref,
                code_digest=code_digest,
            )
        finally:
            if tmpdir is not None:
                try:
                    import shutil

                    shutil.rmtree(tmpdir, ignore_errors=True)
                except Exception:
                    pass

    def _log_result(
        self,
        result: SandboxExecResult,
        *,
        request_id: Optional[str],
        intent_id: Optional[str],
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return

        self.ledger.log_sandbox_exec(
            {
                "request_id": request_id,
                "intent_id": intent_id,
                "sandbox_id": result.sandbox_id,
                "status": result.status,
                "exit_code": result.exit_code,
                "reason_code": result.reason_code,
                "evidence_ref": result.evidence_ref,
                "code_digest": result.code_digest,
                "stdout_preview": result.stdout[:256] if result.stdout else "",
                "stderr_preview": result.stderr[:256] if result.stderr else "",
                "duration_ms": result.duration_ms,
                "resource_usage": result.resource_usage,
                "truncated_stdout": int(result.truncated_stdout),
                "truncated_stderr": int(result.truncated_stderr),
                "ts_ms": ts_ms,
            }
        )

    @staticmethod
    def _terminate_subprocess(proc: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=2,
                )
            except Exception:
                pass
        else:
            try:
                os.killpg(proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except Exception:
                pass

        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.communicate(timeout=1)
        except Exception:
            pass
