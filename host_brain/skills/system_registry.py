"""系统感知 Skill 包。

声明式 Provider：新增系统探测类技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_system_info(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    include_cpu_raw = (args or {}).get("include_cpu")
    include_cpu = bool(include_cpu_raw) if include_cpu_raw is not None else True
    include_memory_raw = (args or {}).get("include_memory")
    include_memory = bool(include_memory_raw) if include_memory_raw is not None else True
    include_disk_raw = (args or {}).get("include_disk")
    include_disk = bool(include_disk_raw) if include_disk_raw is not None else True
    include_network_raw = (args or {}).get("include_network")
    include_network = bool(include_network_raw) if include_network_raw is not None else False
    include_gpu_raw = (args or {}).get("include_gpu")
    include_gpu = bool(include_gpu_raw) if include_gpu_raw is not None else False

    return {
        "include_cpu": include_cpu,
        "include_memory": include_memory,
        "include_disk": include_disk,
        "include_network": include_network,
        "include_gpu": include_gpu,
        "manifest_ref": "system_info::local",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_process_list(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    name_filter = str((args or {}).get("name_filter") or "").strip() or None
    pid_raw = (args or {}).get("pid_filter")
    try:
        pid_filter = int(pid_raw) if pid_raw is not None else None
    except (TypeError, ValueError):
        pid_filter = None
    try:
        limit = max(1, min(500, int((args or {}).get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    sort_by = str((args or {}).get("sort_by") or "cpu").strip().lower()
    if sort_by not in {"cpu", "memory", "pid", "name"}:
        sort_by = "cpu"

    return {
        "name_filter": name_filter,
        "pid_filter": pid_filter,
        "limit": limit,
        "sort_by": sort_by,
        "manifest_ref": "process_list::running",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_process_kill(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    pid_raw = (args or {}).get("pid")
    try:
        pid = int(pid_raw) if pid_raw is not None else None
    except (TypeError, ValueError):
        pid = None
    name = str((args or {}).get("name") or "").strip() or None
    force_raw = (args or {}).get("force")
    force = bool(force_raw) if force_raw is not None else False
    dry_run_raw = (args or {}).get("dry_run")
    dry_run = bool(dry_run_raw) if dry_run_raw is not None else False

    return {
        "pid": pid,
        "name": name,
        "force": force,
        "dry_run": dry_run,
        "manifest_ref": f"process_kill::{pid or name or 'unknown'}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_network_status(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    host = str((args or {}).get("host") or "8.8.8.8").strip() or "8.8.8.8"
    port_raw = (args or {}).get("port")
    try:
        port = int(port_raw) if port_raw is not None else None
    except (TypeError, ValueError):
        port = None
    try:
        timeout_ms = max(500, min(30000, int((args or {}).get("timeout_ms") or 3000)))
    except (TypeError, ValueError):
        timeout_ms = 3000
    get_public_ip_raw = (args or {}).get("get_public_ip")
    get_public_ip = bool(get_public_ip_raw) if get_public_ip_raw is not None else False

    return {
        "host": host,
        "port": port,
        "timeout_ms": timeout_ms,
        "get_public_ip": get_public_ip,
        "manifest_ref": f"network_status::{host}",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


class SystemSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="system_info",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="获取本地系统信息（CPU / 内存 / 磁盘 / 操作系统版本 / 运行时长）。",
            tool="system",
            action="info",
            normalize_args=_normalize_system_info,
        ),
        DeclarativeSkillSpec(
            skill_name="process_list",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="列出当前运行的进程列表（支持按名称/PID 过滤）。",
            tool="system",
            action="process_list",
            normalize_args=_normalize_process_list,
        ),
        DeclarativeSkillSpec(
            skill_name="process_kill",
            clearance="HIGH",
            risk_level=4,
            scope="system",
            reversible=False,
            require_confirm=True,
            description="终止指定进程（不可逆，需用户确认；支持按 PID 或进程名终止）。",
            tool="system",
            action="process_kill",
            normalize_args=_normalize_process_kill,
        ),
        DeclarativeSkillSpec(
            skill_name="network_status",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="检测网络连通性（ping 指定主机 / 检查端口 / 获取公网 IP）。",
            tool="system",
            action="network_status",
            normalize_args=_normalize_network_status,
        ),
    )


def build_system_skill_registry(provider: SystemSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or SystemSkillProvider())
    return registry
