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

logger = logging.getLogger("Nastume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])

# ---------------------------------------------------------------------------
# 9 个 LLM 云节点静态注册表（来自 node_registry.yaml）
# ---------------------------------------------------------------------------
_LLM_NODE_REGISTRY: Dict[str, Dict[str, str]] = {
    "core_reasoner_primary":    {"role": "主推理",      "provider": "openai",   "model_id": "gpt-5.2"},
    "core_reasoner_secondary":  {"role": "备用推理",    "provider": "openai",   "model_id": "gpt-5-mini"},
    "planner_constraints":      {"role": "规划约束",    "provider": "openai",   "model_id": "gpt-5.2-pro"},
    "tool_planner":             {"role": "工具规划",    "provider": "deepseek", "model_id": "deepseek-chat"},
    "safety_verifier":          {"role": "安全验证",    "provider": "openai",   "model_id": "gpt-4.1-mini"},
    "consistency_verifier":     {"role": "一致性验证",  "provider": "gemini",   "model_id": "gemini-2.5-flash-lite"},
    "renderer_ja_c_style":      {"role": "日文渲染",    "provider": "gemini",   "model_id": "gemini-2.5-flash"},
    "workbench_translate":      {"role": "翻译工作台",  "provider": "gemini",   "model_id": "gemini-2.5-pro"},
    "workbench_code_assist":    {"role": "代码辅助",    "provider": "deepseek", "model_id": "deepseek-chat"},
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
):
    """全服务健康聚合：边缘服务 + 9 个 LLM 云节点。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000
    services: List[ServiceHealthItem] = []

    # 1) 边缘服务：从 voice_event_log 聚合
    try:
        rows = ledger.fetch_by_time_range("voice_event_log", since_ms, _now_ms())
    except Exception:
        rows = []

    for svc in _EDGE_SERVICES:
        svc_rows = [r for r in rows if str(r.get("event_type", "")).upper().startswith(svc)]
        health = _compute_node_health(svc_rows)
        services.append(ServiceHealthItem(
            service_id=svc.lower(),
            service_type="edge",
            display_name=svc,
            status=health["status"],
            success_rate=health["success_rate"],
            p95_ms=health["p95_ms"],
            last_call_ts=health["last_call_ts"],
            call_count=health.get("call_count", 0),
        ))

    # 2) LLM 云节点：从 llm_node_call_log 聚合
    try:
        llm_rows = ledger.fetch_by_time_range("llm_node_call_log", since_ms, _now_ms())
    except Exception:
        llm_rows = []

    for node_id, meta in _LLM_NODE_REGISTRY.items():
        node_rows = [r for r in llm_rows if r.get("node_id") == node_id]
        health = _compute_node_health(node_rows)
        services.append(ServiceHealthItem(
            service_id=node_id,
            service_type="llm_node",
            display_name=node_id.replace("_", " ").title(),
            role=meta["role"],
            provider=meta["provider"],
            model_id=meta["model_id"],
            status=health["status"],
            success_rate=health["success_rate"],
            p95_ms=health["p95_ms"],
            last_call_ts=health["last_call_ts"],
            call_count=health.get("call_count", 0),
        ))

    counts = {"UP": 0, "DEGRADED": 0, "DOWN": 0, "UNKNOWN": 0}
    for s in services:
        counts[s.status] = counts.get(s.status, 0) + 1

    return ServicesHealthResponse(
        total=len(services),
        up=counts["UP"],
        degraded=counts["DEGRADED"],
        down=counts["DOWN"],
        unknown=counts["UNKNOWN"],
        services=services,
        timestamp=_now_ms(),
    )

# ---------------------------------------------------------------------------
# GET /obs/sessions
# ---------------------------------------------------------------------------
@router.get("/sessions", response_model=SessionsResponse)
async def get_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="状态过滤: OK/FAIL"),
    window_minutes: int = Query(60, ge=1, le=10080),
):
    """会话列表，按时间倒序，支持分页和状态过滤。"""
    ledger = _get_ledger()
    since_ms = _now_ms() - window_minutes * 60 * 1000

    try:
        # 从 governor_log 获取会话列表（每个 request_id 一条）
        rows = ledger.fetch_by_time_range("governor_log", since_ms, _now_ms())
    except Exception:
        rows = []

    # 从 graph_run_trace 获取链路信息
    try:
        trace_rows = ledger.fetch_by_time_range("graph_run_trace", since_ms, _now_ms())
        trace_map = {r["request_id"]: r for r in trace_rows}
    except Exception:
        trace_map = {}

    # 去重（同一 request_id 取最新）
    seen: Dict[str, Dict] = {}
    for r in rows:
        rid = r.get("request_id", "")
        if rid not in seen or r.get("ts_ms", 0) > seen[rid].get("ts_ms", 0):
            seen[rid] = r

    items_raw = sorted(seen.values(), key=lambda x: x.get("ts_ms", 0), reverse=True)

    # 状态过滤
    if status:
        filter_status = status.upper()
        if filter_status == "OK":
            items_raw = [r for r in items_raw if r.get("decision") == "APPROVE"]
        elif filter_status == "FAIL":
            items_raw = [r for r in items_raw if r.get("decision") != "APPROVE"]

    total = len(items_raw)
    offset = (page - 1) * page_size
    page_items = items_raw[offset: offset + page_size]

    items = []
    for r in page_items:
        rid = r.get("request_id", "")
        trace = trace_map.get(rid, {})
        decision = r.get("decision", "")
        items.append(SessionItem(
            request_id=rid,
            session_id=r.get("session_id") or trace.get("session_id"),
            ts_ms=r.get("ts_ms", 0),
            status="OK" if decision == "APPROVE" else "FAIL",
            decision=decision,
            reason_code=r.get("reason_code"),
            node_count=trace.get("node_count"),
            total_latency_ms=trace.get("total_latency_ms"),
        ))

    return SessionsResponse(
        total=total,
        page=page,
        page_size=page_size,
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
        # 降级：从 governor_log 构造最小链路
        try:
            gov_rows = [r for r in ledger.governor_log if r.get("request_id") == request_id]
        except Exception:
            gov_rows = []
        if not gov_rows:
            raise HTTPException(status_code=404, detail=f"Trace not found: {request_id}")

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
    """多维度时序数据：请求量/拒绝/错误/平均延迟。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - range_minutes * 60 * 1000
    interval_ms = interval_minutes * 60 * 1000

    try:
        gov_rows = ledger.fetch_by_time_range("governor_log", since_ms, now)
    except Exception:
        gov_rows = []

    try:
        err_rows = ledger.fetch_by_time_range("error_log", since_ms, now)
    except Exception:
        err_rows = []

    try:
        trace_rows = ledger.fetch_by_time_range("graph_run_trace", since_ms, now)
    except Exception:
        trace_rows = []

    # 构建时间桶
    num_buckets = max(1, range_minutes // interval_minutes)
    buckets: List[Dict[str, Any]] = []
    for i in range(num_buckets):
        bucket_start = since_ms + i * interval_ms
        buckets.append({
            "ts_ms": bucket_start,
            "requests": 0,
            "rejections": 0,
            "errors": 0,
            "latencies": [],
        })

    def _bucket_idx(ts: int) -> int:
        idx = int((ts - since_ms) / interval_ms)
        return max(0, min(idx, num_buckets - 1))

    for r in gov_rows:
        ts = r.get("ts_ms", 0)
        if ts < since_ms:
            continue
        idx = _bucket_idx(ts)
        buckets[idx]["requests"] += 1
        if r.get("decision") != "APPROVE":
            buckets[idx]["rejections"] += 1

    for r in err_rows:
        ts = r.get("ts_ms", 0)
        if ts < since_ms:
            continue
        idx = _bucket_idx(ts)
        buckets[idx]["errors"] += 1

    for r in trace_rows:
        ts = r.get("ts_ms", 0)
        lat = r.get("total_latency_ms")
        if ts < since_ms or lat is None:
            continue
        idx = _bucket_idx(ts)
        buckets[idx]["latencies"].append(lat)

    points = []
    for b in buckets:
        lats = b["latencies"]
        avg_lat = round(sum(lats) / len(lats), 1) if lats else None
        points.append(TimeseriesPoint(
            ts_ms=b["ts_ms"],
            requests=b["requests"],
            rejections=b["rejections"],
            errors=b["errors"],
            avg_latency_ms=avg_lat,
        ))

    total_req = sum(b["requests"] for b in buckets)
    total_rej = sum(b["rejections"] for b in buckets)
    rej_rate = round(total_rej / total_req * 100, 2) if total_req > 0 else 0.0

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
        rows = ledger.fetch_by_time_range("llm_node_call_log", since_ms, _now_ms())
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
