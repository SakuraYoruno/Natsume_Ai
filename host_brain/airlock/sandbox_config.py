"""Sandbox 配置和辅助函数。

从 sandbox.py 拆分，包含 SandboxConfig、SandboxExecResult 和辅助工具函数。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


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
    """已解析的沙箱资源与隔离配置；默认禁网、禁写，执行器再决定实际后端。"""

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


def load_sandbox_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> SandboxConfig:
    """从环境和局部覆盖构造受边界限制的配置，不修改全局环境。

    未知隔离后端回退受支持的 subprocess；高风险权限均显式默认关闭，调用方不能依赖
    缺失环境变量获得网络或文件写入能力。
    """
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    isolation_backend = str(
        merged.get("SANDBOX_ISOLATION_BACKEND", ISOLATION_BACKEND_SUBPROCESS)
    ).strip().lower()
    if isolation_backend not in _SUPPORTED_ISOLATION_BACKENDS:
        isolation_backend = ISOLATION_BACKEND_SUBPROCESS

    return SandboxConfig(
        timeout_s=_env_int(
            merged.get("SANDBOX_TIMEOUT_S"),
            default=DEFAULT_TIMEOUT_S,
            min_value=1,
            max_value=MAX_TIMEOUT_S,
        ),
        max_stdout_bytes=_env_int(
            merged.get("SANDBOX_MAX_STDOUT_BYTES"),
            default=DEFAULT_MAX_STDOUT_BYTES,
            min_value=1024,
            max_value=64 * 1024 * 1024,
        ),
        max_stderr_bytes=_env_int(
            merged.get("SANDBOX_MAX_STDERR_BYTES"),
            default=DEFAULT_MAX_STDERR_BYTES,
            min_value=1024,
            max_value=64 * 1024 * 1024,
        ),
        max_code_bytes=_env_int(
            merged.get("SANDBOX_MAX_CODE_BYTES"),
            default=DEFAULT_MAX_CODE_BYTES,
            min_value=1024,
            max_value=1024 * 1024,
        ),
        python_executable=str(merged.get("SANDBOX_PYTHON_EXECUTABLE") or "python").strip() or "python",
        allow_network=_env_bool(merged.get("SANDBOX_ALLOW_NETWORK"), default=False),
        allow_fs_write=_env_bool(merged.get("SANDBOX_ALLOW_FS_WRITE"), default=False),
        enabled=_env_bool(merged.get("SANDBOX_ENABLED"), default=True),
        isolation_backend=isolation_backend,
        require_container=_env_bool(merged.get("SANDBOX_REQUIRE_CONTAINER"), default=False),
    )


@dataclass(frozen=True)
class SandboxExecResult:
    """一次沙箱运行的可审计结果，代码以摘要关联而非在结果中重复保存。"""

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
        """导出结果快照；截断标记让调用方不会把不完整输出误作完整证据。"""
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
    """生成不承载业务语义的短期运行关联标识。"""
    return f"sbx_{uuid.uuid4().hex[:16]}"


def _digest_code(code: str) -> str:
    """计算待运行代码摘要，用于审计关联且不将代码文本复制到记录中。"""
    return f"sha256:{hashlib.sha256(code.encode('utf-8')).hexdigest()}"


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 字节上限截断输出，并返回截断事实供安全呈现层判断。"""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="replace")
    return truncated, True


def _env_bool(value: Any, *, default: bool) -> bool:
    """严格解析布尔环境值，歧义值回退调用方给定默认。"""
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    """解析整数并钳制资源界限，坏值回退安全默认。"""
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return int(default)
    return min(max(parsed, min_value), max_value)


def _is_production_env() -> bool:
    """判断当前进程是否为生产环境，供执行器收紧隔离后备策略。"""
    env = str(os.environ.get("Natsume_ENV", "")).strip().lower()
    return env in _PROD_ENV_VALUES
