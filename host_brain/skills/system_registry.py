"""系统感知 Skill 包。

声明式 Provider：新增系统探测类技能时，只需补一个 `DeclarativeSkillSpec`
和对应的参数归一化函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec


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
    include_runtime_vitals_raw = (args or {}).get("include_runtime_vitals")
    include_runtime_vitals = bool(include_runtime_vitals_raw) if include_runtime_vitals_raw is not None else True
    include_gateway_status_raw = (args or {}).get("include_gateway_status")
    include_gateway_status = bool(include_gateway_status_raw) if include_gateway_status_raw is not None else True

    return {
        "include_cpu": include_cpu,
        "include_memory": include_memory,
        "include_disk": include_disk,
        "include_network": include_network,
        "include_gpu": include_gpu,
        "include_runtime_vitals": include_runtime_vitals,
        "include_gateway_status": include_gateway_status,
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


def _normalize_system_resource_snapshot(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    include_gpu_raw = (args or {}).get("include_gpu")
    include_gpu = bool(include_gpu_raw) if include_gpu_raw is not None else True
    include_processes_raw = (args or {}).get("include_processes")
    include_processes = bool(include_processes_raw) if include_processes_raw is not None else True
    include_network_raw = (args or {}).get("include_network")
    include_network = bool(include_network_raw) if include_network_raw is not None else False
    try:
        top_n = max(1, min(50, int((args or {}).get("top_n") or 10)))
    except (TypeError, ValueError):
        top_n = 10
    try:
        sample_seconds = max(0.0, min(3.0, float((args or {}).get("sample_seconds") or 0.2)))
    except (TypeError, ValueError):
        sample_seconds = 0.2

    return {
        "include_gpu": include_gpu,
        "include_processes": include_processes,
        "include_network": include_network,
        "top_n": top_n,
        "sample_seconds": sample_seconds,
        "manifest_ref": "system_resource_snapshot::whole_system",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_process_resource_top(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    try:
        limit = max(1, min(100, int((args or {}).get("limit") or (args or {}).get("top_n") or 20)))
    except (TypeError, ValueError):
        limit = 20
    sort_by = str((args or {}).get("sort_by") or "memory").strip().lower()
    if sort_by not in {"cpu", "memory", "rss", "vram", "name", "pid"}:
        sort_by = "memory"
    include_gpu_raw = (args or {}).get("include_gpu")
    include_gpu = bool(include_gpu_raw) if include_gpu_raw is not None else True
    include_cmdline_raw = (args or {}).get("include_cmdline")
    include_cmdline = bool(include_cmdline_raw) if include_cmdline_raw is not None else False
    try:
        sample_seconds = max(0.0, min(3.0, float((args or {}).get("sample_seconds") or 0.2)))
    except (TypeError, ValueError):
        sample_seconds = 0.2

    return {
        "limit": limit,
        "sort_by": sort_by,
        "include_gpu": include_gpu,
        "include_cmdline": include_cmdline,
        "sample_seconds": sample_seconds,
        "manifest_ref": "process_resource_top::whole_system",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_resource_pressure_analysis(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    try:
        top_n = max(5, min(80, int((args or {}).get("top_n") or 40)))
    except (TypeError, ValueError):
        top_n = 40
    try:
        sample_seconds = max(0.0, min(3.0, float((args or {}).get("sample_seconds") or 0.5)))
    except (TypeError, ValueError):
        sample_seconds = 0.5
    persist_raw = (args or {}).get("persist")
    persist = bool(persist_raw) if persist_raw is not None else True

    return {
        "top_n": top_n,
        "sample_seconds": sample_seconds,
        "persist": persist,
        "manifest_ref": "resource_pressure_analysis::whole_system",
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


def _normalize_resource_release(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    mode = str((args or {}).get("mode") or "dry_run").strip().lower()
    if mode not in {"dry_run", "gc_collect", "trim_working_sets"}:
        mode = "dry_run"
    dry_run_raw = (args or {}).get("dry_run")
    dry_run = bool(dry_run_raw) if dry_run_raw is not None else mode == "dry_run"
    confirm_raw = (args or {}).get("confirm")
    confirm = bool(confirm_raw) if confirm_raw is not None else False
    try:
        min_memory_mb = max(128, min(32768, int((args or {}).get("min_memory_mb") or 512)))
    except (TypeError, ValueError):
        min_memory_mb = 512
    raw_pids = (args or {}).get("pids") or (args or {}).get("pid") or []
    if isinstance(raw_pids, (str, int)):
        raw_pids = [raw_pids]
    pids: list[int] = []
    for item in (raw_pids if isinstance(raw_pids, list) else []):
        try:
            pid = int(item)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            pids.append(pid)
    raw_names = (args or {}).get("process_names") or (args or {}).get("names") or (args or {}).get("name") or []
    if isinstance(raw_names, str):
        raw_names = [raw_names]
    process_names = [str(item or "").strip() for item in raw_names if str(item or "").strip()] if isinstance(raw_names, list) else []

    return {
        "mode": mode,
        "dry_run": dry_run,
        "confirm": confirm,
        "min_memory_mb": min_memory_mb,
        "pids": pids,
        "process_names": process_names,
        "manifest_ref": f"resource_release::{mode}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_resource_manager_status(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    create_raw = (args or {}).get("create_if_missing")
    create_if_missing = bool(create_raw) if create_raw is not None else False
    return {
        "create_if_missing": create_if_missing,
        "manifest_ref": "resource_manager_status::local",
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
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


def _normalize_open_app(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    app_name = str((args or {}).get("app_name") or (args or {}).get("target") or "").strip() or None
    app_id = str((args or {}).get("app_id") or "").strip() or None
    target_path = str((args or {}).get("path") or (args or {}).get("target_path") or "").strip() or None
    query = str((args or {}).get("query") or (args or {}).get("text") or (args or {}).get("user_message") or "").strip() or None

    return {
        "app_name": app_name,
        "app_id": app_id,
        "path": target_path,
        "query": query,
        "manifest_ref": f"open_app::{app_name or app_id or target_path or 'unknown'}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
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
            description="获取本地系统信息和运行体征诊断（Host / Edge / Gateway / QQ / ASR / TTS / VTS / 记忆 / 工具执行）。",
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
            skill_name="system_resource_snapshot",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="获取整台机器的资源管控快照（CPU/GPU、内存/显存、磁盘、热点进程、压力分级和释放建议）。",
            tool="system",
            action="resource_snapshot",
            normalize_args=_normalize_system_resource_snapshot,
            family="system",
            capability_tags=("os", "resources", "monitoring", "gpu"),
            parallelizable=True,
        ),
        DeclarativeSkillSpec(
            skill_name="process_resource_top",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="列出整台机器 CPU/内存/显存占用最高的进程，用于定位卡顿来源。",
            tool="system",
            action="process_resource_top",
            normalize_args=_normalize_process_resource_top,
            family="system",
            capability_tags=("os", "process", "resources", "gpu"),
            parallelizable=True,
        ),
        DeclarativeSkillSpec(
            skill_name="resource_pressure_analysis",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="分析整机内存/显存/CPU 同时拉高的根因，联合资源快照、GPU 上下文、Host/OBS 健康、资源管家和日志信号输出证据链。",
            tool="system",
            action="resource_pressure_analysis",
            normalize_args=_normalize_resource_pressure_analysis,
            family="system",
            capability_tags=("os", "resources", "monitoring", "gpu", "root_cause"),
            parallelizable=False,
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
            skill_name="resource_release",
            clearance="HIGH",
            risk_level=4,
            scope="system",
            reversible=False,
            require_confirm=True,
            description="执行安全资源释放动作（默认 dry-run；实际工作集回收需用户确认，不会自动强杀进程）。",
            tool="system",
            action="resource_release",
            normalize_args=_normalize_resource_release,
            family="system",
            capability_tags=("os", "resources", "cleanup"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="resource_manager_status",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="查询整机自动资源管家的运行状态、最近一次 LLM 分析和自动安全动作。",
            tool="system",
            action="resource_manager_status",
            normalize_args=_normalize_resource_manager_status,
            family="system",
            capability_tags=("os", "resources", "monitoring", "automation"),
            parallelizable=True,
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
        DeclarativeSkillSpec(
            skill_name="open_app",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=False,
            description="打开本地应用程序（优先解析开始菜单快捷方式、已知别名或显式路径）。",
            tool="system",
            action="open_app",
            normalize_args=_normalize_open_app,
            family="system",
            capability_tags=("os", "desktop", "launch"),
        ),
    )
