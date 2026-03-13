"""Shell 命令执行 Skill 包（P1 优先级）。

提供本地 Shell 命令执行能力，支撑 PRD S2 场景（开发协作：读取日志、运行测试、执行命令）。

风险分级说明：
  risk_level=2  只读操作（shell_read_log）
  risk_level=3  受控执行（shell_run / shell_run_test）

安全机制：
  - shell_run 通过 allowlist 白名单限制可执行命令前缀
  - shell_read_log 仅允许读取日志文件，不执行任意命令
  - shell_run_test 仅允许执行测试框架命令（pytest / unittest）
  - 所有命令均有超时保护（timeout_ms）

所有 handler 均为轻量归一化层，返回标准化 payload，
由上层运行时（ShellExecutor / HostExecutor）负责实际执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 默认命令白名单前缀（运行时可通过配置覆盖）
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
    "tail", "head", "wc",
})


class ShellSkillProvider:
    """Shell 命令执行 Skill 集合。

    所有方法通过 @skill 装饰器注册元数据，
    由 build_shell_skill_registry() 统一扫描注册。
    """

    @skill(
        skill_name="shell_run",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="执行 Shell 命令（白名单限制），返回 stdout/stderr 和退出码。",
    )
    def shell_run(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """执行 Shell 命令。

        Args:
            command (str): 要执行的命令字符串（必填，需在白名单内）
            cwd (str): 工作目录（可选，默认当前目录）
            timeout_ms (int): 超时毫秒数（默认 30000，最大 300000）
            env (dict): 额外的环境变量（可选）
            capture_output (bool): 是否捕获输出（默认 True）
            allowlist (list): 自定义命令白名单前缀（可选，覆盖默认白名单）
        """
        command = str((args or {}).get("command") or "").strip()
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

        # 白名单校验（归一化层：只做参数整理，实际校验由运行时执行）
        allowlist_raw = (args or {}).get("allowlist")
        if isinstance(allowlist_raw, list):
            allowlist = [str(x).strip().lower() for x in allowlist_raw if x]
        else:
            allowlist = sorted(_DEFAULT_ALLOWLIST)

        # 提取命令前缀用于白名单预检（归一化层提前标记，运行时可二次校验）
        cmd_prefix = command.split()[0].lower() if command.split() else ""
        allowlist_set = set(allowlist) if allowlist else _DEFAULT_ALLOWLIST
        allowlist_ok = any(
            cmd_prefix == prefix or cmd_prefix.endswith(f"/{prefix}") or cmd_prefix.endswith(f"\\{prefix}")
            for prefix in allowlist_set
        )

        return {
            "status": "OK",
            "tool": "shell",
            "action": "run",
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

    @skill(
        skill_name="shell_read_log",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="读取日志文件末尾 N 行（只读，等价于 tail -n，不执行任意命令）。",
    )
    def shell_read_log(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """读取日志文件末尾 N 行。

        Args:
            path (str): 日志文件路径（必填）
            lines (int): 读取行数（默认 100，最大 5000）
            encoding (str): 文件编码（默认 utf-8）
            grep (str): 可选关键词过滤（只返回包含该关键词的行）
            level_filter (str): 日志级别过滤，如 ERROR / WARNING / INFO（可选）
        """
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
            "status": "OK",
            "tool": "shell",
            "action": "read_log",
            "path": path,
            "lines": lines,
            "encoding": encoding,
            "grep": grep,
            "level_filter": level_filter,
            "manifest_ref": f"shell_read_log::{path}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="shell_run_test",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="执行测试框架命令（pytest / unittest），返回测试结果摘要。",
    )
    def shell_run_test(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """执行测试命令。

        Args:
            framework (str): 测试框架，pytest / unittest（默认 pytest）
            path (str): 测试文件或目录路径（可选，默认当前目录）
            test_filter (str): 测试过滤表达式（pytest -k 参数，可选）
            cwd (str): 工作目录（可选）
            timeout_ms (int): 超时毫秒数（默认 120000，最大 600000）
            verbose (bool): 是否输出详细信息（默认 True）
            fail_fast (bool): 遇到第一个失败是否立即停止（默认 False）
        """
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

        # 构建归一化命令（运行时实际执行）
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
            "status": "OK",
            "tool": "shell",
            "action": "run_test",
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


def build_shell_skill_registry(provider: ShellSkillProvider | None = None) -> SkillRegistry:
    """构建包含 Shell 命令执行 Skills 的 SkillRegistry。

    Shell Skill 列表：
    - shell_run：执行 Shell 命令（risk=3，白名单限制）
    - shell_read_log：读取日志文件末尾 N 行（risk=2，只读）
    - shell_run_test：执行测试框架命令（risk=3，pytest/unittest）

    Returns:
        已注册 3 个 Shell Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or ShellSkillProvider())
    return registry
