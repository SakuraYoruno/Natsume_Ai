"""Observability — 服务健康检查端点。

拆分自 obs_routes_core.py（P2-7）：
  GET /obs/services/health  — 全服务健康聚合
  GET /obs/llm/nodes        — LLM 云节点独立健康状态
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from host_brain.observability.service_health_aggregator import ServiceHealthAggregator
from host_brain.observability.service_registry import (
    LLM_NODE_REGISTRY as _LLM_NODE_REGISTRY,
    EDGE_SERVICE_EVENT_PREFIXES as _EDGE_SERVICE_EVENT_PREFIXES,
)

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])


# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------
class ServiceHealthItem(BaseModel):
    service_id: str
    service_type: str
    display_name: str
    role: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    success_rate: Optional[float] = None
    p95_ms: Optional[int] = None
    last_call_ts: Optional[int] = None
    call_count: Optional[int] = None


class ServicesHealthResponse(BaseModel):
    total: int
    up: int
    degraded: int
    down: int
    unknown: int
    services: List[ServiceHealthItem]
    timestamp: int


class LlmNodeItem(BaseModel):
    node_id: str
    role: str
    provider: str
    model_id: str
    status: str
    success_rate: Optional[float] = None
    p95_ms: Optional[int] = None
    last_call_ts: Optional[int] = None
    call_count: int


class LlmNodesResponse(BaseModel):
    nodes: List[LlmNodeItem]
    timestamp: int


# ---------------------------------------------------------------------------
# 工具函数（本模块内部使用）
# ---------------------------------------------------------------------------
def _compute_node_health(rows: List[Dict], window: int = 20) -> Dict[str, Any]:
    """根据最近 window 条调用记录计算节点健康状态。"""
    if not rows:
        return {"status": "UNKNOWN", "success_rate": None, "p95_ms": None, "last_call_ts": None}
    recent = rows[-window:]
    total = len(recent)
    ok_count = sum(1 for r in recent if str(r.get("status", "")).upper() == "OK")
    success_rate = round(ok_count / total * 100, 1) if total > 0 else 0.0
    latencies = [r["latency_ms"] for r in recent if r.get("latency_ms") is not None]
    p95_ms = None
    if latencies:
        latencies.sort()
        idx = max(0, int(len(latencies) * 0.95) - 1)
        p95_ms = latencies[idx]
    last_call_ts = max((r["ts_ms"] for r in recent if r.get("ts_ms")), default=None)
    if success_rate >= 80:
        status = "UP"
    elif success_rate >= 50:
        status = "DEGRADED"
    else:
        status = "DOWN"
    return {
        "status": status,
        "success_rate": success_rate,
        "p95_ms": p95_ms,
        "last_call_ts": last_call_ts,
        "call_count": total,
    }


def _build_probe_registry(ledger, since_ms: int):
    """基于 ledger 历史数据构建被动探测函数注册表，同时返回 extra_data 字典。"""
    from host_brain.observability.service_health_aggregator import ServiceHealthResult, ALL_SERVICES as _AGGREGATOR_ALL_SERVICES
    from host_brain.observability import service_health_heartbeat as heartbeat

    now_ms = _now_ms()
    host_services_for_aggregator = [s for s in _AGGREGATOR_ALL_SERVICES if s.get("side") == "host"]
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now_ms)
    except Exception:
        voice_rows = []
    try:
        llm_rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=now_ms)
    except Exception:
        llm_rows = []

    fallback_since_ms = now_ms - 7 * 24 * 60 * 60 * 1000
    if (not voice_rows and not llm_rows) and since_ms > fallback_since_ms:
        try:
            voice_rows = ledger.fetch_by_time_range(
                "voice_event_log", start_ms=fallback_since_ms, end_ms=now_ms, limit=5000
            )
        except Exception:
            pass
        try:
            llm_rows = ledger.fetch_by_time_range(
                "llm_node_call_log", start_ms=fallback_since_ms, end_ms=now_ms, limit=5000
            )
        except Exception:
            pass

    registry = {}
    extra_data: Dict[str, Any] = {}

    for sid, prefixes in _EDGE_SERVICE_EVENT_PREFIXES.items():
        cached = heartbeat.get_cached(sid)
        if cached:
            cap = (
                str(cached.get("status") or "UNKNOWN").strip().upper(),
                cached.get("latency_ms"),
                cached.get("last_check_ts"),
                sid,
                cached.get("name") or sid.upper().replace("_", " "),
            )

            async def _make_edge_probe_cached(_sid_ignored=None, c=cap):
                st, p95, last_ts, sid_val, dname = c
                try:
                    p95_int = int(p95) if p95 is not None else None
                except (TypeError, ValueError):
                    p95_int = None
                try:
                    last_int = int(last_ts) if last_ts is not None else None
                except (TypeError, ValueError):
                    last_int = None
                return ServiceHealthResult(sid_val, dname, "edge", st, latency_ms=p95_int, last_check_ts=last_int)

            registry[sid] = _make_edge_probe_cached
            extra_data[sid] = {"success_rate": None, "call_count": 0}
            continue
        svc_rows = [
            r for r in voice_rows
            if any(str(r.get("event_type", "")).upper().startswith(p) for p in prefixes)
        ]
        health = _compute_node_health(svc_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]
        display_name = sid.upper().replace("_", " ") if sid == "edge_rpc" else sid.upper()

        async def _make_edge_probe(_sid_ignored=None, s=_status, p=_p95, l=_last, _sid=sid, _dname=display_name):
            return ServiceHealthResult(_sid, _dname, "edge", s, latency_ms=p, last_check_ts=l)

        registry[sid] = _make_edge_probe
        extra_data[sid] = {
            "success_rate": health["success_rate"],
            "call_count": health.get("call_count", 0),
        }

    for svc in host_services_for_aggregator:
        sid = svc["service_id"]
        if sid in registry:
            continue
        cached = heartbeat.get_cached(sid)
        if cached:
            cap = (
                str(cached.get("status") or "UNKNOWN").strip().upper(),
                cached.get("latency_ms"),
                cached.get("last_check_ts"),
                sid,
                cached.get("name") or svc.get("name") or sid,
            )

            async def _make_host_probe_cached(_sid_ignored=None, c=cap):
                st, p95, last_ts, sid_val, dname = c
                try:
                    p95_int = int(p95) if p95 is not None else None
                except (TypeError, ValueError):
                    p95_int = None
                try:
                    last_int = int(last_ts) if last_ts is not None else None
                except (TypeError, ValueError):
                    last_int = None
                return ServiceHealthResult(sid_val, dname, "host", st, latency_ms=p95_int, last_check_ts=last_int)

            registry[sid] = _make_host_probe_cached
            extra_data[sid] = {"success_rate": None, "call_count": 0}

    for node_id, meta in _LLM_NODE_REGISTRY.items():
        cached = heartbeat.get_cached(node_id)
        if cached:
            cap = (
                str(cached.get("status") or "UNKNOWN").strip().upper(),
                cached.get("latency_ms"),
                cached.get("last_check_ts"),
                node_id,
                meta,
            )

            async def _make_llm_probe_cached(_sid_ignored=None, c=cap):
                st, p95, last_ts, nid, m = c
                try:
                    p95_int = int(p95) if p95 is not None else None
                except (TypeError, ValueError):
                    p95_int = None
                try:
                    last_int = int(last_ts) if last_ts is not None else None
                except (TypeError, ValueError):
                    last_int = None
                return ServiceHealthResult(nid, m["role"], "host", st, latency_ms=p95_int, last_check_ts=last_int)

            registry[node_id] = _make_llm_probe_cached
            extra_data[node_id] = {
                "role": meta["role"],
                "provider": meta["provider"],
                "model_id": meta["model_id"],
                "success_rate": None,
                "call_count": 0,
            }
            continue
        node_rows = [r for r in llm_rows if r.get("node_id") == node_id]
        health = _compute_node_health(node_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]

        async def _make_llm_probe(_sid_ignored=None, s=_status, p=_p95, l=_last, nid=node_id, m=meta):
            return ServiceHealthResult(nid, m["role"], "host", s, latency_ms=p, last_check_ts=l)

        registry[node_id] = _make_llm_probe
        extra_data[node_id] = {
            "role": meta["role"],
            "provider": meta["provider"],
            "model_id": meta["model_id"],
            "success_rate": health["success_rate"],
            "call_count": health.get("call_count", 0),
        }

    return registry, extra_data


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _get_ledger():
    from host_brain.app.obs_routes_core import _get_ledger as _core_get_ledger
    return _core_get_ledger()


# ---------------------------------------------------------------------------
# GET /obs/services/health
# ---------------------------------------------------------------------------
@router.get("/services/health", response_model=ServicesHealthResponse)
async def get_services_health(
    window_minutes: int = Query(60, ge=1, le=1440, description="统计窗口（分钟）"),
    side: Optional[str] = Query(None, description="过滤端：host|edge"),
):
    """全服务健康聚合（边缘服务 + LLM 云节点）。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000
    try:
        from host_brain.observability import service_health_heartbeat as heartbeat
        if not heartbeat.has_fresh_core_service_cache():
            await heartbeat.prime_core_service_cache_once()
    except Exception:
        pass

    probe_registry, extra_data = await asyncio.to_thread(_build_probe_registry, ledger, since_ms)
    aggregator = ServiceHealthAggregator(probe_registry=probe_registry)
    result = await aggregator.aggregate(side_filter=side)

    services = []
    for svc in result.get("services", []):
        sid = svc["service_id"]
        extra = extra_data.get(sid, {})
        services.append(ServiceHealthItem(
            service_id=sid,
            service_type=svc.get("side", "edge"),
            display_name=svc.get("name", sid),
            role=extra.get("role"),
            provider=extra.get("provider"),
            model_id=extra.get("model_id"),
            status=svc["status"],
            success_rate=extra.get("success_rate"),
            p95_ms=svc.get("latency_ms"),
            last_call_ts=svc.get("last_check_ts"),
            call_count=extra.get("call_count"),
        ))

    return ServicesHealthResponse(
        total=result["total"],
        up=result["up"],
        degraded=result["degraded"],
        down=result["down"],
        unknown=result["unknown"],
        services=services,
        timestamp=_now_ms(),
    )


# ---------------------------------------------------------------------------
# GET /obs/llm/nodes
# ---------------------------------------------------------------------------
@router.get("/llm/nodes", response_model=LlmNodesResponse)
def get_llm_nodes(
    window_minutes: int = Query(60, ge=1, le=1440),
):
    """9 个 LLM 云节点独立健康状态（成功率/P95延迟/最后调用时间）。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=_now_ms())
    except Exception:
        rows = []

    nodes = []
    for node_id, meta in _LLM_NODE_REGISTRY.items():
        node_rows = [r for r in rows if r.get("node_id") == node_id]
        health = _compute_node_health(node_rows)
        nodes.append(LlmNodeItem(
            node_id=node_id,
            role=meta["role"],
            provider=meta["provider"],
            model_id=meta["model_id"],
            status=health["status"],
            success_rate=health["success_rate"],
            p95_ms=health["p95_ms"],
            last_call_ts=health["last_call_ts"],
            call_count=health.get("call_count", 0),
        ))

    return LlmNodesResponse(nodes=nodes, timestamp=_now_ms())


# ---------------------------------------------------------------------------
# D10: GET /obs/organ-tribe/status — 器官族实时状态快照
# ---------------------------------------------------------------------------
class OrganTribeAgentItem(BaseModel):
    agent_id: str
    family: str
    status: str
    key_metrics: dict
    last_updated_ts_ms: Optional[int] = None


class OrganTribeStatusResponse(BaseModel):
    total_agents: int
    active_agents: int
    agents: List[OrganTribeAgentItem]
    continuous_system_active: bool
    emotion_decay_tick_enabled: bool
    timestamp: int


@router.get("/organ-tribe/status", response_model=OrganTribeStatusResponse)
def get_organ_tribe_status():
    """D10: 器官族实时状态快照——返回 22 个器官族的当前运行状态和关键指标。

    供 WebUI 数字生命体总览页面展示器官族健康状态。
    """
    agents: List[OrganTribeAgentItem] = []

    # 定义要采集的器官族及其 export_runtime_state 调用
    _AGENT_REGISTRY = [
        ("limbic_agent",          "limbic",          "host_brain.tribe.limbic_agent",          "get_limbic_agent"),
        ("social_brain_agent",    "social_brain",    "host_brain.tribe.social_brain_agent",    "get_social_brain_agent"),
        ("prefrontal_agent",      "prefrontal",      "host_brain.tribe.prefrontal_agent",      "get_prefrontal_agent"),
        ("ans_agent",             "ans",             "host_brain.tribe.ans_agent",             "get_ans_agent"),
        ("immune_agent",          "immune",          "host_brain.tribe.immune_agent",          "get_immune_agent"),
        ("pain_reward_agent",     "pain_reward",     "host_brain.tribe.pain_reward_agent",     "get_pain_reward_agent"),
        ("hippocampus_agent",     "hippocampus",     "host_brain.tribe.hippocampus_agent",     "get_hippocampus_agent"),
        ("cortex_agent",          "cortex",          "host_brain.tribe.cortex_agent",          "get_cortex_agent"),
        ("peripheral_agent",      "peripheral",      "host_brain.tribe.peripheral_agent",      "get_peripheral_agent"),
        ("motor_cortex_agent",    "motor_cortex",    "host_brain.tribe.motor_cortex_agent",    "get_motor_cortex_agent"),
        ("language_system_agent", "language",        "host_brain.tribe.language_system_agent", "get_language_system_agent"),
        ("endocrine_agent",       "endocrine",       "host_brain.tribe.endocrine_agent",       "get_endocrine_agent"),
        ("initiative_governor_agent", "initiative_governor", "host_brain.tribe.initiative_governor_agent", "get_initiative_governor_agent"),
        ("circadian_agent",       "circadian",       "host_brain.tribe.circadian_agent",       "get_circadian_agent"),
        ("mirror_neuron_agent",   "mirror_neuron",   "host_brain.tribe.mirror_neuron_agent",   "get_mirror_neuron_agent"),
        ("somatic_agent",         "somatic",         "host_brain.tribe.somatic_agent",         "get_somatic_agent"),
        ("proprioception_agent",  "proprioception",  "host_brain.tribe.proprioception_agent",  "get_proprioception_agent"),
        ("interoception_agent",   "interoception",   "host_brain.tribe.interoception_agent",   "get_interoception_agent"),
        ("vestibular_agent",      "vestibular",      "host_brain.tribe.vestibular_agent",      "get_vestibular_agent"),
        ("glymphatic_agent",      "glymphatic",      "host_brain.tribe.glymphatic_agent",      "get_glymphatic_agent"),
        ("cerebellum_agent",      "cerebellum",      "host_brain.tribe.cerebellum_agent",      "get_cerebellum_agent"),
        ("default_mode_agent",    "default_mode",    "host_brain.tribe.default_mode_agent",    "get_default_mode_agent"),
    ]

    active_count = 0
    for agent_id, family, module_path, getter_name in _AGENT_REGISTRY:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            getter = getattr(mod, getter_name)
            agent = getter()
            state = {}
            if hasattr(agent, "export_runtime_state"):
                state = dict(agent.export_runtime_state() or {})
            # 提取关键指标（取前 6 个数值/字符串字段）
            key_metrics: dict = {}
            for k, v in state.items():
                if isinstance(v, (int, float, str, bool)) and not k.endswith("_ts_ms"):
                    key_metrics[k] = v
                    if len(key_metrics) >= 6:
                        break
            last_ts = int(state.get("last_updated_ts_ms") or state.get("last_dispatch_ts_ms") or 0)
            status = "active"
            active_count += 1
        except Exception as exc:
            key_metrics = {"error": str(exc)[:60]}
            last_ts = None
            status = "unavailable"

        agents.append(OrganTribeAgentItem(
            agent_id=agent_id,
            family=family,
            status=status,
            key_metrics=key_metrics,
            last_updated_ts_ms=last_ts if last_ts else None,
        ))

    # 检查连续动态系统是否激活（emotion_decay_tick 是否注册）
    continuous_active = False
    decay_tick_enabled = False
    try:
        from host_brain.scheduler.task_registry import SchedulerTaskRegistry
        registry = SchedulerTaskRegistry.get_instance()
        decay_task = registry.get("emotion_decay_tick")
        if decay_task is not None:
            decay_tick_enabled = True
            continuous_active = bool(getattr(decay_task, "enabled", False))
    except Exception:
        pass

    return OrganTribeStatusResponse(
        total_agents=len(agents),
        active_agents=active_count,
        agents=agents,
        continuous_system_active=continuous_active,
        emotion_decay_tick_enabled=decay_tick_enabled,
        timestamp=_now_ms(),
    )
