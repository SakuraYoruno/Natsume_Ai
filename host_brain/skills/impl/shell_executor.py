"""Shell 命令执行 Skill 执行层。

将 shell_registry.py 的归一化 payload 转化为真正的 subprocess 执行。
安全约束：
  - shell_run 强制白名单校验（allowlist_ok=False 时拒绝）
  - 所有命令均有超时保护
  - 输出截断至 MAX_OUTPUT_BYTES 防止内存爆炸
"""

from __future__ import annotations

import logging
import subprocess
from host_brain.core.subprocess_utils import run as _subprocess_run
from pathlib import Path
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"

# 最大输出字节数（防止超大输出）
MAX_OUTPUT_BYTES = 512 * 1024  # 512 KB

SHELL_EXEC_ERROR = "SHELL_EXEC_ERROR"
SHELL_ALLOWLIST_DENIED = "SHELL_ALLOWLIST_DENIED"
SHELL_TIMEOUT = "SHELL_TIMEOUT"
SHELL_PATH_MISSING = "SHELL_PATH_MISSING"


def _reject(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


def _truncate(text: str, max_bytes: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    """截断输出文本，返回 (截断后文本, 是否被截断)。"""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="replace"), True


def execute_shell_run(payload: dict[str, Any]) -> dict[str, Any]:
    """执行 Shell 命令（白名单限制）。"""
    ts = now_ms()
    event_id = new_event_id()
    command = payload.get("command") or ""
    cwd = payload.get("cwd") or None
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    env_extra = payload.get("env") or {}
    capture_output = bool(payload.get("capture_output", True))
    allowlist_ok = bool(payload.get("allowlist_ok", False))
    request_id = payload.get("request_id")

    if not command:
        return _reject(event_id=event_id, reason_code="SHELL_COMMAND_MISSING",
                       detail="command is required", ts_ms=ts, request_id=request_id)

    # 白名单安全门控
    if not allowlist_ok:
        return _reject(event_id=event_id, reason_code=SHELL_ALLOWLIST_DENIED,
                       detail=f"command prefix not in allowlist: {payload.get('cmd_prefix')!r}",
                       ts_ms=ts, request_id=request_id)

    # 构建环境变量
    import os
    env = dict(os.environ)
    if isinstance(env_extra, dict):
        env.update({str(k): str(v) for k, v in env_extra.items()})

    try:
        result = _subprocess_run(
            command,
            shell=True,
            cwd=cwd or None,
            env=env,
            capture_output=capture_output,
            timeout=timeout_ms / 1000.0,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout, stdout_truncated = _truncate(result.stdout or "")
        stderr, stderr_truncated = _truncate(result.stderr or "")

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "command": command,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "success": result.returncode == 0,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except subprocess.TimeoutExpired:
        logger.warning("shell_run timeout: %s", command)
        return _reject(event_id=event_id, reason_code=SHELL_TIMEOUT,
                       detail=f"command timed out after {timeout_ms}ms",
                       ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("shell_run failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=SHELL_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_shell_read_log(payload: dict[str, Any]) -> dict[str, Any]:
    """读取日志文件末尾 N 行（纯 Python 实现，不依赖 tail 命令）。"""
    ts = now_ms()
    event_id = new_event_id()
    path_str = payload.get("path") or ""
    lines_count = int(payload.get("lines") or 100)
    encoding = payload.get("encoding") or "utf-8"
    grep = payload.get("grep") or None
    level_filter = payload.get("level_filter") or None
    request_id = payload.get("session_id") or payload.get("request_id")

    if not path_str:
        return _reject(event_id=event_id, reason_code=SHELL_PATH_MISSING,
                       detail="path is required", ts_ms=ts, request_id=request_id)

    target = Path(path_str)
    if not target.exists():
        return _reject(event_id=event_id, reason_code="SHELL_FILE_NOT_FOUND",
                       detail=f"log file not found: {path_str}", ts_ms=ts, request_id=request_id)

    try:
        text = target.read_text(encoding=encoding, errors="replace")
        all_lines = text.splitlines()

        # 取末尾 N 行
        selected = all_lines[-lines_count:] if lines_count < len(all_lines) else all_lines

        # 关键词过滤
        if grep:
            selected = [l for l in selected if grep in l]

        # 日志级别过滤
        if level_filter:
            selected = [l for l in selected if level_filter in l]

        content = "\n".join(selected)
        content, truncated = _truncate(content)

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "path": str(target),
            "content": content,
            "returned_lines": len(selected),
            "total_lines": len(all_lines),
            "truncated": truncated,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("shell_read_log failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=SHELL_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_shell_run_test(payload: dict[str, Any]) -> dict[str, Any]:
    """执行测试框架命令（pytest / unittest）。"""
    ts = now_ms()
    event_id = new_event_id()
    normalized_command = payload.get("normalized_command") or ""
    cwd = payload.get("cwd") or None
    timeout_ms = int(payload.get("timeout_ms") or 120000)
    request_id = payload.get("request_id")

    if not normalized_command:
        return _reject(event_id=event_id, reason_code="SHELL_COMMAND_MISSING",
                       detail="normalized_command is required", ts_ms=ts, request_id=request_id)

    try:
        import os
        result = _subprocess_run(
            normalized_command,
            shell=True,
            cwd=cwd or None,
            env=dict(os.environ),
            capture_output=True,
            timeout=timeout_ms / 1000.0,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout, _ = _truncate(result.stdout or "")
        stderr, _ = _truncate(result.stderr or "")

        # 解析测试结果摘要（pytest 输出格式）
        summary_line = ""
        for line in reversed((stdout + stderr).splitlines()):
            if "passed" in line or "failed" in line or "error" in line:
                summary_line = line.strip()
                break

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref"),
            "command": normalized_command,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "success": result.returncode == 0,
            "summary": summary_line,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except subprocess.TimeoutExpired:
        return _reject(event_id=event_id, reason_code=SHELL_TIMEOUT,
                       detail=f"test timed out after {timeout_ms}ms",
                       ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("shell_run_test failed: %s", exc, exc_info=True)
        return _reject(event_id=event_id, reason_code=SHELL_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


# 统一分发入口
_ACTION_MAP = {
    "run": execute_shell_run,
    "read_log": execute_shell_read_log,
    "run_test": execute_shell_run_test,
}


def execute_shell_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """根据 payload.action 分发到对应的 Shell 执行函数。"""
    action = str(payload.get("action") or "").strip().lower()
    handler = _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "SHELL_UNKNOWN_ACTION",
            "detail": f"unknown shell action: {action!r}",
            "manifest_ref": None,
            "ts_ms": now_ms(),
        }
    return handler(payload)
