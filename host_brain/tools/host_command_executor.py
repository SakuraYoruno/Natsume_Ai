"""Host 本地命令执行引擎（Phase XII 阶段 1）。

职责：安全地在 Host 本地执行系统命令，分层安全审批。
设计原则：白名单命令 + 沙箱目录 + 安全分级 + 审计日志。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable

_logger = logging.getLogger("Natsume.host.command_executor")

# ---------------------------------------------------------------------------
# 安全级别
# ---------------------------------------------------------------------------
class SecurityLevel(IntEnum):
    L0_READONLY = 0       # 只读，直接执行
    L1_SAFE_WRITE = 1     # 白名单目录写，直接执行
    L2_MODERATE = 2       # 中风险，需确认
    L3_HIGH_RISK = 3      # 高风险，双重确认
    L4_FORBIDDEN = 4      # 禁止操作


# ---------------------------------------------------------------------------
# 命令白名单
# ---------------------------------------------------------------------------
COMMAND_SECURITY_MAP: dict[str, SecurityLevel] = {
    # L0 只读
    "dir": SecurityLevel.L0_READONLY,
    "ls": SecurityLevel.L0_READONLY,
    "type": SecurityLevel.L0_READONLY,
    "cat": SecurityLevel.L0_READONLY,
    "find": SecurityLevel.L0_READONLY,
    "where": SecurityLevel.L0_READONLY,
    "which": SecurityLevel.L0_READONLY,
    "wmic": SecurityLevel.L0_READONLY,
    "systeminfo": SecurityLevel.L0_READONLY,
    "tasklist": SecurityLevel.L0_READONLY,
    "netstat": SecurityLevel.L0_READONLY,
    "ipconfig": SecurityLevel.L0_READONLY,
    "hostname": SecurityLevel.L0_READONLY,
    "whoami": SecurityLevel.L0_READONLY,
    "tree": SecurityLevel.L0_READONLY,
    "ping": SecurityLevel.L0_READONLY,
    "tracert": SecurityLevel.L0_READONLY,
    "nslookup": SecurityLevel.L0_READONLY,
    # L1 安全写
    "7z": SecurityLevel.L1_SAFE_WRITE,
    "tar": SecurityLevel.L1_SAFE_WRITE,
    "unzip": SecurityLevel.L1_SAFE_WRITE,
    "copy": SecurityLevel.L1_SAFE_WRITE,
    "cp": SecurityLevel.L1_SAFE_WRITE,
    "xcopy": SecurityLevel.L1_SAFE_WRITE,
    "robocopy": SecurityLevel.L1_SAFE_WRITE,
    "ren": SecurityLevel.L1_SAFE_WRITE,
    "rename": SecurityLevel.L1_SAFE_WRITE,
    "mv": SecurityLevel.L1_SAFE_WRITE,
    "mkdir": SecurityLevel.L1_SAFE_WRITE,
    "md": SecurityLevel.L1_SAFE_WRITE,
    "git": SecurityLevel.L1_SAFE_WRITE,
    "npm": SecurityLevel.L1_SAFE_WRITE,
    "pip": SecurityLevel.L1_SAFE_WRITE,
    "python": SecurityLevel.L1_SAFE_WRITE,
    "node": SecurityLevel.L1_SAFE_WRITE,
    "curl": SecurityLevel.L1_SAFE_WRITE,
    "wget": SecurityLevel.L1_SAFE_WRITE,
    "docker": SecurityLevel.L1_SAFE_WRITE,
    # L2 中风险
    "del": SecurityLevel.L2_MODERATE,
    "rm": SecurityLevel.L2_MODERATE,
    "rmdir": SecurityLevel.L2_MODERATE,
    "rd": SecurityLevel.L2_MODERATE,
    "move": SecurityLevel.L2_MODERATE,
    "attrib": SecurityLevel.L2_MODERATE,
    "icacls": SecurityLevel.L2_MODERATE,
    "schtasks": SecurityLevel.L2_MODERATE,
    # L3 高风险
    "shutdown": SecurityLevel.L3_HIGH_RISK,
    "restart": SecurityLevel.L3_HIGH_RISK,
    "reboot": SecurityLevel.L3_HIGH_RISK,
    "reg": SecurityLevel.L3_HIGH_RISK,
    "regedit": SecurityLevel.L3_HIGH_RISK,
    "sc": SecurityLevel.L3_HIGH_RISK,
    "net": SecurityLevel.L3_HIGH_RISK,
    "netsh": SecurityLevel.L3_HIGH_RISK,
    "bcdedit": SecurityLevel.L3_HIGH_RISK,
    "diskpart": SecurityLevel.L3_HIGH_RISK,
}

# L4 禁止的模式
FORBIDDEN_PATTERNS: list[re.Pattern] = [
    re.compile(r"format\s+[a-zA-Z]:", re.IGNORECASE),
    re.compile(r"rm\s+(-rf|-r)\s+/\s*$", re.IGNORECASE),
    re.compile(r"del\s+/[sS]\s+/[qQ]\s+[A-Z]:\\", re.IGNORECASE),
    re.compile(r"rd\s+/[sS]\s+/[qQ]\s+[A-Z]:\\$", re.IGNORECASE),
    re.compile(r"cipher\s+/w:", re.IGNORECASE),
    re.compile(r"sfc\s+/scannow", re.IGNORECASE),
]

# 默认安全目录白名单
DEFAULT_SAFE_DIRECTORIES: list[str] = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Documents"),
    "D:\\",
    "E:\\",
    "F:\\",
]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class CommandResult:
    """命令执行结果。"""
    success: bool
    command: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: float = 0.0
    security_level: int = 0
    blocked: bool = False
    block_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "command": self.command[:200],
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout[:500] if self.stdout else "",
            "stderr_preview": self.stderr[:200] if self.stderr else "",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "security_level": self.security_level,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
        }


@dataclass
class ApprovalRequest:
    """需要用户确认的操作。"""
    command: str
    security_level: int
    description: str
    risk_summary: str
    affected_paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command[:200],
            "security_level": self.security_level,
            "description": self.description,
            "risk_summary": self.risk_summary,
            "affected_paths": self.affected_paths[:5],
        }


# ---------------------------------------------------------------------------
# HostCommandExecutor
# ---------------------------------------------------------------------------
class HostCommandExecutor:
    """Host 本地命令安全执行引擎。

    用法::

        executor = HostCommandExecutor()

        # 直接执行（L0/L1 自动通过）
        result = executor.execute("dir D:\\\\Downloads")

        # 需要审批（L2/L3）
        result = executor.execute("del old_file.txt")
        if result.blocked:
            # 发给用户确认
            approval = result.block_reason
    """

    def __init__(
        self,
        *,
        safe_directories: list[str] | None = None,
        command_map: dict[str, SecurityLevel] | None = None,
        max_timeout_s: float = 300.0,
        approval_callback: Callable[[ApprovalRequest], bool] | None = None,
        audit_logger: Callable[[dict], None] | None = None,
    ) -> None:
        self._safe_dirs = [
            Path(d).resolve() for d in (safe_directories or DEFAULT_SAFE_DIRECTORIES)
        ]
        self._cmd_map = dict(command_map or COMMAND_SECURITY_MAP)
        self._timeout = float(max_timeout_s)
        self._approval_cb = approval_callback
        self._audit_log = audit_logger

    def execute(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        force_approve: bool = False,
    ) -> CommandResult:
        """执行命令（带安全检查）。"""
        cmd = str(command or "").strip()
        if not cmd:
            return CommandResult(success=False, command=cmd, block_reason="empty_command", blocked=True)

        # 1. 禁止模式检查
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(cmd):
                result = CommandResult(
                    success=False, command=cmd, blocked=True,
                    security_level=SecurityLevel.L4_FORBIDDEN,
                    block_reason=f"FORBIDDEN: 匹配危险模式 {pattern.pattern}",
                )
                self._log_audit(cmd, result, "FORBIDDEN")
                return result

        # 2. 安全级别判定
        level = self._classify_security(cmd)

        # 3. 目录安全检查（L1+ 写操作需要在安全目录内）
        if level >= SecurityLevel.L1_SAFE_WRITE and cwd:
            if not self._is_safe_directory(cwd):
                result = CommandResult(
                    success=False, command=cmd, blocked=True,
                    security_level=level,
                    block_reason=f"工作目录 {cwd} 不在安全白名单内",
                )
                self._log_audit(cmd, result, "DIRECTORY_BLOCKED")
                return result

        # 4. 审批流程
        if level >= SecurityLevel.L2_MODERATE and not force_approve:
            approval = ApprovalRequest(
                command=cmd,
                security_level=level,
                description=self._describe_command(cmd),
                risk_summary=self._assess_risk(cmd, level),
                affected_paths=self._extract_paths(cmd),
            )
            if self._approval_cb is not None:
                approved = self._approval_cb(approval)
                if not approved:
                    result = CommandResult(
                        success=False, command=cmd, blocked=True,
                        security_level=level,
                        block_reason="用户拒绝执行",
                    )
                    self._log_audit(cmd, result, "USER_REJECTED")
                    return result
            else:
                # 无审批回调，L2+ 返回待审批
                result = CommandResult(
                    success=False, command=cmd, blocked=True,
                    security_level=level,
                    block_reason=f"需要用户确认（安全级别 L{level}）: {approval.risk_summary}",
                )
                self._log_audit(cmd, result, "PENDING_APPROVAL")
                return result

        # 5. 执行
        t0 = time.monotonic()
        timeout = timeout_s or self._timeout
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=cwd,
                env={**os.environ, **(env or {})},
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            elapsed = (time.monotonic() - t0) * 1000
            result = CommandResult(
                success=proc.returncode == 0,
                command=cmd,
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                elapsed_ms=elapsed,
                security_level=level,
            )
        except subprocess.TimeoutExpired:
            elapsed = (time.monotonic() - t0) * 1000
            result = CommandResult(
                success=False, command=cmd,
                exit_code=-1, stderr=f"超时（{timeout}s）",
                elapsed_ms=elapsed, security_level=level,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            result = CommandResult(
                success=False, command=cmd,
                exit_code=-2, stderr=str(exc),
                elapsed_ms=elapsed, security_level=level,
            )

        self._log_audit(cmd, result, "EXECUTED")
        return result

    def _classify_security(self, cmd: str) -> SecurityLevel:
        """判定命令安全级别。"""
        # 提取第一个词作为命令名
        first_word = cmd.split()[0].lower().strip('"').strip("'")
        # 去掉路径前缀
        base_name = Path(first_word).stem.lower() if "\\" in first_word or "/" in first_word else first_word
        level = self._cmd_map.get(base_name)
        if level is not None:
            return level
        # PowerShell cmdlet
        if base_name.startswith("get-"):
            return SecurityLevel.L0_READONLY
        if base_name.startswith(("set-", "new-", "add-")):
            return SecurityLevel.L2_MODERATE
        if base_name.startswith(("remove-", "stop-", "restart-")):
            return SecurityLevel.L3_HIGH_RISK
        # 未知命令默认 L2
        return SecurityLevel.L2_MODERATE

    def _is_safe_directory(self, path: str) -> bool:
        """检查路径是否在安全目录白名单内。"""
        try:
            resolved = Path(path).resolve()
            return any(
                resolved == safe_dir or safe_dir in resolved.parents
                for safe_dir in self._safe_dirs
            )
        except Exception:
            return False

    @staticmethod
    def _describe_command(cmd: str) -> str:
        """生成命令的人类可读描述。"""
        first = cmd.split()[0].lower()
        descriptions = {
            "del": "删除文件", "rm": "删除文件",
            "rmdir": "删除目录", "rd": "删除目录",
            "move": "移动文件", "shutdown": "关机",
            "restart": "重启", "reg": "修改注册表",
            "sc": "管理服务", "net": "网络操作",
        }
        return descriptions.get(first, f"执行命令: {cmd[:50]}")

    @staticmethod
    def _assess_risk(cmd: str, level: SecurityLevel) -> str:
        """评估风险摘要。"""
        if level == SecurityLevel.L2_MODERATE:
            return "此操作会修改文件系统，请确认目标路径正确"
        if level == SecurityLevel.L3_HIGH_RISK:
            return "⚠️ 此操作涉及系统级变更，可能影响系统稳定性"
        return "操作安全"

    @staticmethod
    def _extract_paths(cmd: str) -> list[str]:
        """从命令中提取文件路径。"""
        paths: list[str] = []
        for part in cmd.split():
            if "\\" in part or "/" in part:
                clean = part.strip('"').strip("'")
                if len(clean) > 2:
                    paths.append(clean)
        return paths[:5]

    def _log_audit(self, cmd: str, result: CommandResult, action: str) -> None:
        """记录审计日志。"""
        if self._audit_log:
            self._audit_log({
                "action": action,
                "command": cmd[:200],
                "security_level": result.security_level,
                "success": result.success,
                "blocked": result.blocked,
                "ts_ms": int(time.time() * 1000),
            })
        _logger.info(
            "HostCmd [%s] L%d: %s → success=%s",
            action, result.security_level, cmd[:80], result.success,
        )
