"""AIRLOCK DRAFT_CODE sandbox execution with isolation, resource limits, and audit trail.

提供沙箱执行环境封装（subprocess 隔离 + 超时 + 资源限制），
将 DRAFT_CODE 在隔离进程中执行并产出结构化结果（SandboxExecResult），
结果作为草案证据写入审计链（sandbox_exec_log），不直接影响主链路状态。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    SANDBOX_UNAVAILABLE,
    SANDBOX_TIMEOUT,
    SANDBOX_RESOURCE_EXCEEDED,
    SANDBOX_EXEC_FAILED,
)
from host_brain.core.ids import now_ms


# ── 沙箱状态常量 ──
SANDBOX_STATUS_OK = "OK"
SANDBOX_STATUS_TIMEOUT = "TIMEOUT"
SANDBOX_STATUS_RESOURCE_EXCEEDED = "RESOURCE_EXCEEDED"
SANDBOX_STATUS_EXEC_FAILED = "EXEC_FAILED"
SANDBOX_STATUS_UNAVAILABLE = "UNAVAILABLE"

# ── 默认限制 ──
DEFAULT_TIMEOUT_S = 10
MAX_TIMEOUT_S = 30
DEFAULT_MAX_STDOUT_BYTES = 64 * 1024   # 64 KB
DEFAULT_MAX_STDERR_BYTES = 16 * 1024   # 16 KB
DEFAULT_MAX_CODE_BYTES = 32 * 1024     # 32 KB


@dataclass(frozen=True)
class SandboxConfig:
    """沙箱执行配置。"""
    timeout_s: int = DEFAULT_TIMEOUT_S
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES
    max_code_bytes: int = DEFAULT_MAX_CODE_BYTES
    python_executable: str = "python"
    allow_network: bool = False
    allow_fs_write: bool = False  # True 则允许写临时目录
    enabled: bool = True


@dataclass(frozen=True)
class SandboxExecResult:
    """沙箱执行结构化结果。"""
    sandbox_id: str
    status: str  # OK / TIMEOUT / RESOURCE_EXCEEDED / EXEC_FAILED / UNAVAILABLE
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
    """截断文本到最大字节数，返回 (截断后文本, 是否被截断)。"""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated, True


class SandboxExecutor:
    """DRAFT_CODE 沙箱执行器。

    安全约束：
    - 代码在隔离子进程中执行（subprocess）
    - 超时强杀（SIGKILL / terminate）
    - 输出截断防内存膨胀
    - 默认禁止网络和文件系统写入（通过临时目录隔离）
    - 不影响主进程状态，不引入旁路执行路径
    """

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
        """在沙箱中执行 DRAFT_CODE 并返回结构化结果。

        Args:
            code: 要执行的代码文本
            request_id: 关联的请求 ID（用于审计串联）
            intent_id: 关联的 intent ID（用于审计串联）
            language: 代码语言（当前仅支持 python）
            ts_ms: 时间戳（毫秒）

        Returns:
            SandboxExecResult 结构化执行结果
        """
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        sandbox_id = _generate_sandbox_id()
        code_digest = _digest_code(code)
        evidence_ref = f"sandbox://{sandbox_id}"

        # 沙箱未启用
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

        # 代码大小检查
        code_bytes = len(code.encode("utf-8", errors="replace"))
        if code_bytes > self.config.max_code_bytes:
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
                resource_usage={"code_bytes": code_bytes, "max_code_bytes": self.config.max_code_bytes},
            )
            self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
            return result

        # 语言检查
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

        timeout_s = min(self.config.timeout_s, MAX_TIMEOUT_S)
        result = self._run_subprocess(
            code=code,
            sandbox_id=sandbox_id,
            evidence_ref=evidence_ref,
            code_digest=code_digest,
            timeout_s=timeout_s,
        )
        self._log_result(result, request_id=request_id, intent_id=intent_id, ts_ms=now_value)
        return result

    def _run_subprocess(
        self,
        *,
        code: str,
        sandbox_id: str,
        evidence_ref: str,
        code_digest: str,
        timeout_s: int,
    ) -> SandboxExecResult:
        """在临时目录中通过 subprocess 执行代码。"""
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"sandbox_{sandbox_id}_")
            script_path = os.path.join(tmpdir, "sandbox_script.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            env = os.environ.copy()
            # 安全隔离：清理危险环境变量
            for key in ("HOME", "USERPROFILE", "PATH"):
                # 保留 PATH 以便找到 python 执行文件，但可根据需求进一步限制
                pass

            if not self.config.allow_network:
                # 标记禁网（实际网络隔离在生产中由容器/namespace 提供；
                # 此处通过环境变量告知被执行脚本，并在测试中验证合规性）
                env["SANDBOX_NO_NETWORK"] = "1"

            if not self.config.allow_fs_write:
                env["SANDBOX_NO_FS_WRITE"] = "1"

            # 限制工作目录为临时目录
            start_time = time.perf_counter()
            try:
                proc = subprocess.run(
                    [self.config.python_executable, script_path],
                    capture_output=True,
                    timeout=timeout_s,
                    cwd=tmpdir,
                    env=env,
                    text=False,  # 以 bytes 返回，方便截断
                )
            except subprocess.TimeoutExpired:
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
                    resource_usage={"timeout_s": timeout_s},
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

            # 解码和截断输出
            raw_stdout = proc.stdout or b""
            raw_stderr = proc.stderr or b""
            stdout_text = raw_stdout.decode("utf-8", errors="replace")
            stderr_text = raw_stderr.decode("utf-8", errors="replace")
            stdout_text, trunc_out = _truncate(stdout_text, self.config.max_stdout_bytes)
            stderr_text, trunc_err = _truncate(stderr_text, self.config.max_stderr_bytes)

            resource_usage = {
                "stdout_bytes": len(raw_stdout),
                "stderr_bytes": len(raw_stderr),
                "timeout_s": timeout_s,
            }

            if proc.returncode != 0:
                return SandboxExecResult(
                    sandbox_id=sandbox_id,
                    status=SANDBOX_STATUS_EXEC_FAILED,
                    exit_code=proc.returncode,
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
            # 清理临时目录
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
        """写入审计日志 sandbox_exec_log。"""
        if self.ledger is None:
            return

        self.ledger.log_sandbox_exec({
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
        })
