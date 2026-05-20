"""系统感知 Executor。

将 system_registry.py 归一化层的 payload 路由到系统 API 执行。

支持的 action：
  - info：获取系统信息
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
import subprocess
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
    except ImportError:
        result["note"] = "psutil 未安装，系统信息不可用"
    except Exception as exc:
        result["error"] = str(exc)

    if include_runtime_vitals:
        result["runtime_vitals"] = _build_runtime_vitals(
            resource_snapshot=result,
            include_gateway_status=include_gateway_status,
        )

    _logger.info("系统信息获取完成")
    return result


def _build_runtime_vitals(
    *,
    resource_snapshot: Mapping[str, Any],
    include_gateway_status: bool,
) -> dict[str, Any]:
    _ensure_runtime_env_loaded()
    ts_ms = int(time.time() * 1000)
    edge_probe = _collect_edge_rpc_health_snapshot()
    service_health = _build_service_health_snapshot(edge_probe=edge_probe, ts_ms=ts_ms)
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


def _collect_edge_rpc_health_snapshot() -> dict[str, Any]:
    try:
        from host_brain.app.startup import probe_edge_rpc_health  # noqa: PLC0415

        probe = _run_async_sync(probe_edge_rpc_health)
        return dict(probe or {}) if isinstance(probe, Mapping) else {}
    except Exception as exc:
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"edge rpc health probe failed: {exc.__class__.__name__}: {exc}",
            "event": {},
        }


def _build_service_health_snapshot(*, edge_probe: Mapping[str, Any], ts_ms: int) -> dict[str, Any]:
    try:
        from host_brain.app.config import build_host_runtime_statuses, load_host_config  # noqa: PLC0415
        from host_brain.app.main import build_service_health_report  # noqa: PLC0415
        from host_brain.app.startup import build_edge_dependency_probe_statuses  # noqa: PLC0415

        runtime_statuses: dict[str, Any] = {}
        host_result = load_host_config()
        if host_result.success and host_result.config is not None:
            runtime_statuses.update(build_host_runtime_statuses(host_result.config))
        else:
            runtime_statuses["host.audit.sqlite_ledger"] = {
                "available": False,
                "unavailable_kind": "dependency",
                "detail": f"host config unavailable: {host_result.reason_code}",
            }
        runtime_statuses.update(build_edge_dependency_probe_statuses(edge_probe))
        report = build_service_health_report(
            runtime_statuses=runtime_statuses,
            request_id=f"system_vitals_{ts_ms}",
            ledger=None,
            ts_ms=ts_ms,
        )
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
    cpu_percent = _to_float(cpu.get("percent"))
    memory_percent = _to_float(memory.get("percent"))
    disk_percent = _to_float(disk.get("percent"))
    status = "NORMAL"
    reasons: list[str] = []
    if cpu_percent is not None and cpu_percent >= 90:
        status = "HIGH"
        reasons.append("cpu_high")
    elif cpu_percent is not None and cpu_percent >= 75:
        status = "ELEVATED"
        reasons.append("cpu_elevated")
    if memory_percent is not None and memory_percent >= 88:
        status = "HIGH"
        reasons.append("memory_high")
    elif memory_percent is not None and memory_percent >= 78 and status == "NORMAL":
        status = "ELEVATED"
        reasons.append("memory_elevated")
    if disk_percent is not None and disk_percent >= 92:
        status = "HIGH"
        reasons.append("disk_high")
    elif disk_percent is not None and disk_percent >= 85 and status == "NORMAL":
        status = "ELEVATED"
        reasons.append("disk_elevated")
    return {
        "status": status,
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "disk_percent": disk_percent,
        "reasons": reasons,
    }


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
