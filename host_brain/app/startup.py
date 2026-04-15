"""startup.py — 进程启动初始化模块。

职责：
  - 服务依赖探测（Edge RPC 健康检查）
  - 启动时预热（EmotionTimeline、SkillRegistry 等）
  - 提供 warmup_all() 供 runtime_lifespan 调用

与 dependencies.py 的分工：
  - dependencies.py：只保留 get_xxx() 懒加载工厂函数（单例获取）
  - startup.py：启动时主动调用的初始化/预热/探测逻辑
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Mapping

_logger = logging.getLogger("Natsume.startup")


# ---------------------------------------------------------------------------
# Edge RPC 健康探测（从 dependencies.py 迁移）
# ---------------------------------------------------------------------------

_EDGE_RPC_DEPENDENCY_SERVICE_IDS = (
    "edge.rpc.gateway",
    "edge.rpc.asr",
    "edge.rpc.tts",
    "edge.rpc.barge_in",
    "edge.rpc.resource_gate",
)
_EDGE_RPC_COMPONENT_BY_SERVICE_ID = {
    "edge.rpc.asr": "asr",
    "edge.rpc.tts": "tts",
    "edge.rpc.barge_in": "voice_pipeline",
    "edge.rpc.resource_gate": "resource_gate",
}


def _is_edge_rpc_enabled() -> bool:
    raw = str(os.environ.get("HOST_ENABLE_EDGE_RPC") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_edge_probe_timeout_s() -> float:
    raw = str(os.environ.get("HOST_EDGE_RPC_TIMEOUT_MS") or "3000").strip()
    try:
        timeout_ms = int(raw)
    except ValueError:
        timeout_ms = 3000
    return max(1.0, min(float(timeout_ms) / 1000.0, 10.0))


def _build_edge_rpc_skip_detail() -> str:
    from host_brain.app.dependencies import get_edge_base_url  # noqa: PLC0415
    return f"edge rpc skipped: gateway-only mode ({get_edge_base_url()})"


async def probe_edge_rpc_health() -> dict:
    """探测 Edge RPC 服务健康状态（GET /v1/edge/health）。

    从 dependencies.py 迁移，供 startup 和 health_routes 调用。
    """
    import time
    from host_brain.app.dependencies import get_edge_base_url, get_edge_base_urls  # noqa: PLC0415

    t0 = int(time.time() * 1000)
    if not _is_edge_rpc_enabled():
        return {
            "available": True,
            "status": "SKIPPED",
            "detail": _build_edge_rpc_skip_detail(),
            "latency_ms": 0,
            "base_url": "",
            "event": {},
        }
    base_urls = get_edge_base_urls()
    base_url = base_urls[0] if base_urls else get_edge_base_url()
    try:
        import httpx
        last_error_detail = "edge rpc unreachable"
        attempts: list[str] = []
        for candidate in base_urls:
            try:
                async with httpx.AsyncClient(timeout=_resolve_edge_probe_timeout_s(), trust_env=False) as client:
                    resp = await client.get(f"{candidate}/v1/edge/health")
                latency_ms = max(0, int(time.time() * 1000) - t0)
                if resp.status_code == 200:
                    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                    return {
                        "available": True,
                        "status": str(body.get("status") or "OK"),
                        "detail": "edge rpc reachable",
                        "latency_ms": latency_ms,
                        "base_url": candidate,
                        "event": dict(body.get("event") or {}) if isinstance(body, Mapping) and isinstance(body.get("event"), Mapping) else {},
                    }
                last_error_detail = f"edge health returned HTTP {resp.status_code}"
                attempts.append(f"{candidate}:HTTP{resp.status_code}")
            except Exception as candidate_exc:
                last_error_detail = f"edge rpc unreachable: {candidate_exc.__class__.__name__}"
                attempts.append(f"{candidate}:{candidate_exc.__class__.__name__}")
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"{last_error_detail}; attempted={', '.join(attempts[-6:])}",
            "latency_ms": max(0, int(time.time() * 1000) - t0),
            "base_url": base_url,
        }
    except ImportError:
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": "httpx 未安装，无法探测 Edge RPC",
            "latency_ms": 0,
            "base_url": base_url,
        }
    except Exception as exc:
        latency_ms = max(0, int(time.time() * 1000) - t0)
        return {
            "available": False,
            "status": "DEGRADED",
            "detail": f"edge rpc unreachable: {exc.__class__.__name__}: {exc!s}",
            "latency_ms": latency_ms,
            "base_url": base_url,
        }


def build_edge_dependency_probe_statuses(
    probe: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """将 probe_edge_rpc_health 结果转换为各服务的可用性状态。"""
    if not isinstance(probe, Mapping):
        return {}

    available = bool(probe.get("available", False))
    status = str(probe.get("status") or "").strip().upper()
    detail = str(probe.get("detail") or "").strip()
    latency_raw = probe.get("latency_ms")
    try:
        latency_ms = max(0, int(latency_raw))
    except (TypeError, ValueError):
        latency_ms = 0

    if status == "SKIPPED":
        if not detail:
            detail = _build_edge_rpc_skip_detail()
        return {
            service_id: {"available": True, "detail": detail}
            for service_id in _EDGE_RPC_DEPENDENCY_SERVICE_IDS
        }

    if not detail:
        detail = "edge rpc reachable" if available else "edge rpc unreachable"
    if available and latency_ms > 0:
        detail = f"{detail} ({latency_ms}ms)"

    event = probe.get("event")
    event_payload = event if isinstance(event, Mapping) else {}
    services = event_payload.get("services") if isinstance(event_payload.get("services"), Mapping) else {}
    components = event_payload.get("components") if isinstance(event_payload.get("components"), Mapping) else {}

    if not available:
        status_dict = {"available": False, "unavailable_kind": "edge_rpc", "detail": detail}
        return {sid: dict(status_dict) for sid in _EDGE_RPC_DEPENDENCY_SERVICE_IDS}

    statuses: dict[str, dict[str, Any]] = {"edge.rpc.gateway": {"available": True, "detail": detail}}
    for service_id in _EDGE_RPC_DEPENDENCY_SERVICE_IDS:
        if service_id == "edge.rpc.gateway":
            continue
        service_probe = services.get(service_id) or {}
        component_name = _EDGE_RPC_COMPONENT_BY_SERVICE_ID.get(service_id)
        component_probe = (components.get(component_name) if component_name else None) or {}
        statuses[service_id] = _build_edge_component_probe_status(
            gateway_detail=detail,
            service_probe=service_probe,
            component_probe=component_probe,
        )
    return statuses


def _build_edge_component_probe_status(
    *,
    gateway_detail: str,
    service_probe: Mapping[str, Any],
    component_probe: Mapping[str, Any],
) -> dict[str, Any]:
    service_detail = str(service_probe.get("detail") or "").strip()
    component_detail = str(component_probe.get("detail") or "").strip()
    component_status = str(component_probe.get("status") or "").strip().lower()
    component_enabled = bool(component_probe.get("enabled", True)) if component_probe else True
    component_available = bool(component_probe.get("available", False)) if component_probe else False

    if service_probe and not bool(service_probe.get("available", False)):
        return {"available": False, "unavailable_kind": "dependency", "detail": service_detail or gateway_detail}

    if component_probe:
        if not component_enabled:
            return {"available": False, "unavailable_kind": "dependency", "detail": component_detail or service_detail or "edge component disabled by config"}
        if (not component_available) or (component_status and component_status != "ok"):
            return {"available": False, "unavailable_kind": "dependency", "detail": component_detail or service_detail or gateway_detail}
        detail_parts = [p for p in (component_detail, service_detail, gateway_detail) if p]
        return {"available": True, "detail": "; ".join(detail_parts)}

    if service_probe:
        detail_parts = [p for p in (service_detail, gateway_detail) if p]
        return {"available": True, "detail": "; ".join(detail_parts)}

    return {"available": True, "detail": gateway_detail}


async def collect_dependency_probes() -> dict[str, dict[str, Any]]:
    """收集所有依赖服务的探测结果。"""
    return build_edge_dependency_probe_statuses(await probe_edge_rpc_health())


def collect_dependency_probes_sync() -> dict[str, dict[str, Any]]:
    """同步版本的依赖探测（无事件循环时使用）。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(collect_dependency_probes())
    return {}


# ---------------------------------------------------------------------------
# 启动预热函数（从 dependencies.py 中的 get_xxx 内部逻辑提取）
# ---------------------------------------------------------------------------

def warmup_emotion_timeline() -> str:
    """预热 EmotionTimeline：从 SQLite 加载最近 24h 情绪记录。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        from host_brain.app.dependencies import get_emotion_timeline  # noqa: PLC0415
        get_emotion_timeline()
        return "OK"
    except Exception as exc:
        _logger.warning("EmotionTimeline 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_skill_registry() -> str:
    """预热 SkillRegistry：触发 MCP 工具注册和内置技能加载。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        registry = get_skill_registry()
        skill_count = len(list(registry.all() or []))
        _logger.info("SkillRegistry 预热完成，已加载 %d 个技能", skill_count)
        return "OK"
    except Exception as exc:
        _logger.warning("SkillRegistry 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_graph_runtime() -> str:
    """预热 GraphRuntimeContract：触发 handler 注册。

    返回状态字符串（"OK" / "DEGRADED: ..."）。
    """
    try:
        from host_brain.app.dependencies import get_graph_runtime  # noqa: PLC0415
        get_graph_runtime()
        return "OK"
    except Exception as exc:
        _logger.warning("GraphRuntime 预热失败（降级）: %s", exc)
        return f"DEGRADED: {exc}"


def warmup_all(*, eager: bool = False) -> dict[str, str]:
    """执行所有启动预热，返回各模块状态字典。

    Args:
        eager: True 时预热所有模块（含慢速模块）；False 时跳过慢速模块（按需初始化）

    Returns:
        {"module_name": "OK" / "DEGRADED: ..." / "SKIPPED"}
    """
    statuses: dict[str, str] = {}

    # 快速预热（始终执行）
    statuses["skill_registry"] = warmup_skill_registry()
    statuses["graph_runtime"] = warmup_graph_runtime()

    # 慢速预热（仅 eager 模式）
    if eager:
        statuses["emotion_timeline"] = warmup_emotion_timeline()
    else:
        statuses["emotion_timeline"] = "SKIPPED (lazy init)"

    _logger.info(
        "startup.warmup_all 完成: %s",
        {k: v for k, v in statuses.items()},
    )
    return statuses


__all__ = [
    "probe_edge_rpc_health",
    "build_edge_dependency_probe_statuses",
    "collect_dependency_probes",
    "collect_dependency_probes_sync",
    "warmup_emotion_timeline",
    "warmup_skill_registry",
    "warmup_graph_runtime",
    "warmup_all",
]
