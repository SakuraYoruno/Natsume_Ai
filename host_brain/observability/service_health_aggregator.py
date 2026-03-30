"""
ServiceHealthAggregator — M18-A
并发探测双端所有已注册服务健康状态，聚合返回统一结构。
FR-OBS-008 / TASK-0188
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from host_brain.observability.service_registry import ALL_SERVICES

logger = logging.getLogger("Natsume.obs.health")

# ---------------------------------------------------------------------------
# 健康探测结果结构
# ---------------------------------------------------------------------------
class ServiceHealthResult:
    __slots__ = (
        "service_id", "name", "side", "status",
        "latency_ms", "version", "last_check_ts", "error_summary",
    )

    def __init__(
        self,
        service_id: str,
        name: str,
        side: str,
        status: str,
        latency_ms: Optional[int] = None,
        version: Optional[str] = None,
        last_check_ts: Optional[int] = None,
        error_summary: Optional[str] = None,
    ):
        self.service_id    = service_id
        self.name          = name
        self.side          = side
        self.status        = status
        self.latency_ms    = latency_ms
        self.version       = version
        self.last_check_ts = last_check_ts or int(time.time() * 1000)
        self.error_summary = error_summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_id":    self.service_id,
            "name":          self.name,
            "side":          self.side,
            "status":        self.status,
            "latency_ms":    self.latency_ms,
            "version":       self.version,
            "last_check_ts": self.last_check_ts,
            "error_summary": self.error_summary,
        }


# ---------------------------------------------------------------------------
# 单服务探测（带超时保护）
# ---------------------------------------------------------------------------
async def _probe_service(
    svc: Dict[str, str],
    probe_fn,
    timeout_ms: int = 200,
) -> ServiceHealthResult:
    """
    探测单个服务健康状态。
    probe_fn 是可选的异步探测函数，签名：async (service_id: str) -> ServiceHealthResult
    若 probe_fn 为 None 或超时，返回 UNKNOWN 状态。
    """
    sid  = svc["service_id"]
    name = svc["name"]
    side = svc["side"]

    if probe_fn is None:
        return ServiceHealthResult(sid, name, side, "UNKNOWN")

    start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            probe_fn(sid),
            timeout=timeout_ms / 1000.0,
        )
        elapsed = int((time.monotonic() - start) * 1000)
        if result is None:
            return ServiceHealthResult(sid, name, side, "UNKNOWN", latency_ms=elapsed)
        result.latency_ms = result.latency_ms or elapsed
        return result
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("服务探测超时: %s (%dms)", sid, elapsed)
        return ServiceHealthResult(
            sid, name, side, "DOWN",
            latency_ms=elapsed,
            error_summary=f"探测超时 (>{timeout_ms}ms)",
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.warning("服务探测异常: %s — %s", sid, exc)
        return ServiceHealthResult(
            sid, name, side, "DOWN",
            latency_ms=elapsed,
            error_summary=str(exc)[:200],
        )


# ---------------------------------------------------------------------------
# ServiceHealthAggregator
# ---------------------------------------------------------------------------
class ServiceHealthAggregator:
    """
    并发探测所有已注册服务，聚合返回健康状态快照。
    支持 ?side=host|edge 过滤。
    """

    def __init__(self, probe_registry: Optional[Dict[str, Any]] = None, timeout_ms: int = 200):
        """
        :param probe_registry: {service_id: async_probe_fn} 映射，可选
        :param timeout_ms: 单服务探测超时（毫秒）
        """
        self._probes     = probe_registry or {}
        self._timeout_ms = timeout_ms

    async def aggregate(self, side_filter: Optional[str] = None) -> Dict[str, Any]:
        """
        并发探测所有服务，返回聚合结果。
        :param side_filter: 'host' | 'edge' | None（全部）
        """
        services = ALL_SERVICES
        if side_filter in ("host", "edge"):
            services = [s for s in ALL_SERVICES if s["side"] == side_filter]

        tasks = [
            _probe_service(svc, self._probes.get(svc["service_id"]), self._timeout_ms)
            for svc in services
        ]
        results: List[ServiceHealthResult] = await asyncio.gather(*tasks)

        # 统计
        counts = {"UP": 0, "DOWN": 0, "DEGRADED": 0, "UNKNOWN": 0}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1

        return {
            "total":     len(results),
            "up":        counts["UP"],
            "down":      counts["DOWN"],
            "degraded":  counts["DEGRADED"],
            "unknown":   counts["UNKNOWN"],
            "services":  [r.to_dict() for r in results],
            "timestamp": int(time.time() * 1000),
        }

    def register_probe(self, service_id: str, probe_fn) -> None:
        """注册单个服务的探测函数。"""
        self._probes[service_id] = probe_fn


# ---------------------------------------------------------------------------
# M26-B：validate_health_response() — 响应结构完整性校验
# ---------------------------------------------------------------------------
_REQUIRED_TOP_KEYS = {"total", "up", "down", "unknown", "services", "timestamp"}
_REQUIRED_SERVICE_KEYS = {"service_id", "name", "side", "status"}


def validate_health_response(response: Dict[str, Any]) -> bool:
    """
    校验 ServiceHealthAggregator.aggregate() 返回的响应结构完整性。

    规则：
    - 顶层必须包含 total / up / down / unknown / services / timestamp；
    - services 必须是列表；
    - 每个 service 条目必须包含 service_id / name / side / status；
    - 任何字段缺失返回 False，不抛异常。

    :param response: aggregate() 返回的字典
    :return: True（结构完整）/ False（结构缺失）
    """
    if not isinstance(response, dict):
        return False

    # 校验顶层字段
    if not _REQUIRED_TOP_KEYS.issubset(response.keys()):
        missing = _REQUIRED_TOP_KEYS - response.keys()
        logger.warning("health 响应缺少顶层字段: %s", missing)
        return False

    # services 必须是列表
    services = response.get("services")
    if not isinstance(services, list):
        logger.warning("health 响应 services 字段不是列表: %s", type(services))
        return False

    # 校验每个 service 条目
    for i, svc in enumerate(services):
        if not isinstance(svc, dict):
            logger.warning("health 响应 services[%d] 不是字典", i)
            return False
        if not _REQUIRED_SERVICE_KEYS.issubset(svc.keys()):
            missing = _REQUIRED_SERVICE_KEYS - svc.keys()
            logger.warning("health 响应 services[%d] 缺少字段: %s", i, missing)
            return False

    return True
