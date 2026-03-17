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

logger = logging.getLogger("Natsume.obs.heartbeat")

# 缓存：service_id -> { "status", "latency_ms", "last_check_ts", "name", "side" }
_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()

# 缓存有效期（秒），超过则 GET /obs/services/health 仍回退到 ledger
CACHE_TTL_SEC = 90

# 与 ServiceHealthAggregator / obs_routes 一致：边缘 5 个 + Host 侧用 LLM 节点 id（registry 的 key）
EDGE_SERVICE_IDS = ["asr", "tts", "vts", "audio_playback", "edge_rpc"]
# obs_routes 的 registry 用 _LLM_NODE_REGISTRY 的 key，心跳需覆盖以便 get_cached(node_id) 命中
LLM_NODE_IDS = [
    "chat_direct_ja", "core_reasoner_primary", "core_reasoner_secondary",
    "planner_constraints", "tool_planner", "safety_verifier", "consistency_verifier",
    "renderer_ja_c_style", "workbench_translate", "workbench_code_assist",
]
LLM_NODE_DISPLAY_NAMES = {
    "chat_direct_ja": "聊天直出",
    "core_reasoner_primary": "主推理",
    "core_reasoner_secondary": "备用推理",
    "planner_constraints": "规划约束",
    "tool_planner": "工具规划",
    "safety_verifier": "安全验证",
    "consistency_verifier": "一致性验证",
    "renderer_ja_c_style": "日文渲染",
    "workbench_translate": "翻译工作台",
    "workbench_code_assist": "代码辅助",
}
_HOST_SERVICE_IDS = [
    "llm_gateway", "neo4j", "sqlite_ledger", "chroma", "scheduler",
    "bond_engine", "memory_distiller", "mcp_provider", "embedding", "reranker",
    "main_chain", "routing_matrix", "memory_guard", "state_arbiter", "recall_pack_builder",
]
_HOST_DISPLAY_NAMES = {
    "llm_gateway": "LLM Gateway",
    "neo4j": "Neo4j Bond SSOT",
    "sqlite_ledger": "SQLite Ledger",
    "chroma": "Chroma Vector Store",
    "scheduler": "Scheduler",
    "bond_engine": "Bond Engine",
    "memory_distiller": "Memory Distiller",
    "mcp_provider": "MCP Provider",
    "embedding": "Embedding Service",
    "reranker": "Reranker Service",
    "main_chain": "MainChain Orchestrator",
    "routing_matrix": "LLM Routing Matrix",
    "memory_guard": "Memory Guard",
    "state_arbiter": "State Arbiter",
    "recall_pack_builder": "Recall Pack Builder",
}
_EDGE_DISPLAY_NAMES = {
    "asr": "ASR (Whisper)",
    "tts": "TTS (GPT-SoVITS)",
    "vts": "VTS / Live2D",
    "audio_playback": "Audio Playback",
    "edge_rpc": "Edge RPC Server",
}


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
    """探测 Host 进程：GET /health。返回 { status, latency_ms } 或 DOWN。启动阶段失败时重试一次。"""
    url = _get_host_health_url()
    for attempt in (0, 1):
        t0 = time.monotonic()
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                return {"status": "UP", "latency_ms": elapsed_ms}
            return {"status": "DEGRADED", "latency_ms": elapsed_ms}
        except Exception as exc:
            logger.debug("Heartbeat Host probe attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(3.0)
                continue
            return {"status": "DOWN", "latency_ms": None}
    return {"status": "DOWN", "latency_ms": None}


async def run_once() -> None:
    """执行一轮心跳探测并更新缓存。Edge/Host 分别探测，任一侧失败也写入该侧缓存，避免单侧异常导致整轮不写缓存。"""
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
        host_result = {"status": "DOWN", "latency_ms": None}

    with _cache_lock:
        for sid in EDGE_SERVICE_IDS:
            _cache[sid] = {
                "status": edge_result["status"],
                "latency_ms": edge_result.get("latency_ms"),
                "last_check_ts": now_ts,
                "name": _EDGE_DISPLAY_NAMES.get(sid, sid),
                "side": "edge",
            }
        for sid in _HOST_SERVICE_IDS:
            _cache[sid] = {
                "status": host_result["status"],
                "latency_ms": host_result.get("latency_ms"),
                "last_check_ts": now_ts,
                "name": _HOST_DISPLAY_NAMES.get(sid, sid),
                "side": "host",
            }
        for sid in LLM_NODE_IDS:
            _cache[sid] = {
                "status": host_result["status"],
                "latency_ms": host_result.get("latency_ms"),
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
    """停止心跳任务（shutdown 时调用）。"""
    global _heartbeat_task
    if _heartbeat_task is None:
        return
    _heartbeat_task.cancel()
    try:
        await _heartbeat_task
    except asyncio.CancelledError:
        pass
    _heartbeat_task = None
