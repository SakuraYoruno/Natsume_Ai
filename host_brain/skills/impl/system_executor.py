"""系统感知 Executor。

将 system_registry.py 归一化层的 payload 路由到系统 API 执行。

支持的 action：
  - info：获取系统信息
  - resource_snapshot：获取整机资源管控快照
  - process_resource_top：列出 CPU/内存/显存热点进程
  - resource_manager_status：查询自动资源管家状态
  - resource_release：安全释放资源（默认 dry-run）
  - process_list：列出进程
  - process_kill：终止进程
  - network_status：检测网络状态
  - open_app：打开本地应用
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from host_brain.core.subprocess_utils import run as _subprocess_run
import time
import urllib.request
from typing import Any, Callable, Mapping

_logger = logging.getLogger("Natsume.skills.system_executor")
_START_MENU_DIRS = (
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
)
_KNOWN_APP_ALIASES: dict[str, dict[str, Any]] = {
    "酷狗音乐": {"search_terms": ("酷狗音乐", "酷狗", "kugou"), "app_id": "kugou"},
    "酷狗": {"search_terms": ("酷狗音乐", "酷狗", "kugou"), "app_id": "kugou"},
    "kugou": {"search_terms": ("酷狗音乐", "酷狗", "kugou"), "app_id": "kugou"},
}
_RESOURCE_ANALYSIS_PATH = Path(__file__).resolve().parents[2] / "data" / "resource_pressure_analysis.json"
_RESOURCE_ANALYSIS_JSONL_PATH = Path(__file__).resolve().parents[2] / "data" / "resource_pressure_analysis.jsonl"


def execute_system_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行系统感知操作。

    Args:
        args: 归一化 payload（来自 system_registry handler 的返回值）
        context: 执行上下文

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"system::{action}")

    try:
        result = _dispatch_system_action(action, payload, context)
        success = not (isinstance(result, dict) and result.get("success") is False)
        reason_code = str(result.get("reason_code") or "").strip() if isinstance(result, dict) else ""
        return {
            "status": "OK" if success else "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
            "reason_code": reason_code or None,
        }
    except Exception as exc:
        _logger.warning("System executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_system_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的系统操作。"""
    _action_map = {
        "info": _execute_system_info,
        "resource_snapshot": _execute_system_resource_snapshot,
        "resource_pressure_analysis": _execute_resource_pressure_analysis,
        "process_resource_top": _execute_process_resource_top,
        "resource_manager_status": _execute_resource_manager_status,
        "resource_release": _execute_resource_release,
        "process_list": _execute_process_list,
        "process_kill": _execute_process_kill,
        "network_status": _execute_network_status,
        "open_app": _execute_open_app,
    }
    handler = _action_map.get(action)
    if handler is None:
        return {"note": f"unknown system action: {action}"}
    return handler(payload, context)


def _execute_system_info(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """获取系统信息。"""
    include_cpu = bool(payload.get("include_cpu", True))
    include_memory = bool(payload.get("include_memory", True))
    include_disk = bool(payload.get("include_disk", True))
    include_network = bool(payload.get("include_network", False))
    include_gpu = bool(payload.get("include_gpu", False))
    include_runtime_vitals = bool(payload.get("include_runtime_vitals", True))
    include_gateway_status = bool(payload.get("include_gateway_status", True))

    result: dict[str, Any] = {}
    try:
        import psutil
        import platform

        result["platform"] = {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        }
        if include_cpu:
            result["cpu"] = {
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
                "percent": psutil.cpu_percent(interval=0.1),
                "freq_mhz": getattr(psutil.cpu_freq(), "current", None),
            }
        if include_memory:
            mem = psutil.virtual_memory()
            result["memory"] = {
                "total_gb": round(mem.total / 1024 ** 3, 2),
                "available_gb": round(mem.available / 1024 ** 3, 2),
                "percent": mem.percent,
            }
        if include_disk:
            disk = psutil.disk_usage("/")
            result["disk"] = {
                "total_gb": round(disk.total / 1024 ** 3, 2),
                "free_gb": round(disk.free / 1024 ** 3, 2),
                "percent": disk.percent,
            }
        if include_network:
            net = psutil.net_if_stats()
            result["network_interfaces"] = {
                name: {"isup": stats.isup, "speed": stats.speed}
                for name, stats in net.items()
            }
        if include_gpu:
            result["gpu"] = _collect_nvidia_gpu_snapshot()
    except ImportError:
        result["note"] = "psutil 未安装，系统信息不可用"
    except Exception as exc:
        result["error"] = str(exc)

    if include_runtime_vitals:
        result["runtime_vitals"] = _build_runtime_vitals(
            resource_snapshot=result,
            include_gateway_status=include_gateway_status,
            context=context,
        )

    _logger.info("系统信息获取完成")
    return result


def _build_runtime_vitals(
    *,
    resource_snapshot: Mapping[str, Any],
    include_gateway_status: bool,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _ensure_runtime_env_loaded()
    ts_ms = int(time.time() * 1000)
    edge_probe = _collect_edge_rpc_health_snapshot(context=context)
    service_health = _build_service_health_snapshot(
        edge_probe=edge_probe,
        ts_ms=ts_ms,
        context=context,
    )
    edge_components = _extract_edge_component_statuses(edge_probe)
    gateway = _collect_gateway_runtime_status() if include_gateway_status else _gateway_status_skipped()
    resources = _assess_resource_pressure(resource_snapshot)
    capabilities = _build_capability_matrix(
        service_health=service_health,
        edge_components=edge_components,
        gateway=gateway,
        resources=resources,
    )
    issues = _collect_runtime_issues(
        service_health=service_health,
        edge_components=edge_components,
        gateway=gateway,
        resources=resources,
    )
    recommendations = _build_runtime_recommendations(issues=issues, capabilities=capabilities)
    severity_rank = {"critical": 3, "warning": 2, "info": 1}
    max_severity = max((severity_rank.get(str(row.get("severity") or ""), 0) for row in issues), default=0)
    if max_severity >= 3:
        overall_status = "ACTION_REQUIRED"
    elif max_severity >= 2:
        overall_status = "DEGRADED"
    else:
        overall_status = "READY"
    if str(service_health.get("overall_status") or "").upper() == "DEGRADED" and overall_status == "READY":
        overall_status = "DEGRADED"

    return {
        "status": overall_status,
        "headline": _build_runtime_headline(overall_status, issues),
        "ts_ms": ts_ms,
        "resource_pressure": resources,
        "service_health": {
            "overall_status": service_health.get("overall_status"),
            "required_unavailable": service_health.get("required_unavailable"),
            "optional_unavailable": service_health.get("optional_unavailable"),
            "services": list(service_health.get("services") or []),
        },
        "edge_components": edge_components,
        "gateway": gateway,
        "capabilities": capabilities,
        "issues": issues,
        "recommendations": recommendations,
    }


def _run_async_sync(factory: Callable[[], Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: Any = None
    error: Exception | None = None

    def _runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(factory())
        except Exception as exc:
            error = exc

    import threading

    thread = threading.Thread(target=_runner, name="system-vitals-edge-probe", daemon=True)
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result


_RUNTIME_ENV_LOADED = False


def _ensure_runtime_env_loaded() -> None:
    global _RUNTIME_ENV_LOADED
    if _RUNTIME_ENV_LOADED:
        return
    _RUNTIME_ENV_LOADED = True
    runtime_env_raw = str(os.environ.get("HOST_RUNTIME_ENV_FILE") or "").strip()
    if runtime_env_raw:
        env_path = Path(runtime_env_raw)
    else:
        env_path = Path(__file__).resolve().parents[3] / "runtime" / "host" / "host.runtime.env"
    if not env_path.is_absolute():
        env_path = Path(__file__).resolve().parents[3] / env_path
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()
    except Exception as exc:
        _logger.debug("runtime env lazy load skipped: %s", exc)


def _collect_edge_rpc_health_snapshot(
    *, context: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    try:
        probe_provider = (context or {}).get("edge_rpc_health_probe")
        if not callable(probe_provider):
            raise RuntimeError("EDGE_RPC_HEALTH_PROBE_NOT_COMPOSED")
        probe = _run_async_sync(probe_provider)
        return dict(probe or {}) if isinstance(probe, Mapping) else {}
    except Exception as exc:
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"edge rpc health probe failed: {exc.__class__.__name__}: {exc}",
            "event": {},
        }


def _build_service_health_snapshot(
    *,
    edge_probe: Mapping[str, Any],
    ts_ms: int,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        builder = (context or {}).get("service_health_snapshot_builder")
        if not callable(builder):
            raise RuntimeError("SERVICE_HEALTH_SNAPSHOT_BUILDER_NOT_COMPOSED")
        report = builder(edge_probe=dict(edge_probe), ts_ms=ts_ms)
        summary = dict(report.get("summary") or {})
        return {
            "overall_status": summary.get("overall_status"),
            "required_unavailable": int(summary.get("required_unavailable") or 0),
            "optional_unavailable": int(summary.get("optional_unavailable") or 0),
            "services": list(report.get("services") or []),
            "reason_code": report.get("reason_code"),
        }
    except Exception as exc:
        return {
            "overall_status": "DEGRADED",
            "required_unavailable": 1,
            "optional_unavailable": 0,
            "services": [
                {
                    "service_id": "host.service_health",
                    "owner": "host",
                    "required": True,
                    "status": "DOWN",
                    "reason_code": "SERVICE_HEALTH_PROBE_FAILED",
                    "detail": f"{exc.__class__.__name__}: {exc}",
                }
            ],
            "reason_code": "SERVICE_HEALTH_PROBE_FAILED",
        }


def _extract_edge_component_statuses(edge_probe: Mapping[str, Any]) -> dict[str, Any]:
    event = edge_probe.get("event") if isinstance(edge_probe, Mapping) else {}
    event_payload = event if isinstance(event, Mapping) else {}
    components = event_payload.get("components") if isinstance(event_payload.get("components"), Mapping) else {}
    selected = ("asr", "tts", "tts_playback_device", "vts", "voice_pipeline", "resource_gate")
    out: dict[str, Any] = {}
    for name in selected:
        component = components.get(name) if isinstance(components, Mapping) else None
        if not isinstance(component, Mapping):
            out[name] = {"status": "UNKNOWN", "enabled": None, "available": None, "detail": "component not reported"}
            continue
        enabled = bool(component.get("enabled", True))
        available = bool(component.get("available", False))
        raw_status = str(component.get("status") or "").strip().lower()
        health = component.get("health") if isinstance(component.get("health"), Mapping) else {}
        health_state = str(health.get("state") or "").strip().upper()
        status = "UP"
        if not enabled:
            status = "DISABLED"
        elif (not available) or raw_status in {"down", "error", "failed"} or health_state in {"ERROR", "FAILED"}:
            status = "DOWN"
        elif raw_status and raw_status not in {"ok", "up", "ready"}:
            status = "DEGRADED"
        detail = _first_nonempty(
            component.get("detail"),
            health.get("connection_error"),
            health.get("load_error"),
            health.get("last_synth_error"),
            "component healthy" if status == "UP" else "",
        )
        out[name] = {
            "status": status,
            "enabled": enabled,
            "available": available,
            "detail": detail,
            "health_state": health_state or None,
            "reason_code": _edge_component_reason_code(name, status, health),
        }
    return out


def _edge_component_reason_code(name: str, status: str, health: Mapping[str, Any]) -> str | None:
    if status == "UP":
        return None
    if status == "DISABLED":
        return f"{name.upper()}_DISABLED_BY_CONFIG"
    connection_error = str(health.get("connection_error") or "").strip()
    if connection_error:
        return f"{name.upper()}_CONNECTION_FAILED"
    load_error = str(health.get("load_error") or health.get("last_synth_error") or "").strip()
    if load_error:
        return f"{name.upper()}_RUNTIME_ERROR"
    return f"{name.upper()}_{status}"


def _collect_gateway_runtime_status() -> dict[str, Any]:
    base_url = _resolve_gateway_base_url()
    if not base_url:
        return {
            "status": "UNKNOWN",
            "base_url": "",
            "detail": "gateway base url not configured",
            "qq_onebot": {"status": "UNKNOWN", "detail": "not checked"},
        }
    try:
        stats = _fetch_json_url(f"{base_url.rstrip('/')}/v1/gateway/stats", timeout_s=5.0)
    except Exception as exc:
        return {
            "status": "DOWN",
            "base_url": base_url,
            "detail": f"gateway stats unreachable: {exc.__class__.__name__}: {exc}",
            "qq_onebot": {"status": "UNKNOWN", "detail": "gateway unreachable"},
        }

    adapters = stats.get("adapters") if isinstance(stats.get("adapters"), Mapping) else {}
    qq = adapters.get("qq") if isinstance(adapters.get("qq"), Mapping) else {}
    onebot = qq.get("onebot_status") if isinstance(qq.get("onebot_status"), Mapping) else {}
    onebot_data = onebot.get("data") if isinstance(onebot.get("data"), Mapping) else {}
    qq_adapter_status = str(qq.get("status") or "").lower()
    qq_good = bool(onebot_data.get("good")) or bool(onebot_data.get("online"))
    qq_status = (
        "UP"
        if qq_adapter_status == "ok" and str(onebot.get("status") or "").lower() == "ok" and qq_good
        else "DOWN"
    )
    outbound = stats.get("outbound") if isinstance(stats.get("outbound"), Mapping) else {}
    router = stats.get("router") if isinstance(stats.get("router"), Mapping) else {}
    gateway_status = "UP"
    return {
        "status": gateway_status,
        "base_url": base_url,
        "detail": "gateway stats reachable",
        "router": {
            "routed": int(router.get("routed") or 0),
            "failed": int(router.get("failed") or 0),
            "cached": int(router.get("cached") or 0),
        },
        "outbound": {
            "dispatched": int(outbound.get("dispatched") or 0),
            "failed": int(outbound.get("failed") or 0),
            "no_adapter": int(outbound.get("no_adapter") or 0),
        },
        "qq_onebot": {
            "status": qq_status,
            "self_id": str(qq.get("self_id") or "").strip() or None,
            "online": bool(onebot_data.get("online")),
            "good": bool(onebot_data.get("good")),
            "detail": (
                str(onebot.get("message") or onebot.get("wording") or qq.get("detail") or "").strip()
                or ("onebot reachable" if qq_status == "UP" else "onebot health unavailable")
            ),
        },
        "last_inbound": dict(qq.get("last_inbound") or {}) if isinstance(qq.get("last_inbound"), Mapping) else {},
        "last_outbound": dict(qq.get("last_outbound") or {}) if isinstance(qq.get("last_outbound"), Mapping) else {},
    }


def _gateway_status_skipped() -> dict[str, Any]:
    return {
        "status": "SKIPPED",
        "base_url": "",
        "detail": "gateway status probe skipped",
        "qq_onebot": {"status": "SKIPPED", "detail": "not requested"},
    }


def _resolve_gateway_base_url() -> str:
    for key in ("HOST_EDGE_GATEWAY_BASE_URL", "EDGE_GATEWAY_BASE_URL", "GATEWAY_BASE_URL"):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    host = str(os.environ.get("EDGE_RPC_HOST") or os.environ.get("EDGE_GATEWAY_HOST") or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = str(os.environ.get("EDGE_GATEWAY_PORT") or "19000").strip() or "19000"
    return f"http://{host}:{port}"


def _fetch_json_url(url: str, *, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body or "{}")
    return dict(parsed or {}) if isinstance(parsed, Mapping) else {}


def _assess_resource_pressure(resource_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    cpu = resource_snapshot.get("cpu") if isinstance(resource_snapshot.get("cpu"), Mapping) else {}
    memory = resource_snapshot.get("memory") if isinstance(resource_snapshot.get("memory"), Mapping) else {}
    disk = resource_snapshot.get("disk") if isinstance(resource_snapshot.get("disk"), Mapping) else {}
    gpu = resource_snapshot.get("gpu") if isinstance(resource_snapshot.get("gpu"), Mapping) else {}
    gpu_summary = gpu.get("summary") if isinstance(gpu.get("summary"), Mapping) else {}
    cpu_percent = _to_float(cpu.get("percent"))
    memory_percent = _to_float(memory.get("percent"))
    disk_percent = _to_float(disk.get("percent"))
    gpu_percent = _to_float(gpu_summary.get("utilization_percent") or gpu_summary.get("gpu_utilization"))
    vram_percent = _to_float(gpu_summary.get("memory_percent") or gpu_summary.get("vram_percent"))
    status = "NORMAL"
    reasons: list[str] = []
    if cpu_percent is not None and cpu_percent >= 90:
        status = "HIGH"
        reasons.append("cpu_high")
    elif cpu_percent is not None and cpu_percent >= 75:
        if status == "NORMAL":
            status = "ELEVATED"
        reasons.append("cpu_elevated")
    if memory_percent is not None and memory_percent >= 88:
        status = "HIGH"
        reasons.append("memory_high")
    elif memory_percent is not None and memory_percent >= 78:
        if status == "NORMAL":
            status = "ELEVATED"
        reasons.append("memory_elevated")
    if disk_percent is not None and disk_percent >= 92:
        status = "HIGH"
        reasons.append("disk_high")
    elif disk_percent is not None and disk_percent >= 85:
        if status == "NORMAL":
            status = "ELEVATED"
        reasons.append("disk_elevated")
    if vram_percent is not None and vram_percent >= 88:
        status = "HIGH"
        reasons.append("vram_high")
    elif vram_percent is not None and vram_percent >= 75:
        if status == "NORMAL":
            status = "ELEVATED"
        reasons.append("vram_elevated")
    if gpu_percent is not None and gpu_percent >= 95:
        status = "HIGH"
        reasons.append("gpu_high")
    elif gpu_percent is not None and gpu_percent >= 80:
        if status == "NORMAL":
            status = "ELEVATED"
        reasons.append("gpu_elevated")
    return {
        "status": status,
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "disk_percent": disk_percent,
        "gpu_percent": gpu_percent,
        "vram_percent": vram_percent,
        "reasons": reasons,
    }


def _augment_pressure_with_process_hotspots(
    pressure: Mapping[str, Any],
    processes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Treat protected Natsume runtime CPU saturation as pressure.

    Whole-machine CPU can still look moderate on a many-core desktop while the
    Host/OBS Python processes are burning full cores and starving response
    paths. Those processes stay protected from release, but they should still
    throttle background work.
    """

    out = dict(pressure or {})
    reasons = [str(item) for item in list(out.get("reasons") or []) if str(item).strip()]
    runtime_rows = [
        row for row in processes
        if str(row.get("role") or "").strip() == "natsume_runtime"
    ]
    runtime_cpu_total = round(sum(float(row.get("cpu_percent") or 0.0) for row in runtime_rows), 1)
    runtime_cpu_max = round(max((float(row.get("cpu_percent") or 0.0) for row in runtime_rows), default=0.0), 1)
    out["natsume_runtime_cpu_percent"] = runtime_cpu_total
    out["natsume_runtime_process_count"] = len(runtime_rows)
    if runtime_cpu_total >= 160.0 or (runtime_cpu_total >= 120.0 and runtime_cpu_max >= 75.0):
        out["status"] = "HIGH"
        if "natsume_runtime_cpu_high" not in reasons:
            reasons.append("natsume_runtime_cpu_high")
    elif runtime_cpu_total >= 90.0 or runtime_cpu_max >= 75.0:
        if str(out.get("status") or "NORMAL").upper() == "NORMAL":
            out["status"] = "ELEVATED"
        if "natsume_runtime_cpu_elevated" not in reasons:
            reasons.append("natsume_runtime_cpu_elevated")
    out["reasons"] = reasons
    return out


def _build_capability_matrix(
    *,
    service_health: Mapping[str, Any],
    edge_components: Mapping[str, Any],
    gateway: Mapping[str, Any],
    resources: Mapping[str, Any],
) -> dict[str, Any]:
    service_map = {
        str(row.get("service_id") or ""): dict(row)
        for row in list(service_health.get("services") or [])
        if isinstance(row, Mapping)
    }

    def _service_ready(service_id: str) -> bool:
        return str(service_map.get(service_id, {}).get("status") or "").upper() == "UP"

    tts_status = str(_mapping(edge_components.get("tts")).get("status") or "UNKNOWN").upper()
    vts_status = str(_mapping(edge_components.get("vts")).get("status") or "UNKNOWN").upper()
    qq_status = str(_mapping(gateway.get("qq_onebot")).get("status") or "UNKNOWN").upper()
    gateway_status = str(gateway.get("status") or "UNKNOWN").upper()
    resource_status = str(resources.get("status") or "UNKNOWN").upper()
    voice_status = "READY" if tts_status == "UP" else "DEGRADED"
    avatar_status = "READY" if vts_status == "UP" else ("DISABLED" if vts_status == "DISABLED" else "DEGRADED")
    heavy_task_status = "DEFER" if resource_status == "HIGH" else ("CAUTION" if resource_status == "ELEVATED" else "READY")
    return {
        "chat": {"status": "READY" if int(service_health.get("required_unavailable") or 0) == 0 else "DEGRADED"},
        "qq_communication": {"status": "READY" if gateway_status == "UP" and qq_status == "UP" else "DEGRADED"},
        "tool_execution": {"status": "READY" if _service_ready("host.skills.loader") else "DEGRADED"},
        "memory_recall": {"status": "READY" if _service_ready("host.retrieval.recall_fusion") else "DEGRADED"},
        "voice_tts": {"status": voice_status},
        "avatar_vts": {"status": avatar_status},
        "heavy_tasks": {"status": heavy_task_status},
    }


def _collect_runtime_issues(
    *,
    service_health: Mapping[str, Any],
    edge_components: Mapping[str, Any],
    gateway: Mapping[str, Any],
    resources: Mapping[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in list(service_health.get("services") or []):
        if not isinstance(row, Mapping):
            continue
        if str(row.get("status") or "").upper() == "UP":
            continue
        required = bool(row.get("required", True))
        issues.append({
            "severity": "critical" if required else "warning",
            "component": str(row.get("service_id") or "service"),
            "status": str(row.get("status") or "UNKNOWN"),
            "reason_code": row.get("reason_code"),
            "detail": str(row.get("detail") or "").strip(),
            "action": _recommended_action_for_component(str(row.get("service_id") or ""), row.get("reason_code")),
        })
    for name, component in edge_components.items():
        data = _mapping(component)
        status = str(data.get("status") or "").upper()
        if status in {"UP", "UNKNOWN"}:
            continue
        severity = "info" if status == "DISABLED" else "warning"
        if name == "vts" and status == "DOWN":
            severity = "warning"
        issues.append({
            "severity": severity,
            "component": f"edge.{name}",
            "status": status,
            "reason_code": data.get("reason_code"),
            "detail": str(data.get("detail") or "").strip(),
            "action": _recommended_action_for_component(f"edge.{name}", data.get("reason_code")),
        })
    gateway_status = str(gateway.get("status") or "").upper()
    if gateway_status not in {"UP", "SKIPPED"}:
        issues.append({
            "severity": "critical",
            "component": "edge.gateway",
            "status": gateway_status or "UNKNOWN",
            "reason_code": "GATEWAY_UNAVAILABLE",
            "detail": str(gateway.get("detail") or "").strip(),
            "action": "检查 Edge Gateway 进程和 19000 端口。",
        })
    qq_onebot = _mapping(gateway.get("qq_onebot"))
    qq_status = str(qq_onebot.get("status") or "").upper()
    if qq_status not in {"UP", "SKIPPED", "UNKNOWN"}:
        issues.append({
            "severity": "critical",
            "component": "qq.onebot",
            "status": qq_status,
            "reason_code": "QQ_ONEBOT_UNAVAILABLE",
            "detail": str(qq_onebot.get("detail") or "").strip(),
            "action": "检查 QQ/NapCat/OneBot 是否在线，并确认 self_id 正确。",
        })
    resource_status = str(resources.get("status") or "").upper()
    if resource_status in {"HIGH", "ELEVATED"}:
        issues.append({
            "severity": "warning" if resource_status == "ELEVATED" else "critical",
            "component": "host.resources",
            "status": resource_status,
            "reason_code": "RESOURCE_PRESSURE",
            "detail": ",".join(str(x) for x in resources.get("reasons") or []),
            "action": "推迟重型任务，或先释放 CPU/内存/磁盘压力。",
        })
    return _dedupe_issues(issues)


def _build_runtime_recommendations(*, issues: list[dict[str, Any]], capabilities: Mapping[str, Any]) -> list[str]:
    recommendations: list[str] = []
    for issue in issues:
        action = str(issue.get("action") or "").strip()
        if action and action not in recommendations:
            recommendations.append(action)
    if not recommendations:
        recommendations.append("核心闭环正常，可以继续执行生产型 skills。")
    if str(_mapping(capabilities.get("heavy_tasks")).get("status") or "").upper() == "READY":
        recommendations.append("当前资源压力允许执行代码、文档、检索、整理类任务。")
    return recommendations[:5]


def _build_runtime_headline(overall_status: str, issues: list[dict[str, Any]]) -> str:
    if overall_status == "READY":
        return "核心闭环可用"
    critical_count = sum(1 for row in issues if row.get("severity") == "critical")
    warning_count = sum(1 for row in issues if row.get("severity") == "warning")
    if critical_count:
        return f"发现 {critical_count} 个关键断点，需先处理"
    return f"可用但有 {warning_count} 个降级点"


def _recommended_action_for_component(component: str, reason_code: Any) -> str:
    normalized = str(component or "").lower()
    reason = str(reason_code or "").upper()
    if "integrations.llm" in normalized or "llm_gateway" in normalized:
        return "检查当前 LLM provider/API key/profile 配置；若 Host 健康已通过，可忽略离线脚本环境误报。"
    if "vts" in normalized:
        return "启动 VTube Studio 并确认插件端口 8001 可连接。"
    if "tts" in normalized:
        return "检查 Edge TTS 模型加载、声卡输出设备和 CUDA/后端状态。"
    if "asr" in normalized:
        return "如果需要语音输入，启用 Edge ASR；只走文字/QQ 时可忽略。"
    if "gateway" in normalized:
        return "检查 Edge Gateway 进程和通讯平台适配器。"
    if "retrieval" in normalized:
        return "检查 Chroma/记忆召回服务，避免长期记忆和台词召回为空。"
    if "skills" in normalized:
        return "检查 SkillRegistry/SkillLoader，工具执行会受影响。"
    if "EDGE_RPC" in reason:
        return "检查 Edge RPC 地址、HMAC 密钥和网络连通。"
    return "查看该组件的 health detail 并按组件日志继续定位。"


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    for issue in sorted(issues, key=lambda row: severity_order.get(str(row.get("severity") or ""), 9)):
        key = (str(issue.get("component") or ""), str(issue.get("reason_code") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round_number(value: Any, digits: int = 1) -> float | None:
    number = _to_float(value)
    return round(number, digits) if number is not None else None


def _run_nvidia_smi_csv(query: str, *, timeout_s: float = 5.0) -> tuple[bool, list[list[str]], str]:
    command = ["nvidia-smi", query, "--format=csv,noheader,nounits"]
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout_s,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = _subprocess_run(command, **run_kwargs)
    except FileNotFoundError:
        return False, [], "nvidia-smi not found"
    except subprocess.TimeoutExpired:
        return False, [], "nvidia-smi timeout"
    except Exception as exc:
        return False, [], f"{exc.__class__.__name__}: {exc}"
    if completed.returncode != 0:
        error = str(completed.stderr or completed.stdout or "").strip()
        return False, [], error[:500] or f"nvidia-smi exited {completed.returncode}"
    try:
        import csv

        rows = [
            [cell.strip() for cell in row]
            for row in csv.reader(str(completed.stdout or "").splitlines())
            if any(str(cell or "").strip() for cell in row)
        ]
    except Exception as exc:
        return False, [], f"parse nvidia-smi csv failed: {exc}"
    return True, rows, ""


def _collect_nvidia_gpu_snapshot() -> dict[str, Any]:
    ok, rows, error = _run_nvidia_smi_csv(
        "--query-gpu=index,name,driver_version,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit"
    )
    if not ok:
        return {
            "available": False,
            "vendor": "nvidia",
            "error": error,
            "gpus": [],
            "summary": {},
        }

    gpus: list[dict[str, Any]] = []
    for row in rows:
        padded = [*row, *[""] * max(0, 10 - len(row))]
        used_mb = _round_number(padded[6], 1)
        total_mb = _round_number(padded[7], 1)
        memory_percent = round((used_mb / total_mb) * 100, 1) if used_mb is not None and total_mb else None
        gpus.append({
            "index": _to_int(padded[0]),
            "name": padded[1],
            "driver_version": padded[2],
            "temperature_c": _round_number(padded[3], 1),
            "utilization_percent": _round_number(padded[4], 1),
            "memory_utilization_percent": _round_number(padded[5], 1),
            "memory_used_mb": used_mb,
            "memory_total_mb": total_mb,
            "memory_free_mb": round(total_mb - used_mb, 1) if used_mb is not None and total_mb is not None else None,
            "memory_percent": memory_percent,
            "power_draw_w": _round_number(padded[8], 2),
            "power_limit_w": _round_number(padded[9], 2),
        })

    summary = dict(gpus[0]) if gpus else {}
    return {
        "available": bool(gpus),
        "vendor": "nvidia",
        "gpus": gpus,
        "summary": summary,
    }


def _collect_gpu_processes(*, limit: int = 20) -> dict[str, Any]:
    ok, rows, error = _run_nvidia_smi_csv(
        "--query-compute-apps=pid,process_name,used_memory",
        timeout_s=5.0,
    )
    if not ok:
        return {
            "available": False,
            "processes": [],
            "count": 0,
            "error": error,
        }

    process_name_by_pid: dict[int, str] = {}
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                process_name_by_pid[int(proc.info.get("pid"))] = str(proc.info.get("name") or "")
            except Exception:
                continue
    except Exception:
        process_name_by_pid = {}

    processes: list[dict[str, Any]] = []
    for row in rows:
        padded = [*row, *[""] * max(0, 3 - len(row))]
        pid = _to_int(padded[0])
        used_mb = _round_number(padded[2], 1)
        processes.append({
            "pid": pid,
            "name": process_name_by_pid.get(pid or -1) or Path(padded[1]).name or padded[1],
            "process_path": padded[1],
            "vram_mb": used_mb or 0.0,
        })
    processes.sort(key=lambda item: float(item.get("vram_mb") or 0.0), reverse=True)
    return {
        "available": True,
        "processes": processes[:limit],
        "count": len(processes),
    }


def _run_nvidia_smi_plain(args: list[str], *, timeout_s: float = 5.0) -> tuple[bool, str, str]:
    command = ["nvidia-smi", *args]
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout_s,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = _subprocess_run(command, **run_kwargs)
    except FileNotFoundError:
        return False, "", "nvidia-smi not found"
    except subprocess.TimeoutExpired:
        return False, "", "nvidia-smi timeout"
    except Exception as exc:
        return False, "", f"{exc.__class__.__name__}: {exc}"
    if completed.returncode != 0:
        error = str(completed.stderr or completed.stdout or "").strip()
        return False, str(completed.stdout or ""), error[:500] or f"nvidia-smi exited {completed.returncode}"
    return True, str(completed.stdout or ""), ""


def _parse_pmon_number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _process_info_by_pid(pid: int) -> dict[str, Any]:
    try:
        import psutil

        proc = psutil.Process(int(pid))
        info = proc.as_dict(["name", "cmdline", "memory_info", "memory_percent", "status", "username"])
        raw_cmdline = " ".join(str(part) for part in (info.get("cmdline") or []))[:500]
        name = str(info.get("name") or "").strip() or f"pid-{pid}"
        mem_info = info.get("memory_info")
        rss_mb = round(float(getattr(mem_info, "rss", 0) or 0) / 1024 ** 2, 1)
        private_mb = None
        try:
            full_info = proc.memory_full_info()
            private_raw = getattr(full_info, "uss", None) or getattr(full_info, "private", None)
            private_mb = round(float(private_raw or 0) / 1024 ** 2, 1)
        except Exception:
            private_mb = None
        role = _classify_process_resource_role(name, raw_cmdline)
        return {
            "pid": int(pid),
            "name": name,
            "cmdline": raw_cmdline,
            "memory_mb": rss_mb,
            "private_mb": private_mb,
            "memory_percent": _round_number(info.get("memory_percent"), 2) or 0.0,
            "status": str(info.get("status") or ""),
            "username": str(info.get("username") or ""),
            "role": role,
            "protected": role in _RESOURCE_PROTECTED_ROLES or _is_protected_process_name(name),
        }
    except Exception:
        return {
            "pid": int(pid),
            "name": f"pid-{pid}",
            "cmdline": "",
            "memory_mb": 0.0,
            "private_mb": None,
            "memory_percent": 0.0,
            "status": "",
            "username": "",
            "role": "unknown",
            "protected": False,
        }


def _collect_gpu_context_processes(*, limit: int = 80) -> dict[str, Any]:
    """Collect graphics/compute GPU contexts.

    On Windows WDDM, per-process framebuffer memory is often unavailable even
    when total VRAM is high. This still gives us PID-level activity evidence.
    """

    ok, stdout, error = _run_nvidia_smi_plain(["pmon", "-c", "1", "-s", "um"], timeout_s=5.0)
    if not ok:
        return {
            "available": False,
            "processes": [],
            "count": 0,
            "error": error,
            "wddm_memory_note": "Windows WDDM may hide per-process graphics VRAM.",
        }
    processes: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 11:
            continue
        pid = _to_int(parts[1])
        if pid is None:
            continue
        base = _process_info_by_pid(pid)
        command_name = " ".join(parts[11:]).strip() if len(parts) > 11 else str(base.get("name") or "")
        sm_percent = _parse_pmon_number(parts[3])
        mem_percent = _parse_pmon_number(parts[4])
        fb_mb = _parse_pmon_number(parts[9])
        processes.append({
            **base,
            "gpu_index": _to_int(parts[0]),
            "gpu_context_type": parts[2],
            "gpu_sm_percent": sm_percent,
            "gpu_mem_percent": mem_percent,
            "gpu_fb_mb": fb_mb,
            "gpu_ccpm_mb": _parse_pmon_number(parts[10]),
            "gpu_command": command_name or base.get("name"),
            "gpu_active": bool((sm_percent or 0) > 0 or (mem_percent or 0) > 0),
        })
    processes.sort(
        key=lambda row: (
            float(row.get("gpu_sm_percent") or 0.0),
            float(row.get("gpu_mem_percent") or 0.0),
            float(row.get("memory_mb") or 0.0),
        ),
        reverse=True,
    )
    return {
        "available": True,
        "processes": processes[:limit],
        "count": len(processes),
        "wddm_memory_note": (
            "Windows WDDM often reports graphics process VRAM as N/A/0MB; "
            "use total VRAM plus active GPU contexts for attribution."
        ),
    }


def _collect_system_resource_snapshot(
    *,
    include_gpu: bool,
    include_network: bool,
    sample_seconds: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ts_ms": int(time.time() * 1000),
        "scope": "whole_system",
    }
    try:
        import platform
        import psutil

        result["platform"] = {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "hostname": platform.node(),
            "python_version": platform.python_version(),
        }
        cpu_freq = psutil.cpu_freq()
        result["cpu"] = {
            "percent": _round_number(psutil.cpu_percent(interval=sample_seconds), 1),
            "count_logical": psutil.cpu_count(logical=True),
            "count_physical": psutil.cpu_count(logical=False),
            "freq_mhz": _round_number(getattr(cpu_freq, "current", None), 1),
        }
        mem = psutil.virtual_memory()
        result["memory"] = {
            "total_gb": round(mem.total / 1024 ** 3, 2),
            "used_gb": round(mem.used / 1024 ** 3, 2),
            "available_gb": round(mem.available / 1024 ** 3, 2),
            "percent": _round_number(mem.percent, 1),
        }
        swap = psutil.swap_memory()
        result["swap"] = {
            "total_gb": round(swap.total / 1024 ** 3, 2),
            "used_gb": round(swap.used / 1024 ** 3, 2),
            "percent": _round_number(swap.percent, 1),
        }
        disk_path = "C:\\" if os.name == "nt" else "/"
        disk = psutil.disk_usage(disk_path)
        result["disk"] = {
            "path": disk_path,
            "total_gb": round(disk.total / 1024 ** 3, 2),
            "used_gb": round(disk.used / 1024 ** 3, 2),
            "free_gb": round(disk.free / 1024 ** 3, 2),
            "percent": _round_number(disk.percent, 1),
        }
        result["process_summary"] = {
            "count": len(psutil.pids()),
            "boot_time": psutil.boot_time(),
            "uptime_hours": round((time.time() - psutil.boot_time()) / 3600, 2),
        }
        if include_network:
            net = psutil.net_io_counters()
            result["network"] = {
                "bytes_sent_mb": round(net.bytes_sent / 1024 ** 2, 1),
                "bytes_recv_mb": round(net.bytes_recv / 1024 ** 2, 1),
                "packets_sent": int(net.packets_sent),
                "packets_recv": int(net.packets_recv),
            }
    except ImportError:
        result["note"] = "psutil 未安装，系统资源快照降级"
    except Exception as exc:
        result["error"] = str(exc)

    if include_gpu:
        result["gpu"] = _collect_nvidia_gpu_snapshot()
    return result


_PROTECTED_PROCESS_NAMES = {
    "system",
    "registry",
    "secure system",
    "idle",
    "csrss.exe",
    "lsass.exe",
    "services.exe",
    "smss.exe",
    "wininit.exe",
    "winlogon.exe",
    "memcompression",
}
_RESOURCE_PROTECTED_ROLES = {
    "system_protected",
    "natsume_runtime",
    "edge_runtime",
    "container_runtime",
    "model_training",
}
_MODEL_TRAINING_CMD_MARKERS = (
    "torchrun",
    "deepspeed",
    "accelerate launch",
    "train.py",
    "train_",
    "train-",
    "trainer",
    "finetune",
    "fine-tune",
    "fine_tune",
    "gpt-sovits",
    "gpt_sovits",
    "so-vits",
    "sovits",
    "bert-vits",
    "vits",
    "rvc",
    "fish-speech",
    "cosyvoice",
    "f5-tts",
    "xtts",
    "whisper",
)


def _is_protected_process_name(name: str) -> bool:
    return str(name or "").strip().lower() in _PROTECTED_PROCESS_NAMES


def _looks_like_model_training_process(name: str, cmdline: str) -> bool:
    normalized_name = str(name or "").strip().lower()
    normalized_cmd = str(cmdline or "").strip().lower()
    if not normalized_cmd:
        return False
    if not any(marker in normalized_cmd for marker in _MODEL_TRAINING_CMD_MARKERS):
        return False
    return (
        normalized_name in {"python.exe", "pythonw.exe", "python3.exe", "python"}
        or "python" in normalized_cmd
        or "torchrun" in normalized_cmd
        or "deepspeed" in normalized_cmd
        or "accelerate launch" in normalized_cmd
    )


def _classify_process_resource_role(name: str, cmdline: str) -> str:
    normalized_name = str(name or "").strip().lower()
    normalized_cmd = str(cmdline or "").strip().lower()
    if (
        "edge_client" in normalized_cmd
        or "onebot" in normalized_cmd
        or "napcat" in normalized_cmd
    ):
        return "edge_runtime"
    if _looks_like_model_training_process(normalized_name, normalized_cmd):
        return "model_training"
    if any(
        marker in normalized_cmd
        for marker in ("gpt-sovits", "gpt_sovits", "so-vits", "sovits", "faster-whisper")
    ):
        return "edge_runtime"
    if "host_brain" in normalized_cmd:
        return "natsume_runtime"
    if normalized_name.startswith("docker") or normalized_name.startswith("vmmem") or "docker" in normalized_cmd or "com.docker" in normalized_cmd:
        return "container_runtime"
    if normalized_name.startswith("obs") or "obs-studio" in normalized_cmd:
        return "recording_streaming"
    if normalized_name in {"chrome.exe", "msedge.exe", "msedgewebview2.exe", "firefox.exe"}:
        return "browser"
    if normalized_name in {"qq.exe", "wechat.exe", "wechatappex.exe", "weixin.exe", "weixinappex.exe"}:
        return "communication_app"
    if (
        normalized_name in {"douyin.exe", "steam.exe", "sgame.exe", "sgrender.exe", "gameviewer.exe"}
        or normalized_name.endswith("win64-shipping.exe")
        or normalized_name.endswith("-win64-shipping.exe")
        or "steamapps" in normalized_cmd
        or "unrealengine" in normalized_cmd
        or "unityplayer" in normalized_cmd
    ):
        return "foreground_media_or_game"
    if _is_protected_process_name(normalized_name):
        return "system_protected"
    return "user_or_system_process"


def _collect_process_resource_top(
    *,
    limit: int,
    sort_by: str,
    sample_seconds: float,
    include_cmdline: bool = False,
) -> dict[str, Any]:
    try:
        import psutil

        attrs = ["pid", "name", "status", "username", "create_time", "memory_info", "memory_percent", "cmdline"]
        tracked = []
        for proc in psutil.process_iter(["pid"]):
            try:
                proc.cpu_percent(None)
                tracked.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        if sample_seconds > 0:
            time.sleep(sample_seconds)

        processes: list[dict[str, Any]] = []
        for proc in tracked:
            try:
                info = proc.as_dict(attrs=attrs)
                mem_info = info.get("memory_info")
                rss_mb = round(float(getattr(mem_info, "rss", 0) or 0) / 1024 ** 2, 1)
                private_mb = None
                try:
                    full_info = proc.memory_full_info()
                    private_raw = getattr(full_info, "uss", None) or getattr(full_info, "private", None)
                    private_mb = round(float(private_raw or 0) / 1024 ** 2, 1)
                except Exception:
                    private_mb = None
                raw_cmdline = " ".join(str(part) for part in (info.get("cmdline") or []))[:500]
                cmdline = raw_cmdline[:300] if include_cmdline else ""
                name = str(info.get("name") or "").strip() or f"pid-{proc.pid}"
                role = _classify_process_resource_role(name, raw_cmdline)
                protected = role in _RESOURCE_PROTECTED_ROLES or _is_protected_process_name(name)
                processes.append({
                    "pid": int(info.get("pid") or proc.pid),
                    "name": name,
                    "cpu_percent": _round_number(proc.cpu_percent(None), 1) or 0.0,
                    "memory_mb": rss_mb,
                    "private_mb": private_mb,
                    "memory_percent": _round_number(info.get("memory_percent"), 2) or 0.0,
                    "vram_mb": 0.0,
                    "status": str(info.get("status") or ""),
                    "username": str(info.get("username") or ""),
                    "role": role,
                    "protected": protected,
                    "cmdline": cmdline,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        sort_key_map = {
            "cpu": "cpu_percent",
            "memory": "memory_mb",
            "rss": "memory_mb",
            "pid": "pid",
            "name": "name",
        }
        sort_key = sort_key_map.get(sort_by, "memory_mb")
        processes.sort(key=lambda row: row.get(sort_key) or 0, reverse=sort_by not in {"name", "pid"})
        return {"available": True, "processes": processes[:limit], "count": len(processes)}
    except ImportError:
        return {"available": False, "processes": [], "count": 0, "note": "psutil 未安装"}
    except Exception as exc:
        return {"available": False, "processes": [], "count": 0, "error": str(exc)}


def _merge_vram_into_processes(processes: list[dict[str, Any]], gpu_processes: Mapping[str, Any]) -> list[dict[str, Any]]:
    gpu_rows = list(gpu_processes.get("processes") or []) if isinstance(gpu_processes.get("processes"), list) else []
    vram_by_pid = {
        int(row.get("pid")): float(row.get("vram_mb") or 0.0)
        for row in gpu_rows
        if isinstance(row, Mapping) and _to_int(row.get("pid")) is not None
    }
    merged = []
    seen_pids: set[int] = set()
    for row in processes:
        item = dict(row)
        pid = _to_int(item.get("pid"))
        if pid is not None:
            item["vram_mb"] = round(float(vram_by_pid.get(pid, item.get("vram_mb") or 0.0)), 1)
            seen_pids.add(pid)
        merged.append(item)
    for gpu_row in gpu_rows:
        if not isinstance(gpu_row, Mapping):
            continue
        pid = _to_int(gpu_row.get("pid"))
        if pid is None or pid in seen_pids:
            continue
        name = str(gpu_row.get("name") or "gpu-process").strip()
        raw_cmdline = ""
        memory_mb = 0.0
        private_mb = None
        memory_percent = 0.0
        status = ""
        username = ""
        try:
            import psutil

            proc = psutil.Process(pid)
            info = proc.as_dict(["name", "cmdline", "memory_info", "memory_percent", "status", "username"])
            name = str(info.get("name") or name).strip() or name
            raw_cmdline = " ".join(str(part) for part in (info.get("cmdline") or []))[:500]
            mem_info = info.get("memory_info")
            memory_mb = round(float(getattr(mem_info, "rss", 0) or 0) / 1024 ** 2, 1)
            memory_percent = _round_number(info.get("memory_percent"), 2) or 0.0
            status = str(info.get("status") or "")
            username = str(info.get("username") or "")
            try:
                full_info = proc.memory_full_info()
                private_raw = getattr(full_info, "uss", None) or getattr(full_info, "private", None)
                private_mb = round(float(private_raw or 0) / 1024 ** 2, 1)
            except Exception:
                private_mb = None
        except Exception:
            pass
        role = _classify_process_resource_role(name, raw_cmdline)
        merged.append({
            "pid": pid,
            "name": name,
            "cpu_percent": 0.0,
            "memory_mb": memory_mb,
            "private_mb": private_mb,
            "memory_percent": memory_percent,
            "vram_mb": _round_number(gpu_row.get("vram_mb"), 1) or 0.0,
            "status": status,
            "username": username,
            "role": role,
            "protected": role in _RESOURCE_PROTECTED_ROLES or _is_protected_process_name(name),
            "cmdline": "",
        })
    return merged


def _build_resource_release_candidates(
    processes: list[dict[str, Any]],
    *,
    min_memory_mb: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    current_pid = os.getpid()
    for row in processes:
        memory_mb = float(row.get("memory_mb") or 0.0)
        vram_mb = float(row.get("vram_mb") or 0.0)
        if memory_mb < min_memory_mb and vram_mb <= 0:
            continue
        pid = _to_int(row.get("pid"))
        protected = bool(row.get("protected")) or pid == current_pid
        role = str(row.get("role") or "")
        safe_to_trim = not protected and role not in _RESOURCE_PROTECTED_ROLES
        candidates.append({
            "pid": pid,
            "name": str(row.get("name") or ""),
            "memory_mb": round(memory_mb, 1),
            "vram_mb": round(vram_mb, 1),
            "role": role,
            "safe_to_trim": safe_to_trim,
            "protected": protected,
            "recommended_action": (
                "trim_working_set"
                if safe_to_trim and memory_mb >= min_memory_mb
                else "manual_review_or_close_app"
            ),
        })
    candidates.sort(key=lambda item: (float(item.get("vram_mb") or 0.0), float(item.get("memory_mb") or 0.0)), reverse=True)
    return candidates[:20]


def _build_resource_recommendations(
    *,
    pressure: Mapping[str, Any],
    processes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[str]:
    recommendations: list[str] = []
    status = str(pressure.get("status") or "UNKNOWN").upper()
    reasons = {str(item) for item in pressure.get("reasons") or []}
    if "memory_high" in reasons or "memory_elevated" in reasons:
        recommendations.append("内存有压力：先看热点进程，优先关闭不用的浏览器/媒体/游戏类应用。")
    if "vram_high" in reasons or "vram_elevated" in reasons:
        recommendations.append("显存有压力：显存通常要关闭对应 GPU 进程、卸载模型或重启图形/推理组件才能真正释放。")
    if (
        "cpu_high" in reasons
        or "cpu_elevated" in reasons
        or "natsume_runtime_cpu_high" in reasons
        or "natsume_runtime_cpu_elevated" in reasons
    ):
        recommendations.append("CPU/Host 有压力：暂停编译、浏览器自动化、视频/游戏或后台批处理这类重任务，并延后系统后台 LLM 链路。")
    if candidates:
        actionable = [
            item
            for item in candidates
            if str(item.get("role") or "") not in _RESOURCE_PROTECTED_ROLES
        ]
        names = ", ".join(
            f"{item.get('name')}(PID {item.get('pid')})"
            for item in actionable[:3]
            if item.get("pid")
        )
        if names:
            recommendations.append(f"当前可优先排查：{names}。")
    elif status == "NORMAL":
        recommendations.append("资源压力正常；如果仍卡顿，下一步应看磁盘 IO、驱动、前台应用和网络。")
    if processes:
        recommendations.append("真实释放动作默认不会强杀进程；工作集回收需要显式确认，强占显存仍建议人工关闭对应应用。")
    return recommendations[:5]


def _execute_system_resource_snapshot(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    _ = context
    include_gpu = bool(payload.get("include_gpu", True))
    include_processes = bool(payload.get("include_processes", True))
    include_network = bool(payload.get("include_network", False))
    top_n = max(1, min(50, int(payload.get("top_n") or 10)))
    sample_seconds = max(0.0, min(3.0, float(payload.get("sample_seconds") or 0.2)))

    snapshot = _collect_system_resource_snapshot(
        include_gpu=include_gpu,
        include_network=include_network,
        sample_seconds=sample_seconds,
    )
    pressure = _assess_resource_pressure(snapshot)
    process_top: dict[str, Any] = {"available": False, "processes": [], "count": 0}
    gpu_processes: dict[str, Any] = {"available": False, "processes": [], "count": 0}
    merged_processes: list[dict[str, Any]] = []
    if include_processes:
        process_top = _collect_process_resource_top(
            limit=top_n,
            sort_by="memory",
            sample_seconds=sample_seconds,
            include_cmdline=False,
        )
        if include_gpu:
            gpu_processes = _collect_gpu_processes(limit=top_n)
        merged_processes = _merge_vram_into_processes(list(process_top.get("processes") or []), gpu_processes)
        merged_processes.sort(
            key=lambda row: (float(row.get("vram_mb") or 0.0), float(row.get("memory_mb") or 0.0)),
            reverse=True,
        )
        process_top["processes"] = merged_processes[:top_n]
        pressure = _augment_pressure_with_process_hotspots(pressure, merged_processes)
    candidates = _build_resource_release_candidates(merged_processes, min_memory_mb=512)
    recommendations = _build_resource_recommendations(
        pressure=pressure,
        processes=merged_processes,
        candidates=candidates,
    )
    return {
        "success": True,
        "scope": "whole_system",
        "snapshot": snapshot,
        "pressure": pressure,
        "process_top": process_top,
        "gpu_processes": gpu_processes,
        "release_candidates": candidates,
        "recommendations": recommendations,
        "release_policy": {
            "dry_run_default": True,
            "requires_confirm_for_mutation": True,
            "will_not_auto_kill_process": True,
            "vram_release_note": "显存释放通常需要关闭对应 GPU 进程、卸载模型或重启图形/推理组件。",
        },
    }


def _execute_process_resource_top(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    _ = context
    limit = max(1, min(100, int(payload.get("limit") or 20)))
    sort_by = str(payload.get("sort_by") or "memory").strip().lower()
    include_gpu = bool(payload.get("include_gpu", True))
    include_cmdline = bool(payload.get("include_cmdline", False))
    sample_seconds = max(0.0, min(3.0, float(payload.get("sample_seconds") or 0.2)))
    process_top = _collect_process_resource_top(
        limit=limit,
        sort_by="memory" if sort_by == "vram" else sort_by,
        sample_seconds=sample_seconds,
        include_cmdline=include_cmdline,
    )
    gpu_processes = _collect_gpu_processes(limit=limit) if include_gpu else {"available": False, "processes": [], "count": 0}
    processes = _merge_vram_into_processes(list(process_top.get("processes") or []), gpu_processes)
    if sort_by == "vram":
        processes.sort(key=lambda row: float(row.get("vram_mb") or 0.0), reverse=True)
    elif sort_by in {"memory", "rss"}:
        processes.sort(key=lambda row: float(row.get("memory_mb") or 0.0), reverse=True)
    elif sort_by == "cpu":
        processes.sort(key=lambda row: float(row.get("cpu_percent") or 0.0), reverse=True)
    process_top["processes"] = processes[:limit]
    return {
        "success": True,
        "scope": "whole_system",
        "sort_by": sort_by,
        "processes": process_top["processes"],
        "count": len(process_top["processes"]),
        "total_seen": process_top.get("count"),
        "gpu_processes": gpu_processes,
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        return dict(parsed or {}) if isinstance(parsed, Mapping) else {}
    except Exception:
        return {}


def _load_resource_manager_status_from_file() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "data" / "runtime_resource_manager.json"
    payload = _read_json_file(path)
    if payload:
        payload.setdefault("registered", False)
        payload.setdefault("status_path", str(path))
    return payload


def _fetch_local_json(url: str, *, timeout_s: float = 2.0) -> dict[str, Any]:
    try:
        return _fetch_json_url(url, timeout_s=timeout_s)
    except Exception as exc:
        return {
            "status": "UNREACHABLE",
            "error": f"{exc.__class__.__name__}: {exc}",
            "url": url,
        }


def _tail_text_file(path: Path, *, max_bytes: int = 96_000) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(max(0, size - max_bytes))
            data = fh.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _count_pattern(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _collect_resource_log_signals() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    log_paths = [
        root / "runtime" / "host" / "logs" / "host.stdout.log",
        root / "runtime" / "host" / "logs" / "host.stderr.log",
        root / "runtime" / "host" / "logs" / "obs.stdout.log",
        root / "runtime" / "host" / "logs" / "obs.stderr.log",
    ]
    text_by_name: dict[str, str] = {}
    for path in log_paths:
        text_by_name[path.name] = _tail_text_file(path)
    combined = "\n".join(text_by_name.values())
    counts = {
        "gemini_afc_calls": _count_pattern(combined, r"AFC is enabled"),
        "obs_trace_builds": _count_pattern(combined, r"trace build mode=full"),
        "obs_warm_cache": _count_pattern(combined, r"warm cache"),
        "resource_deferred": _count_pattern(combined, r"RESOURCE_PRESSURE|deferred:resource_pressure"),
        "evolution_triggers": _count_pattern(combined, r"EVOLUTION_CYCLE_TRIGGERED"),
        "loneliness_signals": _count_pattern(combined, r"loneliness_signal"),
        "cuda_oom": _count_pattern(combined, r"CUDA out of memory|out of memory|OutOfMemory"),
        "event_loop_closed": _count_pattern(combined, r"Event loop is closed"),
        "tracebacks": _count_pattern(combined, r"Traceback"),
        "timeouts": _count_pattern(combined, r"timeout|timed out"),
    }
    recent_lines: list[str] = []
    for name, text in text_by_name.items():
        for line in text.splitlines()[-80:]:
            if re.search(r"(RESOURCE_PRESSURE|deferred:resource_pressure|trace build mode=full|AFC is enabled|out of memory|Traceback|Event loop is closed|EVOLUTION_CYCLE_TRIGGERED)", line, re.IGNORECASE):
                recent_lines.append(f"{name}: {line.strip()[:260]}")
    return {
        "log_paths": [str(path) for path in log_paths],
        "counts": counts,
        "recent_evidence": recent_lines[-12:],
    }


def _process_label(row: Mapping[str, Any]) -> str:
    name = str(row.get("name") or "process").strip()
    pid = row.get("pid")
    mem = _round_number(row.get("memory_mb"), 1) or 0.0
    vram = _round_number(row.get("vram_mb") or row.get("gpu_fb_mb"), 1) or 0.0
    role = str(row.get("role") or "").strip()
    gpu_sm = row.get("gpu_sm_percent")
    gpu_text = f", GPU-SM={gpu_sm}%" if gpu_sm not in (None, "") else ""
    return f"{name}(PID {pid}, RAM={mem:.0f}MB, VRAM={vram:.0f}MB, role={role}{gpu_text})"


def _sum_metric(rows: list[Mapping[str, Any]], key: str) -> float:
    total = 0.0
    for row in rows:
        total += float(row.get(key) or 0.0)
    return round(total, 1)


def _rows_by_role(rows: list[Mapping[str, Any]], *roles: str) -> list[Mapping[str, Any]]:
    wanted = {role for role in roles}
    return [row for row in rows if str(row.get("role") or "") in wanted]


def _unique_rows_by_pid(*groups: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    no_pid: list[dict[str, Any]] = []
    for group in groups:
        for row in group:
            item = dict(row)
            pid = _to_int(item.get("pid"))
            if pid is None:
                no_pid.append(item)
                continue
            existing = merged.get(pid, {})
            combined = {**existing, **item}
            combined["memory_mb"] = max(float(existing.get("memory_mb") or 0.0), float(item.get("memory_mb") or 0.0))
            combined["vram_mb"] = max(float(existing.get("vram_mb") or 0.0), float(item.get("vram_mb") or item.get("gpu_fb_mb") or 0.0))
            merged[pid] = combined
    return [*merged.values(), *no_pid]


def _add_hypothesis(
    hypotheses: list[dict[str, Any]],
    *,
    cause_id: str,
    title: str,
    confidence: float,
    severity: str,
    evidence: list[str],
    recommendation: str,
    protected: bool = False,
) -> None:
    hypotheses.append({
        "cause_id": cause_id,
        "title": title,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "severity": severity,
        "evidence": [item for item in evidence if str(item).strip()][:6],
        "recommendation": recommendation,
        "protected": bool(protected),
    })


def _build_resource_pressure_hypotheses(
    *,
    snapshot_detail: Mapping[str, Any],
    process_rows: list[Mapping[str, Any]],
    gpu_context_rows: list[Mapping[str, Any]],
    resource_manager_status: Mapping[str, Any],
    obs_health: Mapping[str, Any],
    log_signals: Mapping[str, Any],
) -> list[dict[str, Any]]:
    snapshot = _mapping(snapshot_detail.get("snapshot"))
    pressure = _mapping(snapshot_detail.get("pressure"))
    memory = _mapping(snapshot.get("memory"))
    gpu = _mapping(snapshot.get("gpu"))
    gpu_summary = _mapping(gpu.get("summary"))
    memory_percent = _to_float(memory.get("percent")) or 0.0
    vram_percent = _to_float(gpu_summary.get("memory_percent")) or 0.0
    gpu_percent = _to_float(gpu_summary.get("utilization_percent")) or 0.0
    counts = _mapping(log_signals.get("counts"))
    hypotheses: list[dict[str, Any]] = []

    combined_pressure = memory_percent >= 78.0 and vram_percent >= 70.0
    if combined_pressure:
        _add_hypothesis(
            hypotheses,
            cause_id="combined_ram_vram_pressure",
            title="内存和显存同时处于高压区，不是单一 Host 卡顿",
            confidence=0.92,
            severity="critical" if memory_percent >= 88.0 or vram_percent >= 85.0 else "warning",
            evidence=[
                f"内存 {memory_percent:.1f}%",
                f"显存 {vram_percent:.1f}%",
                f"GPU 利用率 {gpu_percent:.1f}%",
                "压力原因：" + ",".join(str(item) for item in pressure.get("reasons") or []),
            ],
            recommendation="进入降载模式：推迟后台 LLM/浏览器自动化/OBS warm cache，并先确认前台 GPU 应用和训练任务。",
        )

    training_rows = [row for row in process_rows if str(row.get("role") or "") == "model_training"]
    if training_rows:
        _add_hypothesis(
            hypotheses,
            cause_id="protected_model_training",
            title="后台语音/模型训练会同时占 RAM、VRAM、CPU，且应被保护",
            confidence=0.9,
            severity="expected_heavy",
            evidence=[_process_label(row) for row in training_rows[:4]],
            recommendation="这类进程默认不能自动释放；需要继续训练时只降载其他后台任务，不碰训练进程。",
            protected=True,
        )

    game_rows = _rows_by_role(process_rows, "foreground_media_or_game")
    active_game_contexts = _rows_by_role(gpu_context_rows, "foreground_media_or_game")
    if game_rows or active_game_contexts:
        evidence_rows = _unique_rows_by_pid(game_rows, active_game_contexts)
        _add_hypothesis(
            hypotheses,
            cause_id="foreground_graphics_or_game",
            title="前台游戏/视频/图形应用是 RAM+GPU 压力的高概率来源",
            confidence=0.84 if combined_pressure or active_game_contexts else 0.72,
            severity="warning",
            evidence=[_process_label(row) for row in evidence_rows[:5]],
            recommendation="如果不是当前正在使用，优先暂停/退出；如果正在玩或看视频，让夏目只保留核心聊天和资源管家。",
        )

    natsume_rows = _rows_by_role(process_rows, "natsume_runtime")
    natsume_cpu = _sum_metric(natsume_rows, "cpu_percent")
    natsume_mem = _sum_metric(natsume_rows, "memory_mb")
    if natsume_cpu >= 80.0 or natsume_mem >= 2500.0 or str(resource_manager_status.get("last_decision") or "").find("Natsume运行时") >= 0:
        _add_hypothesis(
            hypotheses,
            cause_id="natsume_runtime_self_pressure",
            title="Natsume Host/OBS 自身有运行时压力，需要内部降载而不是释放进程",
            confidence=0.86,
            severity="warning",
            evidence=[
                f"Natsume runtime CPU 合计 {natsume_cpu:.1f}%",
                f"Natsume runtime RAM 合计 {natsume_mem:.0f}MB",
                *[_process_label(row) for row in natsume_rows[:4]],
            ],
            recommendation="不要杀 Host/OBS；应降低后台 tick、warm cache、健康 LLM 探针和多模态重链路频率。",
            protected=True,
        )

    browser_rows = _rows_by_role(process_rows, "browser")
    browser_mem = _sum_metric(browser_rows, "memory_mb")
    browser_contexts = _rows_by_role(gpu_context_rows, "browser")
    if browser_mem >= 1500.0 or len(browser_contexts) >= 3:
        _add_hypothesis(
            hypotheses,
            cause_id="browser_tabs_gpu_contexts",
            title="浏览器标签页/网页 GPU 上下文会积累内存和显存压力",
            confidence=0.76,
            severity="warning",
            evidence=[
                f"浏览器 RAM 合计 {browser_mem:.0f}MB",
                f"GPU 上下文数 {len(browser_contexts)}",
                *[_process_label(row) for row in _unique_rows_by_pid(browser_rows, browser_contexts)[:4]],
            ],
            recommendation="关闭不用的网页/视频页；浏览器自动化和网页采集应在资源 NORMAL/ELEVATED 以下再跑。",
        )

    container_rows = _rows_by_role(process_rows, "container_runtime")
    container_mem = _sum_metric(container_rows, "memory_mb")
    if container_mem >= 1024.0:
        _add_hypothesis(
            hypotheses,
            cause_id="docker_or_wsl_memory",
            title="Docker/WSL 容器运行时可能长期占用系统内存",
            confidence=0.72,
            severity="info",
            evidence=[f"容器 RAM 合计 {container_mem:.0f}MB", *[_process_label(row) for row in container_rows[:4]]],
            recommendation="Neo4j/Chroma/Docker 属于基础设施，默认不自动停；只有确认当前不用时再人工停 Docker。",
            protected=True,
        )

    if int(counts.get("obs_trace_builds") or 0) >= 4 or int(counts.get("obs_warm_cache") or 0) >= 4:
        warm_errors = []
        for key in ("runtime_snapshot_warm_cache", "trace_feed_warm_cache", "insights_summary_warm_cache", "digital_life_overview_warm_cache", "life_body_warm_cache"):
            cache = _mapping(obs_health.get(key))
            err = str(cache.get("last_error") or "").strip()
            if err:
                warm_errors.append(f"{key}={err}")
        _add_hypothesis(
            hypotheses,
            cause_id="obs_warm_cache_pressure",
            title="OBS 监控 warm cache/Trace 构建曾经会放大 Host 压力",
            confidence=0.78,
            severity="warning",
            evidence=[
                f"trace build 日志计数 {counts.get('obs_trace_builds')}",
                f"warm cache 日志计数 {counts.get('obs_warm_cache')}",
                *warm_errors[:4],
            ],
            recommendation="保持 OBS warm cache 受资源门控和 single-flight 控制；压力 HIGH 时只返回旧缓存。",
        )

    if int(counts.get("gemini_afc_calls") or 0) >= 3:
        _add_hypothesis(
            hypotheses,
            cause_id="multimodal_or_gemini_request_burst",
            title="多模态/Gemini 请求突发会放大 CPU、内存和事件循环压力",
            confidence=0.68,
            severity="info",
            evidence=[f"近期 Gemini AFC 调用日志计数 {counts.get('gemini_afc_calls')}"],
            recommendation="多模态观察应走异步队列并受资源门控；压力 HIGH 时先回执，观察完成后再补发结果。",
        )

    visible_vram = max((float(row.get("vram_mb") or row.get("gpu_fb_mb") or 0.0) for row in process_rows), default=0.0)
    if vram_percent >= 60.0 and visible_vram <= 0.0:
        _add_hypothesis(
            hypotheses,
            cause_id="wddm_vram_attribution_gap",
            title="Windows WDDM 隐藏了图形进程级显存，导致看起来像“莫名显存消失”",
            confidence=0.88,
            severity="info",
            evidence=[
                f"总显存占用 {vram_percent:.1f}%",
                "nvidia-smi 在 WDDM 下显示图形进程 GPU Memory=N/A/0MB",
                f"GPU 上下文数 {len(gpu_context_rows)}",
            ],
            recommendation="用总显存 + GPU 上下文 + RAM 热点联合推断；不要只依赖 per-process VRAM 字段。",
        )

    if int(counts.get("cuda_oom") or 0) > 0:
        _add_hypothesis(
            hypotheses,
            cause_id="cuda_oom_seen",
            title="近期日志出现 OOM，说明曾经真实触顶过",
            confidence=0.95,
            severity="critical",
            evidence=[f"OOM 日志计数 {counts.get('cuda_oom')}", *list(log_signals.get("recent_evidence") or [])[:3]],
            recommendation="降低并发 GPU 任务：训练、VTS/TTS、多模态、浏览器自动化不能同时抢显存。",
        )

    if not hypotheses:
        _add_hypothesis(
            hypotheses,
            cause_id="no_current_pressure",
            title="当前采样没有复现爆内存/爆显存，需等下次高压样本或启用历史记录",
            confidence=0.64,
            severity="info",
            evidence=[f"内存 {memory_percent:.1f}%", f"显存 {vram_percent:.1f}%", f"GPU {gpu_percent:.1f}%"],
            recommendation="保持资源管家 15 秒采样；下次 HIGH 时自动记录根因快照。",
        )

    hypotheses.sort(key=lambda row: ({"critical": 0, "warning": 1, "expected_heavy": 2, "info": 3}.get(str(row.get("severity")), 9), -float(row.get("confidence") or 0.0)))
    return hypotheses[:8]


def _build_resource_pressure_summary(hypotheses: list[dict[str, Any]], snapshot_detail: Mapping[str, Any]) -> str:
    snapshot = _mapping(snapshot_detail.get("snapshot"))
    pressure = _mapping(snapshot_detail.get("pressure"))
    memory = _mapping(snapshot.get("memory"))
    gpu = _mapping(snapshot.get("gpu"))
    gpu_summary = _mapping(gpu.get("summary"))
    memory_percent = _to_float(memory.get("percent")) or 0.0
    vram_percent = _to_float(gpu_summary.get("memory_percent")) or 0.0
    status = str(pressure.get("status") or "UNKNOWN").upper()
    top = hypotheses[0] if hypotheses else {}
    title = str(top.get("title") or "未形成明确根因").strip()
    return f"资源压力 {status}：内存 {memory_percent:.1f}%，显存 {vram_percent:.1f}%。首要判断：{title}。"


def _build_resource_pressure_actions(
    *,
    hypotheses: list[dict[str, Any]],
    process_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    protected = [
        _process_label(row)
        for row in process_rows
        if str(row.get("role") or "") in _RESOURCE_PROTECTED_ROLES
    ][:8]
    confirm_rows = [
        row
        for row in process_rows
        if str(row.get("role") or "") in {"browser", "foreground_media_or_game", "communication_app", "recording_streaming", "user_or_system_process"}
        and (float(row.get("memory_mb") or 0.0) >= 512.0 or float(row.get("vram_mb") or 0.0) > 0)
    ]
    return {
        "safe_automatic": [
            "压力 HIGH 时延后后台 LLM、浏览器自动化、检索预热、日记/梦境/演化等非关键任务。",
            "压力 HIGH 时 OBS warm cache 返回旧缓存或跳过刷新。",
            "允许 Host 当前进程做 Python GC；必要时只对安全候选做工作集回收。",
        ],
        "needs_user_confirm": [_process_label(row) for row in confirm_rows[:8]],
        "do_not_touch": protected,
        "next_steps": [str(row.get("recommendation") or "").strip() for row in hypotheses[:4] if str(row.get("recommendation") or "").strip()],
    }


def _persist_resource_pressure_analysis(analysis: Mapping[str, Any]) -> None:
    try:
        _RESOURCE_ANALYSIS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RESOURCE_ANALYSIS_PATH.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        with _RESOURCE_ANALYSIS_JSONL_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(analysis, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        _logger.debug("resource pressure analysis persist skipped: %s", exc)


def _execute_resource_pressure_analysis(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    _ = context
    top_n = max(5, min(80, int(payload.get("top_n") or 40)))
    sample_seconds = max(0.0, min(3.0, float(payload.get("sample_seconds") or 0.5)))
    persist = bool(payload.get("persist", True))

    snapshot_detail = _execute_system_resource_snapshot(
        {
            "include_gpu": True,
            "include_processes": True,
            "include_network": False,
            "top_n": top_n,
            "sample_seconds": sample_seconds,
        },
        context,
    )
    process_top = _execute_process_resource_top(
        {
            "limit": top_n,
            "sort_by": "memory",
            "include_gpu": True,
            "include_cmdline": True,
            "sample_seconds": 0.0,
        },
        context,
    )
    process_rows = [dict(row) for row in list(process_top.get("processes") or []) if isinstance(row, Mapping)]
    gpu_contexts = _collect_gpu_context_processes(limit=top_n)
    gpu_context_rows = [dict(row) for row in list(gpu_contexts.get("processes") or []) if isinstance(row, Mapping)]
    combined_rows = _unique_rows_by_pid(process_rows, gpu_context_rows)

    manager_status = _execute_resource_manager_status({"create_if_missing": False}, context)
    if not bool(manager_status.get("registered")):
        file_status = _load_resource_manager_status_from_file()
        if file_status:
            manager_status = {"success": True, **file_status}
    host_health = _fetch_local_json("http://127.0.0.1:18080/health?skip_dependency_probes=1", timeout_s=2.0)
    obs_health = _fetch_local_json("http://127.0.0.1:18082/health", timeout_s=2.0)
    log_signals = _collect_resource_log_signals()
    hypotheses = _build_resource_pressure_hypotheses(
        snapshot_detail=snapshot_detail,
        process_rows=combined_rows,
        gpu_context_rows=gpu_context_rows,
        resource_manager_status=manager_status,
        obs_health=obs_health,
        log_signals=log_signals,
    )
    analysis = {
        "success": True,
        "scope": "whole_system",
        "ts_ms": int(time.time() * 1000),
        "summary": _build_resource_pressure_summary(hypotheses, snapshot_detail),
        "hypotheses": hypotheses,
        "actions": _build_resource_pressure_actions(hypotheses=hypotheses, process_rows=combined_rows),
        "current_snapshot": snapshot_detail,
        "process_top": process_top,
        "gpu_contexts": gpu_contexts,
        "resource_manager": manager_status,
        "host_health": {
            "status": host_health.get("status"),
            "error": host_health.get("error"),
            "summary": _mapping(_mapping(host_health.get("details")).get("summary")),
        },
        "obs_health": {
            "status": obs_health.get("status"),
            "runtime_snapshot_warm_cache": _mapping(obs_health.get("runtime_snapshot_warm_cache")),
            "trace_feed_warm_cache": _mapping(obs_health.get("trace_feed_warm_cache")),
            "insights_summary_warm_cache": _mapping(obs_health.get("insights_summary_warm_cache")),
            "digital_life_overview_warm_cache": _mapping(obs_health.get("digital_life_overview_warm_cache")),
            "life_body_warm_cache": _mapping(obs_health.get("life_body_warm_cache")),
        },
        "log_signals": log_signals,
        "limits": {
            "windows_wddm_vram_attribution": "per-process graphics VRAM may be N/A/0MB; attribution uses total VRAM plus GPU contexts.",
            "no_process_kill": "analysis never kills or stops processes.",
        },
    }
    if persist:
        _persist_resource_pressure_analysis(analysis)
        analysis["analysis_path"] = str(_RESOURCE_ANALYSIS_PATH)
        analysis["history_path"] = str(_RESOURCE_ANALYSIS_JSONL_PATH)
    return analysis


def _select_release_targets(
    candidates: list[dict[str, Any]],
    *,
    pids: list[int],
    process_names: list[str],
) -> list[dict[str, Any]]:
    wanted_pids = {int(pid) for pid in pids if int(pid) > 0}
    wanted_names = {str(name or "").strip().lower() for name in process_names if str(name or "").strip()}
    if not wanted_pids and not wanted_names:
        return []
    out: list[dict[str, Any]] = []
    for row in candidates:
        pid = _to_int(row.get("pid"))
        name = str(row.get("name") or "").strip().lower()
        if (pid is not None and pid in wanted_pids) or (name and any(item in name for item in wanted_names)):
            out.append(dict(row))
    return out


def _trim_windows_working_set(pid: int) -> dict[str, Any]:
    if os.name != "nt":
        return {"success": False, "pid": pid, "reason_code": "UNSUPPORTED_PLATFORM", "detail": "working set trim only supports Windows"}
    try:
        import ctypes

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_SET_QUOTA = 0x0100
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, int(pid))
        if not handle:
            return {
                "success": False,
                "pid": pid,
                "reason_code": "OPEN_PROCESS_FAILED",
                "detail": f"winerr={ctypes.get_last_error()}",
            }
        try:
            ok = bool(psapi.EmptyWorkingSet(handle))
            if not ok:
                return {
                    "success": False,
                    "pid": pid,
                    "reason_code": "EMPTY_WORKING_SET_FAILED",
                    "detail": f"winerr={ctypes.get_last_error()}",
                }
            return {"success": True, "pid": pid}
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:
        return {"success": False, "pid": pid, "reason_code": "WORKING_SET_TRIM_FAILED", "detail": str(exc)}


def _execute_resource_release(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    _ = context
    mode = str(payload.get("mode") or "dry_run").strip().lower()
    dry_run = bool(payload.get("dry_run", mode == "dry_run"))
    confirm = bool(payload.get("confirm", False))
    min_memory_mb = max(128, min(32768, int(payload.get("min_memory_mb") or 512)))
    pids = [int(pid) for pid in list(payload.get("pids") or []) if _to_int(pid) is not None and int(pid) > 0]
    process_names = [str(name or "").strip() for name in list(payload.get("process_names") or []) if str(name or "").strip()]

    process_top = _execute_process_resource_top(
        {
            "limit": 50,
            "sort_by": "memory",
            "include_gpu": True,
            "include_cmdline": False,
            "sample_seconds": 0.1,
        },
        context,
    )
    processes = list(process_top.get("processes") or []) if isinstance(process_top.get("processes"), list) else []
    candidates = _build_resource_release_candidates(processes, min_memory_mb=min_memory_mb)
    if dry_run or mode == "dry_run":
        return {
            "success": True,
            "mode": "dry_run",
            "dry_run": True,
            "requires_confirm": True,
            "scope": "whole_system",
            "release_candidates": candidates,
            "recommendations": _build_resource_recommendations(
                pressure={"status": "UNKNOWN", "reasons": []},
                processes=processes,
                candidates=candidates,
            ),
        }

    if mode == "gc_collect":
        import gc

        collected = gc.collect()
        return {
            "success": True,
            "mode": "gc_collect",
            "scope": "current_host_process",
            "collected": int(collected),
            "note": "Python GC 只影响当前 Host 进程，不能释放其他桌面应用或显存。",
        }

    if mode != "trim_working_sets":
        return {
            "success": False,
            "reason_code": "UNSUPPORTED_RELEASE_MODE",
            "mode": mode,
            "supported_modes": ["dry_run", "gc_collect", "trim_working_sets"],
        }
    if not confirm:
        return {
            "success": False,
            "reason_code": "CONFIRM_REQUIRED",
            "mode": mode,
            "dry_run": False,
            "release_candidates": candidates,
            "detail": "工作集回收会影响整机进程，需要用户确认后执行。",
        }

    targets = _select_release_targets(candidates, pids=pids, process_names=process_names)
    if not targets:
        return {
            "success": False,
            "reason_code": "TARGET_REQUIRED",
            "mode": mode,
            "release_candidates": candidates,
            "detail": "请指定要回收工作集的 PID 或进程名；不会默认扫全系统执行。",
        }

    results: list[dict[str, Any]] = []
    for target in targets:
        pid = _to_int(target.get("pid"))
        if pid is None:
            continue
        if not bool(target.get("safe_to_trim")):
            results.append({
                "success": False,
                "pid": pid,
                "name": target.get("name"),
                "reason_code": "TARGET_NOT_SAFE_TO_TRIM",
                "detail": "系统/运行时/容器进程不自动回收，请人工确认。",
            })
            continue
        trim_result = _trim_windows_working_set(pid)
        trim_result["name"] = target.get("name")
        results.append(trim_result)
    success_count = sum(1 for item in results if item.get("success") is True)
    return {
        "success": success_count > 0,
        "reason_code": None if success_count > 0 else "NO_TARGET_TRIMMED",
        "mode": mode,
        "scope": "whole_system",
        "results": results,
        "trimmed": success_count,
        "failed": len(results) - success_count,
    }


def _execute_resource_manager_status(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    create_if_missing = bool(payload.get("create_if_missing", False))
    try:
        provider = (context or {}).get("resource_manager_provider")
        if not callable(provider):
            raise RuntimeError("RESOURCE_MANAGER_PROVIDER_NOT_COMPOSED")
        manager = provider(create_if_missing=create_if_missing)
        if manager is None:
            return {
                "success": True,
                "running": False,
                "registered": False,
                "detail": "SystemResourceManager is not registered in this process.",
            }
        status = dict(manager.get_status() or {})
        return {
            "success": True,
            "registered": True,
            **status,
        }
    except Exception as exc:
        return {
            "success": False,
            "reason_code": "RESOURCE_MANAGER_STATUS_FAILED",
            "detail": str(exc),
        }


def _execute_process_list(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """列出运行中的进程。"""
    name_filter = payload.get("name_filter")
    pid_filter = payload.get("pid_filter")
    limit = int(payload.get("limit") or 50)
    sort_by = str(payload.get("sort_by") or "cpu")

    processes = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = proc.info
                if name_filter and name_filter.lower() not in (info.get("name") or "").lower():
                    continue
                if pid_filter is not None and info.get("pid") != pid_filter:
                    continue
                processes.append({
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "memory_percent": round(info.get("memory_percent") or 0.0, 2),
                    "status": info.get("status"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 排序
        sort_key = {"cpu": "cpu_percent", "memory": "memory_percent", "pid": "pid", "name": "name"}.get(sort_by, "cpu_percent")
        processes.sort(key=lambda p: p.get(sort_key) or 0, reverse=(sort_by in {"cpu", "memory"}))
        processes = processes[:limit]
    except ImportError:
        return {"processes": [], "note": "psutil 未安装"}
    except Exception as exc:
        return {"processes": [], "error": str(exc)}

    _logger.info("进程列表获取: count=%d", len(processes))
    return {"processes": processes, "count": len(processes)}


def _execute_process_kill(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """终止进程。"""
    pid = payload.get("pid")
    name = payload.get("name")
    force = bool(payload.get("force", False))
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        return {"dry_run": True, "pid": pid, "name": name}

    killed = []
    try:
        import psutil
        import signal as _signal

        sig = _signal.SIGKILL if force else _signal.SIGTERM

        if pid is not None:
            try:
                proc = psutil.Process(pid)
                proc.send_signal(sig)
                killed.append({"pid": pid, "name": proc.name()})
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                return {"killed": [], "error": str(exc)}
        elif name:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if name.lower() in (proc.info.get("name") or "").lower():
                        proc.send_signal(sig)
                        killed.append({"pid": proc.pid, "name": proc.info.get("name")})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except ImportError:
        return {"killed": [], "note": "psutil 未安装"}
    except Exception as exc:
        return {"killed": killed, "error": str(exc)}

    _logger.info("进程终止: killed=%s", killed)
    return {"killed": killed, "count": len(killed)}


def _execute_network_status(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """检测网络状态。"""
    host = str(payload.get("host") or "8.8.8.8")
    port = payload.get("port")
    timeout_ms = int(payload.get("timeout_ms") or 3000)
    get_public_ip = bool(payload.get("get_public_ip", False))

    result: dict[str, Any] = {"host": host}
    timeout_s = timeout_ms / 1000.0

    # Ping 检测
    try:
        import socket
        sock = socket.create_connection(
            (host, port or 80),
            timeout=timeout_s,
        )
        sock.close()
        result["reachable"] = True
        result["port"] = port or 80
    except Exception as exc:
        result["reachable"] = False
        result["error"] = str(exc)

    # 获取公网 IP
    if get_public_ip:
        try:
            import urllib.request
            with urllib.request.urlopen("https://api.ipify.org", timeout=timeout_s) as resp:
                result["public_ip"] = resp.read().decode("utf-8").strip()
        except Exception:
            result["public_ip"] = None

    _logger.info("网络状态检测: host=%s, reachable=%s", host, result.get("reachable"))
    return result


def _iter_open_app_terms(payload: dict[str, Any]) -> list[str]:
    raw_values = [
        payload.get("app_name"),
        payload.get("app_id"),
        payload.get("query"),
        payload.get("path"),
    ]
    terms: list[str] = []
    for raw in raw_values:
        value = str(raw or "").strip()
        if not value:
            continue
        terms.append(value)
        normalized = value.lower()
        alias = _KNOWN_APP_ALIASES.get(value) or _KNOWN_APP_ALIASES.get(normalized)
        if alias:
            for term in alias.get("search_terms", ()):
                term_text = str(term or "").strip()
                if term_text:
                    terms.append(term_text)
        else:
            for alias_key, alias_meta in _KNOWN_APP_ALIASES.items():
                alias_key_text = str(alias_key or "").strip().lower()
                if alias_key_text and alias_key_text in normalized:
                    for term in alias_meta.get("search_terms", ()):
                        term_text = str(term or "").strip()
                        if term_text:
                            terms.append(term_text)
            cleaned = (
                value.replace("帮我", "")
                .replace("替我", "")
                .replace("本地的", "")
                .replace("本地", "")
                .replace("应用程序", "")
                .replace("应用", "")
                .replace("打开", "")
                .replace("启动", "")
                .replace("一下", "")
                .replace("吧", "")
                .replace("吗", "")
                .replace("？", "")
                .replace("。", "")
                .strip()
            )
            if cleaned and cleaned.lower() != normalized:
                terms.append(cleaned)
    unique: list[str] = []
    seen: set[str] = set()
    for item in terms:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _resolve_open_app_target(payload: dict[str, Any]) -> tuple[str, str]:
    explicit_path = str(payload.get("path") or "").strip()
    if explicit_path:
        path_obj = Path(explicit_path)
        if path_obj.exists():
            return ("path", str(path_obj))

    terms = _iter_open_app_terms(payload)
    lowered_terms = tuple(term.lower() for term in terms if term)

    for root in _START_MENU_DIRS:
        if not root.exists():
            continue
        try:
            for candidate in root.rglob("*"):
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in {".lnk", ".exe", ".url"}:
                    continue
                candidate_name = candidate.stem.lower()
                if any(term in candidate_name for term in lowered_terms):
                    return ("path", str(candidate))
        except Exception:
            continue

    app_id = str(payload.get("app_id") or "").strip()
    if not app_id:
        for term in terms:
            alias = _KNOWN_APP_ALIASES.get(term) or _KNOWN_APP_ALIASES.get(term.lower())
            if alias and str(alias.get("app_id") or "").strip():
                app_id = str(alias.get("app_id")).strip()
                break
    if app_id:
        return ("app_id", app_id)

    raise FileNotFoundError(f"unable to resolve local app target from terms={terms!r}")


def _resolve_open_app_display_name(payload: dict[str, Any], *, target_kind: str, target_value: str) -> str:
    explicit_name = str(payload.get("app_name") or "").strip()
    if explicit_name:
        cleaned_name = (
            explicit_name.replace("帮我", "")
            .replace("替我", "")
            .replace("本地的", "")
            .replace("本地", "")
            .replace("应用程序", "")
            .replace("应用", "")
            .replace("软件", "")
            .replace("程序", "")
            .replace("打开", "")
            .replace("启动", "")
            .replace("运行", "")
            .replace("一下", "")
            .replace("吧", "")
            .replace("吗", "")
            .replace("？", "")
            .replace("。", "")
            .strip()
        )
        return cleaned_name or explicit_name

    query = str(payload.get("query") or "").strip()
    lowered_query = query.lower()
    for alias_key, alias_meta in _KNOWN_APP_ALIASES.items():
        search_terms = tuple(str(item or "").strip().lower() for item in alias_meta.get("search_terms", ()))
        if any(term and term in lowered_query for term in search_terms):
            return str(alias_key).strip()

    if target_kind == "path":
        try:
            return Path(target_value).stem or target_value
        except Exception:
            return target_value

    if target_kind == "app_id":
        for alias_key, alias_meta in _KNOWN_APP_ALIASES.items():
            if str(alias_meta.get("app_id") or "").strip().lower() == str(target_value or "").strip().lower():
                return str(alias_key).strip()

    return target_value


def _launch_windows_shell_app_id(app_id: str) -> dict[str, Any]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f'Start-Process "shell:AppsFolder\\\\{app_id}"',
    ]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {
        "success": True,
        "launch_mode": "app_id",
        "target": app_id,
        "pid": int(getattr(proc, "pid", 0) or 0),
        "message": f"已为你打开 {app_id}。",
    }


def _launch_windows_path(target_path: str) -> dict[str, Any]:
    os.startfile(target_path)  # type: ignore[attr-defined]
    return {
        "success": True,
        "launch_mode": "path",
        "target": target_path,
        "message": f"已经替你打开了。",
    }


def _execute_open_app(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """打开本地应用，优先使用显式路径/开始菜单快捷方式，其次回退到 shell app id。"""
    _ = context
    try:
        target_kind, target_value = _resolve_open_app_target(payload)
        if target_kind == "path":
            result = _launch_windows_path(target_value)
        else:
            result = _launch_windows_shell_app_id(target_value)
        result["app_name"] = _resolve_open_app_display_name(payload, target_kind=target_kind, target_value=target_value)
        if not str(result.get("message") or "").strip():
            result["message"] = f"已经替你打开 {result['app_name']}。"
        _logger.info("打开本地应用: app=%s target=%s mode=%s", result["app_name"], target_value, result["launch_mode"])
        return result
    except Exception as exc:
        _logger.warning("打开本地应用失败: payload=%s error=%s", payload, exc)
        return {
            "success": False,
            "reason_code": "APP_LAUNCH_FAILED",
            "detail": str(exc),
            "app_name": str(payload.get("app_name") or ""),
            "message": "抱歉，我这边没能把这个应用打开。",
        }
