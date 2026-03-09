"""Observability REST API — M18 监控面板后端数据接口。

提供 M17 React 前端所需的全套监控数据端点：
  GET /obs/services/health     — 全服务健康聚合（边缘服务 + 9 个 LLM 云节点）
  GET /obs/sessions            — 会话列表（分页 + 过滤）
  GET /obs/trace/{request_id}  — 单次会话完整链路节点详情
  GET /obs/metrics/timeseries  — 多维度时序数据（请求量/拒绝/延迟）
  GET /obs/llm/nodes           — LLM 云节点独立健康状态
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.observability.service_health_aggregator import ServiceHealthAggregator
from host_brain.observability.session_query import SessionQueryService
from host_brain.observability.metrics_timeseries import MetricsTimeseriesService

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])

# ---------------------------------------------------------------------------
# 9 个 LLM 云节点静态注册表（来自 node_registry.yaml）
# ---------------------------------------------------------------------------
_LLM_NODE_REGISTRY: Dict[str, Dict[str, str]] = {
    "chat_direct_ja":           {"role": "聊天直出",      "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "core_reasoner_primary":    {"role": "主推理",      "provider": "gemini", "model_id": "gemini-2.5-pro"},
    "core_reasoner_secondary":  {"role": "备用推理",    "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "planner_constraints":      {"role": "规划约束",    "provider": "gemini", "model_id": "gemini-2.5-pro"},
    "tool_planner":             {"role": "工具规划",    "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "safety_verifier":          {"role": "安全验证",    "provider": "gemini", "model_id": "gemini-2.5-flash-lite"},
    "consistency_verifier":     {"role": "一致性验证",  "provider": "gemini", "model_id": "gemini-2.5-flash-lite"},
    "renderer_ja_c_style":      {"role": "日文渲染",    "provider": "gemini", "model_id": "gemini-2.5-flash"},
    "workbench_translate":      {"role": "翻译工作台",  "provider": "gemini", "model_id": "gemini-2.5-pro"},
    "workbench_code_assist":    {"role": "代码辅助",    "provider": "gemini", "model_id": "gemini-2.5-pro"},
}

# 边缘服务列表
_EDGE_SERVICES = ["ASR", "TTS", "VTS", "VOICE_RESOURCE_GATE", "AIRLOCK", "GOVERNOR"]

# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------
_ledger_provider = None

def set_ledger_provider(provider) -> None:
    global _ledger_provider
    _ledger_provider = provider

def _get_ledger() -> SQLiteLedger:
    if _ledger_provider is not None:
        return _ledger_provider()
    from host_brain.app.fastapi_app import get_ledger as _app_ledger
    return _app_ledger()

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _now_ms() -> int:
    return int(time.time() * 1000)

def _safe_json(text: Any) -> Any:
    if isinstance(text, str):
        try:
            return json.loads(text)
        except Exception:
            return text
    return text

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

# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------
class ServiceHealthItem(BaseModel):
    service_id: str
    service_type: str  # "edge" | "llm_node"
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

class SessionItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    ts_ms: int
    status: str
    decision: Optional[str] = None
    reason_code: Optional[str] = None
    node_count: Optional[int] = None
    total_latency_ms: Optional[int] = None

class SessionsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[SessionItem]
    timestamp: int

class TraceNode(BaseModel):
    node_id: str
    node_type: str  # "edge" | "llm_node" | "system"
    display_name: str
    provider: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    latency_ms: Optional[int] = None
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    ts_ms: Optional[int] = None
    error: Optional[str] = None

class TraceEdge(BaseModel):
    source: str
    target: str

class TraceResponse(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    status: str
    total_latency_ms: Optional[int] = None
    node_count: int
    nodes: List[TraceNode]
    edges: List[TraceEdge]
    ts_ms: int
    timestamp: int

class TimeseriesPoint(BaseModel):
    ts_ms: int
    requests: int
    rejections: int
    errors: int
    avg_latency_ms: Optional[float] = None

class MetricsTimeseriesResponse(BaseModel):
    range_ms: int
    interval_ms: int
    points: List[TimeseriesPoint]
    total_requests: int
    total_rejections: int
    rejection_rate: float
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
# GET /obs/services/health
# ---------------------------------------------------------------------------
@router.get("/services/health", response_model=ServicesHealthResponse)
async def get_services_health(
    window_minutes: int = Query(60, ge=1, le=1440, description="统计窗口（分钟）"),
    side: Optional[str] = Query(None, description="过滤端：host|edge"),
):
    """WARN-BE-001: 调用 ServiceHealthAggregator.aggregate() 而非内联聚合逻辑。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000

    # 使用 ledger 数据构建探测函数注册表（被动监控）
    probe_registry = _build_probe_registry(ledger, since_ms)
    aggregator = ServiceHealthAggregator(probe_registry=probe_registry)
    result = await aggregator.aggregate(side_filter=side)

    # 将 ServiceHealthAggregator 返回的通用结构映射到 ServicesHealthResponse
    services = []
    for svc in result.get("services", []):
        services.append(ServiceHealthItem(
            service_id=svc["service_id"],
            service_type=svc.get("side", "edge"),
            display_name=svc.get("name", svc["service_id"]),
            status=svc["status"],
            p95_ms=svc.get("latency_ms"),
            last_call_ts=svc.get("last_check_ts"),
            call_count=None,
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


def _build_probe_registry(ledger, since_ms: int) -> dict:
    """基于 ledger 历史数据构建被动探测函数注册表。"""
    import asyncio
    from host_brain.observability.service_health_aggregator import ServiceHealthResult

    # 预加载所需数据
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=_now_ms())
    except Exception:
        voice_rows = []
    try:
        llm_rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=_now_ms())
    except Exception:
        llm_rows = []

    registry = {}

    # 边缘服务探测
    for svc in _EDGE_SERVICES:
        svc_rows = [r for r in voice_rows if str(r.get("event_type", "")).upper().startswith(svc)]
        health = _compute_node_health(svc_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]

        async def _make_edge_probe(s=_status, p=_p95, l=_last, sid=svc.lower()):
            return ServiceHealthResult(sid, sid, "edge", s, latency_ms=p, last_check_ts=l)

        registry[svc.lower()] = _make_edge_probe

    # LLM 节点探测
    for node_id, meta in _LLM_NODE_REGISTRY.items():
        node_rows = [r for r in llm_rows if r.get("node_id") == node_id]
        health = _compute_node_health(node_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]

        async def _make_llm_probe(s=_status, p=_p95, l=_last, nid=node_id, m=meta):
            return ServiceHealthResult(nid, m["role"], "host", s, latency_ms=p, last_check_ts=l)

        registry[node_id] = _make_llm_probe

    return registry

# ---------------------------------------------------------------------------
# GET /obs/sessions
# ---------------------------------------------------------------------------
@router.get("/sessions", response_model=SessionsResponse)
async def get_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="状态过滤: ok/rejected/error"),
    window_minutes: int = Query(60, ge=1, le=10080),
    keyword: Optional[str] = Query(None),
):
    """WARN-BE-002: 调用 SessionQueryService.query() 而非内联查询逻辑。"""
    ledger = _get_ledger()
    svc = SessionQueryService(ledger)
    since_ms = _now_ms() - window_minutes * 60 * 1000
    result = svc.query(
        page=page,
        page_size=page_size,
        status=status,
        keyword=keyword,
        start_time=since_ms,
        end_time=_now_ms(),
    )

    items = [
        SessionItem(
            request_id=r["request_id"],
            session_id=r.get("session_id"),
            ts_ms=r.get("ts_ms", 0),
            status=r.get("status", "error").upper(),
            decision=r.get("decision"),
            reason_code=r.get("reason_code"),
            total_latency_ms=r.get("duration_ms"),
        )
        for r in result.get("items", [])
    ]

    return SessionsResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items,
        timestamp=_now_ms(),
    )

# ---------------------------------------------------------------------------
# GET /obs/trace/{request_id}
# ---------------------------------------------------------------------------
@router.get("/trace/{request_id}", response_model=TraceResponse)
async def get_trace(request_id: str):
    """获取单次会话的完整链路节点详情（含入参/出参/状态/延迟）。"""
    ledger = _get_ledger()

    # 1) 从 graph_run_trace 获取链路骨架
    try:
        conn = ledger._conn
        row = conn.execute(
            "SELECT * FROM graph_run_trace WHERE request_id = ? LIMIT 1",
            (request_id,)
        ).fetchone()
    except Exception:
        row = None

    if row is None:
        # 降级：从 governor_log 构造最小链路（使用方法调用而非属性访问，避免 AttributeError）
        try:
            all_gov = ledger.fetch_by_time_range("governor_log", start_ms=0, end_ms=_now_ms())
            gov_rows = [r for r in all_gov if r.get("request_id") == request_id]
        except Exception:
            gov_rows = []
        if not gov_rows:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "request_id": request_id},
            )

        gov = gov_rows[-1]
        nodes = [
            TraceNode(
                node_id="governor",
                node_type="system",
                display_name="Governor",
                status="OK" if gov.get("decision") == "APPROVE" else "FAIL",
                ts_ms=gov.get("ts_ms"),
            )
        ]
        return TraceResponse(
            request_id=request_id,
            status="OK" if gov.get("decision") == "APPROVE" else "FAIL",
            node_count=len(nodes),
            nodes=nodes,
            edges=[],
            ts_ms=gov.get("ts_ms", _now_ms()),
            timestamp=_now_ms(),
        )

    # 2) 解析完整链路
    row_dict = dict(row)
    nodes_raw = _safe_json(row_dict.get("nodes_json")) or []
    edges_raw = _safe_json(row_dict.get("edges_json")) or []

    nodes = []
    for n in nodes_raw:
        nodes.append(TraceNode(
            node_id=n.get("node_id", ""),
            node_type=n.get("node_type", "system"),
            display_name=n.get("display_name", n.get("node_id", "")),
            provider=n.get("provider"),
            model_id=n.get("model_id"),
            status=n.get("status", "OK"),
            latency_ms=n.get("latency_ms"),
            input_data=n.get("input_data"),
            output_data=n.get("output_data"),
            ts_ms=n.get("ts_ms"),
            error=n.get("error"),
        ))

    edges = [TraceEdge(source=e.get("source", ""), target=e.get("target", "")) for e in edges_raw]

    return TraceResponse(
        request_id=request_id,
        session_id=row_dict.get("session_id"),
        status=row_dict.get("status", "OK"),
        total_latency_ms=row_dict.get("total_latency_ms"),
        node_count=row_dict.get("node_count", len(nodes)),
        nodes=nodes,
        edges=edges,
        ts_ms=row_dict.get("ts_ms", _now_ms()),
        timestamp=_now_ms(),
    )

# ---------------------------------------------------------------------------
# GET /obs/metrics/timeseries
# ---------------------------------------------------------------------------
@router.get("/metrics/timeseries", response_model=MetricsTimeseriesResponse)
async def get_metrics_timeseries(
    range_minutes: int = Query(60, ge=5, le=10080, description="时间范围（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="聚合间隔（分钟）"),
):
    """WARN-BE-003: 调用 MetricsTimeseriesService.query() 而非内联聚合逻辑。"""
    ledger = _get_ledger()
    svc = MetricsTimeseriesService(ledger)
    now = _now_ms()
    start_time = now - range_minutes * 60 * 1000
    interval_map = {1: "1m", 5: "5m", 60: "1h"}
    interval = interval_map.get(interval_minutes, "5m")

    result = svc.query(
        start_time=start_time,
        end_time=now,
        interval=interval,
    )

    # 将 MetricsTimeseriesService 返回的通用结构映射到 MetricsTimeseriesResponse
    timeseries = result.get("timeseries", {})
    timestamps = timeseries.get("timestamps", [])
    totals = timeseries.get("total", [])
    rej_rates = timeseries.get("rejection_rate", [])
    interval_ms = interval_minutes * 60 * 1000

    points = []
    for i, ts_label in enumerate(timestamps):
        req = totals[i] if i < len(totals) else 0
        rej_rate_val = rej_rates[i] if i < len(rej_rates) else 0.0
        rej = int(req * rej_rate_val / 100) if req > 0 else 0
        points.append(TimeseriesPoint(
            ts_ms=start_time + i * interval_ms,
            requests=req,
            rejections=rej,
            errors=0,
            avg_latency_ms=None,
        ))

    summary = result.get("summary", {})
    total_req = summary.get("total_requests", 0)
    total_rej = summary.get("total_rejections", 0)
    rej_rate = summary.get("rejection_rate", 0.0)

    return MetricsTimeseriesResponse(
        range_ms=range_minutes * 60 * 1000,
        interval_ms=interval_ms,
        points=points,
        total_requests=total_req,
        total_rejections=total_rej,
        rejection_rate=rej_rate,
        timestamp=now,
    )

# ---------------------------------------------------------------------------
# GET /obs/llm/nodes
# ---------------------------------------------------------------------------
@router.get("/llm/nodes", response_model=LlmNodesResponse)
async def get_llm_nodes(
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

