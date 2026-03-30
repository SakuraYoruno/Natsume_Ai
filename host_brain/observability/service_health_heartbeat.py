"""
服务健康心跳 — 定时主动探测 Edge/Host，结果写入缓存供 GET /obs/services/health 使用。
避免无业务流量时全部显示「未知」。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from host_brain.observability.service_registry import (
    EDGE_SERVICE_IDS,
    EDGE_DISPLAY_NAMES as _EDGE_DISPLAY_NAMES,
    HOST_SERVICE_IDS as _HOST_SERVICE_IDS,
    HOST_DISPLAY_NAMES as _HOST_DISPLAY_NAMES,
    LLM_NODE_IDS,
    LLM_NODE_DISPLAY_NAMES,
)

logger = logging.getLogger("Natsume.obs.heartbeat")

# 缓存：service_id -> { "status", "latency_ms", "last_check_ts", "name", "side" }
_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()

# 缓存有效期（秒），超过则 GET /obs/services/health 仍回退到 ledger
CACHE_TTL_SEC = 90


# /health 端点返回的 service_id → 心跳模块 service_id 映射
# /health 使用 "host.audit.sqlite_ledger" 格式，心跳模块使用 "sqlite_ledger" 格式
_HEALTH_SID_TO_HEARTBEAT_SID: Dict[str, str] = {
    "host.audit.sqlite_ledger":       "sqlite_ledger",
    "host.governor.decision":         "llm_gateway",       # governor 依赖 LLM，映射到 llm_gateway
    "host.skills.loader":             "scheduler",          # skills loader 映射到 scheduler
    "host.integrations.llm_gateway":  "llm_gateway",
    "host.retrieval.embedding":       "embedding",
    "host.retrieval.reranker":        "reranker",
    # Edge 侧映射
    "edge.rpc.gateway":               "edge_rpc",
    "edge.rpc.asr":                   "asr",
    "edge.rpc.tts":                   "tts",
    "edge.rpc.barge_in":              "audio_playback",
    "edge.rpc.resource_gate":         "audio_playback",
}

# 模块级 httpx.AsyncClient 单例（复用 TCP 连接，避免每次探测都创建/销毁）
_http_client: Optional[Any] = None
_http_client_lock = threading.Lock()


def _get_http_client():
    """获取或创建模块级 httpx.AsyncClient 单例。"""
    global _http_client
    with _http_client_lock:
        if _http_client is None:
            import httpx
            _http_client = httpx.AsyncClient(timeout=5.0)
        return _http_client


async def close_http_client() -> None:
    """关闭 httpx 客户端（shutdown 时调用）。"""
    global _http_client
    with _http_client_lock:
        if _http_client is not None:
            try:
                await _http_client.aclose()
            except Exception:
                pass
            _http_client = None


def _get_host_health_url() -> str:
    host = os.environ.get("HOST_HTTP_HOST", "127.0.0.1").strip()
    port = os.environ.get("HOST_HTTP_PORT", "18080").strip()
    return f"http://{host}:{port}/health"


async def _probe_edge() -> Dict[str, Any]:
    """探测 Edge RPC：GET /v1/edge/health。返回 { status, latency_ms } 或 DOWN。Edge 正常时返回 status=ACCEPTED，视为 UP。"""
    try:
        from host_brain.app.dependencies import probe_edge_rpc_health
        out = await probe_edge_rpc_health()
        st = (out.get("status") or "").strip().upper()
        ok = out.get("available") and st in ("OK", "UP", "ACCEPTED")
        return {
            "status": "UP" if ok else "DEGRADED",
            "latency_ms": out.get("latency_ms"),
        }
    except Exception as exc:
        logger.debug("Heartbeat Edge probe failed: %s", exc)
        return {"status": "DOWN", "latency_ms": None}


async def _probe_host() -> Dict[str, Any]:
    """探测 Host 进程：GET /health，解析返回的 details.services 获取各子服务独立状态。

    返回结构：
    {
        "overall_status": "UP"|"DEGRADED"|"DOWN",
        "latency_ms": int,
        "sub_services": {  # 各子服务独立状态
            "sqlite_ledger": {"status": "UP", ...},
            "llm_gateway": {"status": "DOWN", ...},
            ...
        }
    }
    """
    url = _get_host_health_url()
    client = _get_http_client()
    for attempt in (0, 1):
        t0 = time.monotonic()
        try:
            resp = await client.get(url)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                body = resp.json()
                details = body.get("details") or {}
                services_list = details.get("services") or []
                sub_services: Dict[str, Dict[str, Any]] = {}
                for svc in services_list:
                    health_sid = svc.get("service_id", "")
                    heartbeat_sid = _HEALTH_SID_TO_HEARTBEAT_SID.get(health_sid)
                    if not heartbeat_sid:
                        continue
                    svc_status = str(svc.get("status", "UNKNOWN")).strip().upper()
                    sub_services[heartbeat_sid] = {
                        "status": svc_status,
                        "detail": svc.get("detail"),
                    }
                return {
                    "overall_status": "UP",
                    "latency_ms": elapsed_ms,
                    "sub_services": sub_services,
                }
            return {"overall_status": "DEGRADED", "latency_ms": elapsed_ms, "sub_services": {}}
        except Exception as exc:
            logger.debug("Heartbeat Host probe attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(3.0)
                continue
            return {"overall_status": "DOWN", "latency_ms": None, "sub_services": {}}
    return {"overall_status": "DOWN", "latency_ms": None, "sub_services": {}}


async def run_once() -> None:
    """执行一轮心跳探测并更新缓存。

    Edge/Host 分别探测，任一侧失败也写入该侧缓存，避免单侧异常导致整轮不写缓存。
    Host 侧：解析 /health 返回的各子服务独立状态，不再一刀切广播。
    Edge 侧：从 /health 返回的 edge.rpc.* 子服务中提取独立状态。
    LLM 节点：使用 Host 整体状态（LLM 云节点的可用性取决于 Host 进程是否存活）。
    """
    now_ts = int(time.time() * 1000)
    try:
        edge_result = await _probe_edge()
    except Exception as exc:
        logger.debug("Heartbeat Edge probe failed (will cache DOWN): %s", exc)
        edge_result = {"status": "DOWN", "latency_ms": None}
    try:
        host_result = await _probe_host()
    except Exception as exc:
        logger.debug("Heartbeat Host probe failed (will cache DOWN): %s", exc)
        host_result = {"overall_status": "DOWN", "latency_ms": None, "sub_services": {}}

    host_overall = host_result.get("overall_status", "DOWN")
    host_latency = host_result.get("latency_ms")
    sub_services = host_result.get("sub_services") or {}

    with _cache_lock:
        # Edge 服务：优先使用 /health 返回的子服务独立状态，否则回退到 Edge 整体探测结果
        for sid in EDGE_SERVICE_IDS:
            sub = sub_services.get(sid)
            if sub:
                status = sub["status"]
            else:
                status = edge_result["status"]
            _cache[sid] = {
                "status": status,
                "latency_ms": edge_result.get("latency_ms"),
                "last_check_ts": now_ts,
                "name": _EDGE_DISPLAY_NAMES.get(sid, sid),
                "side": "edge",
            }
        # Host 服务：使用各子服务独立状态，无映射的回退到 Host 整体状态
        for sid in _HOST_SERVICE_IDS:
            sub = sub_services.get(sid)
            if sub:
                status = sub["status"]
            else:
                status = host_overall
            _cache[sid] = {
                "status": status,
                "latency_ms": host_latency,
                "last_check_ts": now_ts,
                "name": _HOST_DISPLAY_NAMES.get(sid, sid),
                "side": "host",
            }
        # LLM 节点：使用 Host 整体状态（LLM 云节点可用性取决于 Host 进程）
        for sid in LLM_NODE_IDS:
            _cache[sid] = {
                "status": host_overall,
                "latency_ms": host_latency,
                "last_check_ts": now_ts,
                "name": LLM_NODE_DISPLAY_NAMES.get(sid, sid),
                "side": "host",
            }


def get_cached(service_id: str) -> Optional[Dict[str, Any]]:
    """若缓存存在且未过期，返回该服务的缓存条目，否则返回 None。"""
    with _cache_lock:
        entry = _cache.get(service_id)
    if not entry:
        return None
    last_ts = entry.get("last_check_ts") or 0
    if (time.time() * 1000 - last_ts) / 1000.0 > CACHE_TTL_SEC:
        return None
    return dict(entry)


# 首次探测前等待（秒），避免 lifespan 刚启动时 /health 尚未监听导致全部被标为 DOWN
HEARTBEAT_INITIAL_DELAY_SEC = 8.0

async def heartbeat_loop(interval_sec: float = 30.0) -> None:
    """后台循环：每 interval_sec 执行一次 run_once()，直到任务被取消。"""
    logger.info("Service health heartbeat started (interval=%.0fs, initial_delay=%.0fs)", interval_sec, HEARTBEAT_INITIAL_DELAY_SEC)
    await asyncio.sleep(HEARTBEAT_INITIAL_DELAY_SEC)
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            logger.info("Service health heartbeat stopped")
            raise
        except Exception as exc:
            logger.warning("Heartbeat run_once failed: %s", exc)
        await asyncio.sleep(interval_sec)


_heartbeat_task: Optional[asyncio.Task] = None


def start_heartbeat(interval_sec: float = 30.0) -> asyncio.Task:
    """启动心跳后台任务。应在 FastAPI lifespan 中调用。"""
    global _heartbeat_task
    if _heartbeat_task is not None and not _heartbeat_task.done():
        return _heartbeat_task
    _heartbeat_task = asyncio.create_task(heartbeat_loop(interval_sec))
    return _heartbeat_task


async def stop_heartbeat() -> None:
    """停止心跳任务并关闭 HTTP 客户端（shutdown 时调用）。"""
    global _heartbeat_task
    if _heartbeat_task is None:
        return
    _heartbeat_task.cancel()
    try:
        await _heartbeat_task
    except asyncio.CancelledError:
        pass
    _heartbeat_task = None
    await close_http_client()
