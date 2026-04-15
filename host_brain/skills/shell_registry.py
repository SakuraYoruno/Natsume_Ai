"""Shell command skill pack."""

from __future__ import annotations

import re
from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry

_DEFAULT_ALLOWLIST = frozenset({
    "python", "python3", "pip", "pip3",
    "pytest", "unittest",
    "git", "ls", "dir", "cat", "type",
    "echo", "pwd", "cd",
    "node", "npm", "npx",
    "cargo", "go",
    "make", "cmake",
    "curl", "wget",
    "grep", "find", "rg",
    "netstat", "findstr",
    "tail", "head", "wc",
})

_PORT_RE = re.compile(r"(?:port|\u7aef\u53e3)\s*[:=]?\s*(\d{2,5})", re.IGNORECASE)
_PORT_INTENT_MARKERS = (
    "listen",
    "pid",
    "process",
    "\u76d1\u542c",  # 监听
    "\u8fdb\u7a0b",  # 进程
    "\u5360\u7528",  # 占用
)


def _coalesce_shell_text(args: dict[str, Any], ctx: dict[str, Any] | None) -> str:
    merged = dict(args or {})
    if isinstance(ctx, dict):
        merged.setdefault("task_text", ctx.get("task_text"))
        merged.setdefault("user_message", ctx.get("user_message"))
        merged.setdefault("query", ctx.get("query"))
    for key in ("command", "task", "task_text", "text", "user_message", "query"):
        candidate = str(merged.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _infer_shell_command_from_text(text: str) -> str:
    normalized = str(text or "").strip()
    lowered = normalized.lower()
    if not normalized:
        return ""

    if ("todo" in lowered) and ("python" in lowered or ".py" in lowered):
        return 'rg -n "TODO" -g "*.py" .'
    if "todo" in lowered:
        return 'rg -n "TODO" .'

    port_match = _PORT_RE.search(normalized)
    if port_match and any(token in lowered for token in _PORT_INTENT_MARKERS):
        port = port_match.group(1)
        return f"netstat -ano | findstr :{port}"

    return ""


def _normalize_shell_run(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    command = str((args or {}).get("command") or "").strip()
    if not command:
        command = _infer_shell_command_from_text(_coalesce_shell_text(args or {}, ctx))

    cwd = str((args or {}).get("cwd") or "").strip() or None
    try:
        timeout_ms = max(1000, min(300000, int((args or {}).get("timeout_ms") or 30000)))
    except (TypeError, ValueError):
        timeout_ms = 30000

    env = (args or {}).get("env")
    if not isinstance(env, dict):
        env = {}

    capture_output_raw = (args or {}).get("capture_output")
    capture_output = bool(capture_output_raw) if capture_output_raw is not None else True

    allowlist_raw = (args or {}).get("allowlist")
    if isinstance(allowlist_raw, list):
        allowlist = [str(x).strip().lower() for x in allowlist_raw if x]
    else:
        allowlist = sorted(_DEFAULT_ALLOWLIST)

    cmd_prefix = command.split()[0].lower() if command.split() else ""
    allowlist_set = set(allowlist) if allowlist else _DEFAULT_ALLOWLIST
    allowlist_ok = any(
        cmd_prefix == prefix or cmd_prefix.endswith(f"/{prefix}") or cmd_prefix.endswith(f"\\{prefix}")
        for prefix in allowlist_set
    )

    return {
        "command": command,
        "cwd": cwd,
        "timeout_ms": timeout_ms,
        "env": env,
        "capture_output": capture_output,
        "allowlist": allowlist,
        "allowlist_ok": allowlist_ok,
        "cmd_prefix": cmd_prefix,
        "manifest_ref": f"shell_run::{command[:80]}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_shell_read_log(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    try:
        lines = max(1, min(5000, int((args or {}).get("lines") or 100)))
    except (TypeError, ValueError):
        lines = 100

    encoding = str((args or {}).get("encoding") or "utf-8").strip() or "utf-8"
    grep = str((args or {}).get("grep") or "").strip() or None
    level_filter = str((args or {}).get("level_filter") or "").strip().upper() or None
    if level_filter and level_filter not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level_filter = None

    return {
        "path": path,
        "lines": lines,
        "encoding": encoding,
        "grep": grep,
        "level_filter": level_filter,
        "manifest_ref": f"shell_read_log::{path}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
    }


def _normalize_shell_run_test(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    framework = str((args or {}).get("framework") or "pytest").strip().lower()
    if framework not in {"pytest", "unittest"}:
        framework = "pytest"

    path = str((args or {}).get("path") or "").strip() or None
    test_filter = str((args or {}).get("test_filter") or "").strip() or None
    cwd = str((args or {}).get("cwd") or "").strip() or None
    try:
        timeout_ms = max(5000, min(600000, int((args or {}).get("timeout_ms") or 120000)))
    except (TypeError, ValueError):
        timeout_ms = 120000

    verbose_raw = (args or {}).get("verbose")
    verbose = bool(verbose_raw) if verbose_raw is not None else True
    fail_fast_raw = (args or {}).get("fail_fast")
    fail_fast = bool(fail_fast_raw) if fail_fast_raw is not None else False

    cmd_parts = [framework]
    if path:
        cmd_parts.append(path)
    if test_filter:
        cmd_parts.extend(["-k", test_filter])
    if verbose:
        cmd_parts.append("-v")
    if fail_fast:
        cmd_parts.append("-x")
    normalized_command = " ".join(cmd_parts)

    return {
        "framework": framework,
        "path": path,
        "test_filter": test_filter,
        "cwd": cwd,
        "timeout_ms": timeout_ms,
        "verbose": verbose,
        "fail_fast": fail_fast,
        "normalized_command": normalized_command,
        "manifest_ref": f"shell_run_test::{framework}::{path or '.'}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


class ShellSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="shell_run",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="Execute an allowlisted shell command and return stdout/stderr with exit code.",
            tool="shell",
            action="run",
            normalize_args=_normalize_shell_run,
        ),
        DeclarativeSkillSpec(
            skill_name="shell_read_log",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="Read the tail of a log file (read-only).",
            tool="shell",
            action="read_log",
            normalize_args=_normalize_shell_read_log,
        ),
        DeclarativeSkillSpec(
            skill_name="shell_run_test",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="Run test command (pytest/unittest) and return summary.",
            tool="shell",
            action="run_test",
            normalize_args=_normalize_shell_run_test,
        ),
    )


def build_shell_skill_registry(provider: ShellSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or ShellSkillProvider())
    return registry
