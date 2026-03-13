"""系统感知 Skill 包（P2 优先级）。

提供对本地系统状态的感知能力（系统信息、进程列表、网络状态），
支撑 PRD S2 场景（系统监控 + 环境感知）。

风险分级说明：
  risk_level=1  只读/感知操作（system_info / process_list / network_status）
  risk_level=3  写操作（process_kill），require_confirm=True

所有 handler 均为轻量归一化层，返回标准化 payload，
由 impl/system_executor.py 路由到系统 API 执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill


class SystemSkillProvider:
    """系统感知 Skill 集合。"""

    @skill(
        skill_name="system_info",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        description="获取本地系统信息（CPU / 内存 / 磁盘 / 操作系统版本 / 运行时长）。",
    )
    def system_info(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """获取系统信息。

        Args:
            include_cpu (bool): 是否包含 CPU 信息（默认 True）
            include_memory (bool): 是否包含内存信息（默认 True）
            include_disk (bool): 是否包含磁盘信息（默认 True）
            include_network (bool): 是否包含网络接口信息（默认 False）
            include_gpu (bool): 是否包含 GPU 信息（默认 False）
        """
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
            "status": "OK",
            "tool": "system",
            "action": "info",
            "include_cpu": include_cpu,
            "include_memory": include_memory,
            "include_disk": include_disk,
            "include_network": include_network,
            "include_gpu": include_gpu,
            "manifest_ref": "system_info::local",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="process_list",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        description="列出当前运行的进程列表（支持按名称/PID 过滤）。",
    )
    def process_list(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """列出运行中的进程。

        Args:
            name_filter (str): 按进程名过滤（可选，支持模糊匹配）
            pid_filter (int): 按 PID 精确过滤（可选）
            limit (int): 最多返回条数（默认 50）
            sort_by (str): 排序字段，cpu / memory / pid / name（默认 cpu）
        """
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
            "status": "OK",
            "tool": "system",
            "action": "process_list",
            "name_filter": name_filter,
            "pid_filter": pid_filter,
            "limit": limit,
            "sort_by": sort_by,
            "manifest_ref": "process_list::running",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="process_kill",
        clearance="HIGH",
        risk_level=4,
        scope="system",
        reversible=False,
        require_confirm=True,
        description="终止指定进程（不可逆，需用户确认；支持按 PID 或进程名终止）。",
    )
    def process_kill(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """终止进程（不可逆操作，需确认）。

        Args:
            pid (int): 要终止的进程 PID（与 name 二选一）
            name (str): 要终止的进程名（与 pid 二选一，终止所有匹配进程）
            force (bool): 是否强制终止（SIGKILL，默认 False，先尝试 SIGTERM）
            dry_run (bool): 仅模拟执行，不实际终止（默认 False）
        """
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
            "status": "OK",
            "tool": "system",
            "action": "process_kill",
            "pid": pid,
            "name": name,
            "force": force,
            "dry_run": dry_run,
            "manifest_ref": f"process_kill::{pid or name or 'unknown'}",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="network_status",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        description="检测网络连通性（ping 指定主机 / 检查端口 / 获取公网 IP）。",
    )
    def network_status(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """检测网络状态。

        Args:
            host (str): 要 ping 的主机（默认 8.8.8.8）
            port (int): 要检测的端口（可选）
            timeout_ms (int): 超时毫秒数（默认 3000）
            get_public_ip (bool): 是否获取公网 IP（默认 False）
        """
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
            "status": "OK",
            "tool": "system",
            "action": "network_status",
            "host": host,
            "port": port,
            "timeout_ms": timeout_ms,
            "get_public_ip": get_public_ip,
            "manifest_ref": f"network_status::{host}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_system_skill_registry(provider: SystemSkillProvider | None = None) -> SkillRegistry:
    """构建包含系统感知 Skills 的 SkillRegistry。

    System Skill 列表：
    - system_info：获取系统信息（risk=1，只读）
    - process_list：列出进程（risk=1，只读）
    - process_kill：终止进程（risk=4，不可逆，需确认）
    - network_status：检测网络状态（risk=1，只读）

    Returns:
        已注册 4 个系统感知 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or SystemSkillProvider())
    return registry
