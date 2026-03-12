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
    probe_registry, extra_data = _build_probe_registry(ledger, since_ms)
    aggregator = ServiceHealthAggregator(probe_registry=probe_registry)
    result = await aggregator.aggregate(side_filter=side)

    # 将 ServiceHealthAggregator 返回的通用结构映射到 ServicesHealthResponse
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


def _build_probe_registry(ledger, since_ms: int):
    """基于 ledger 历史数据构建被动探测函数注册表，同时返回 extra_data 字典（含 success_rate/call_count 等）。"""
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
    extra_data = {}  # service_id -> {success_rate, call_count, role, provider, model_id}

    # 边缘服务探测
    for svc in _EDGE_SERVICES:
        svc_rows = [r for r in voice_rows if str(r.get("event_type", "")).upper().startswith(svc)]
        health = _compute_node_health(svc_rows)
        _status = health["status"]
        _p95 = health["p95_ms"]
        _last = health["last_call_ts"]
        sid = svc.lower()

        async def _make_edge_probe(s=_status, p=_p95, l=_last, _sid=sid):
            return ServiceHealthResult(_sid, _sid.upper(), "edge", s, latency_ms=p, last_check_ts=l)

        registry[sid] = _make_edge_probe
        extra_data[sid] = {
            "success_rate": health["success_rate"],
            "call_count":   health.get("call_count", 0),
        }

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
        extra_data[node_id] = {
            "role":         meta["role"],
            "provider":     meta["provider"],
            "model_id":     meta["model_id"],
            "success_rate": health["success_rate"],
            "call_count":   health.get("call_count", 0),
        }

    return registry, extra_data

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
    # 修复：使用公共方法 fetch_by_request_id 替代直接访问私有属性 _conn，
    # 同时利用 _decode_row 自动反序列化 nodes_json/edges_json 等 JSON 字段
    try:
        trace_rows = ledger.fetch_by_request_id("graph_run_trace", request_id)
    except Exception:
        trace_rows = []

    if not trace_rows:
        # 降级：从 governor_log 构造最小链路
        try:
            gov_rows = ledger.fetch_by_request_id("governor_log", request_id)
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

    # 2) 解析完整链路（fetch_by_request_id 已通过 _decode_row 自动反序列化 JSON 字段）
    row_dict = trace_rows[0]
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


# ---------------------------------------------------------------------------
# P1: GET /obs/governor/stats  — Governor 决策漏斗 + reason_code 分布
# ---------------------------------------------------------------------------
class GovernorReasonItem(BaseModel):
    reason_code: str
    count: int
    pct: float

class GovernorStatsResponse(BaseModel):
    window_minutes: int
    total: int
    approved: int
    rejected: int
    rejection_rate: float
    reason_codes: List[GovernorReasonItem]
    timeseries_labels: List[str]
    timeseries_approved: List[int]
    timeseries_rejected: List[int]
    timestamp: int

@router.get("/governor/stats", response_model=GovernorStatsResponse)
async def get_governor_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """P1: Governor 决策漏斗 — 批准/拒绝数量、拒绝率、reason_code 分布、时序趋势。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("governor_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)
    approved = sum(1 for r in rows if str(r.get("decision", "")).upper() == "APPROVE")
    rejected = total - approved
    rejection_rate = round(rejected / total * 100, 1) if total > 0 else 0.0

    # reason_code 分布（仅拒绝行）
    from collections import Counter
    rej_rows = [r for r in rows if str(r.get("decision", "")).upper() != "APPROVE"]
    rc_counter = Counter(r.get("reason_code") or "UNKNOWN" for r in rej_rows)
    reason_codes = [
        GovernorReasonItem(
            reason_code=code,
            count=cnt,
            pct=round(cnt / rejected * 100, 1) if rejected > 0 else 0.0,
        )
        for code, cnt in rc_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_approved = [0] * bucket_count
    ts_rejected = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            if str(r.get("decision", "")).upper() == "APPROVE":
                ts_approved[idx] += 1
            else:
                ts_rejected[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return GovernorStatsResponse(
        window_minutes=window_minutes,
        total=total,
        approved=approved,
        rejected=rejected,
        rejection_rate=rejection_rate,
        reason_codes=reason_codes,
        timeseries_labels=labels,
        timeseries_approved=ts_approved,
        timeseries_rejected=ts_rejected,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P1: GET /obs/errors  — 全局错误日志统计
# ---------------------------------------------------------------------------
class ErrorStageItem(BaseModel):
    stage: str
    count: int
    pct: float

class ErrorCodeItem(BaseModel):
    reason_code: str
    count: int

class ErrorsResponse(BaseModel):
    window_minutes: int
    total_errors: int
    stages: List[ErrorStageItem]
    top_codes: List[ErrorCodeItem]
    timeseries_labels: List[str]
    timeseries_counts: List[int]
    recent: List[Dict[str, Any]]
    timestamp: int

@router.get("/errors", response_model=ErrorsResponse)
async def get_errors(
    window_minutes: int = Query(60, ge=1, le=10080),
    interval_minutes: int = Query(5, ge=1, le=60),
    limit: int = Query(20, ge=1, le=100, description="最近错误条数"),
):
    """P1: 全局错误日志统计 — 错误总量、stage 分布、reason_code Top10、时序趋势、最近错误列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    total = len(rows)

    from collections import Counter
    stage_counter = Counter(r.get("stage") or "UNKNOWN" for r in rows)
    stages = [
        ErrorStageItem(
            stage=s,
            count=c,
            pct=round(c / total * 100, 1) if total > 0 else 0.0,
        )
        for s, c in stage_counter.most_common(10)
    ]

    code_counter = Counter(r.get("reason_code") or "UNKNOWN" for r in rows)
    top_codes = [
        ErrorCodeItem(reason_code=code, count=cnt)
        for code, cnt in code_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_counts = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_counts[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    # 最近 N 条错误（倒序）
    recent_rows = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent = [
        {
            "request_id": r.get("request_id"),
            "stage": r.get("stage"),
            "reason_code": r.get("reason_code"),
            "detail": (r.get("detail") or "")[:200],
            "ts_ms": r.get("ts_ms"),
        }
        for r in recent_rows
    ]

    return ErrorsResponse(
        window_minutes=window_minutes,
        total_errors=total,
        stages=stages,
        top_codes=top_codes,
        timeseries_labels=labels,
        timeseries_counts=ts_counts,
        recent=recent,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P2/P3: GET /obs/subsystems/stats  — 子系统健康统计（Memory/Retrieval/Skill/Agent Loop/Airlock/Bond）
# ---------------------------------------------------------------------------
class SubsystemStatItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]

class SubsystemsStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[SubsystemStatItem]
    timestamp: int

def _compute_subsystem_health(rows: List[Dict], status_field: str = "status", latency_field: Optional[str] = None) -> Dict[str, Any]:
    """通用子系统健康计算：成功率、P95延迟、最后活跃时间。"""
    total = len(rows)
    if total == 0:
        return {"total": 0, "success": 0, "failed": 0, "success_rate": None, "p95_ms": None, "last_ts": None}

    success = sum(1 for r in rows if str(r.get(status_field, "")).upper() in ("OK", "SUCCESS", "DONE", "APPROVED"))
    failed = total - success
    success_rate = round(success / total * 100, 1)

    p95_ms = None
    if latency_field:
        latencies = [r[latency_field] for r in rows if r.get(latency_field) is not None]
        if latencies:
            latencies.sort()
            idx = max(0, int(len(latencies) * 0.95) - 1)
            p95_ms = latencies[idx]

    last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
    return {"total": total, "success": success, "failed": failed, "success_rate": success_rate, "p95_ms": p95_ms, "last_ts": last_ts}

@router.get("/subsystems/stats", response_model=SubsystemsStatsResponse)
async def get_subsystems_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
):
    """P2/P3: 子系统健康统计 — Memory/Retrieval/Skill/Agent Loop/Airlock/Bond/Vision/Sandbox/Channel。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 定义各子系统：(subsystem_id, display_name, table, status_field, latency_field)
    SUBSYSTEM_DEFS = [
        ("memory",    "Memory 操作",    "memory_ops_log",     "status",        None),
        ("retrieval", "向量检索",        "retrieval_event_log","status",        None),
        ("skill",     "Skill 执行",     "skill_exec_log",     "result_status", None),
        ("agent_loop","Agent Loop",     "agent_loop_log",     "status",        "latency_ms"),
        ("airlock",   "Airlock 过滤",   "airlock_log",        "blocked",       None),
        ("bond",      "Bond 关系更新",  "bond_update_log",    "status",        None),
        ("vision",    "Vision 视觉",    "vision_event_log",   "status",        None),
        ("sandbox",   "Sandbox 执行",   "sandbox_exec_log",   "status",        "duration_ms"),
        ("channel",   "Channel I/O",   "channel_io_log",     "status",        None),
        ("intent",    "Intent 解析",    "intent_log",         "lint_result",   None),
        ("qa",        "QA 违规检查",    "qa_violation_log",   None,            None),
    ]

    subsystems = []
    for sid, dname, table, status_field, latency_field in SUBSYSTEM_DEFS:
        try:
            rows = ledger.fetch_by_time_range(table, start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

        if sid == "airlock":
            # airlock: blocked=0 表示通过，blocked=1 表示拦截
            total = len(rows)
            blocked = sum(1 for r in rows if r.get("blocked") in (1, True, "1"))
            success = total - blocked
            failed = blocked
            success_rate = round(success / total * 100, 1) if total > 0 else None
            last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
            h = {"total": total, "success": success, "failed": failed, "success_rate": success_rate, "p95_ms": None, "last_ts": last_ts}
        elif sid == "qa":
            # qa_violation_log: 每行都是一次违规，total=违规次数，success=0
            total = len(rows)
            h = {"total": total, "success": 0, "failed": total, "success_rate": 0.0 if total > 0 else None, "p95_ms": None, "last_ts": max((r.get("ts_ms") or 0 for r in rows), default=None)}
        elif sid == "intent":
            # intent_log: lint_result 为空或 "OK" 表示通过
            total = len(rows)
            success = sum(1 for r in rows if not r.get("lint_result") or str(r.get("lint_result", "")).upper() in ("OK", "PASS", ""))
            failed = total - success
            success_rate = round(success / total * 100, 1) if total > 0 else None
            last_ts = max((r.get("ts_ms") or 0 for r in rows), default=None)
            h = {"total": total, "success": success, "failed": failed, "success_rate": success_rate, "p95_ms": None, "last_ts": last_ts}
        else:
            h = _compute_subsystem_health(rows, status_field=status_field or "status", latency_field=latency_field)

        subsystems.append(SubsystemStatItem(
            subsystem=sid,
            display_name=dname,
            total=h["total"],
            success=h["success"],
            failed=h["failed"],
            success_rate=h["success_rate"],
            p95_ms=h.get("p95_ms"),
            last_ts=h.get("last_ts"),
        ))

    return SubsystemsStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# P3: GET /obs/agent_loop/stats  — Agent Loop 详细统计
# ---------------------------------------------------------------------------
class AgentLoopRoundItem(BaseModel):
    request_id: str
    session_id: Optional[str]
    goal: str
    total_rounds: int
    goal_achieved: bool
    total_actions: int
    success_actions: int
    failed_actions: int
    total_latency_ms: Optional[int]
    ts_ms: int

class AgentLoopStatsResponse(BaseModel):
    window_minutes: int
    total_loops: int
    achieved: int
    failed: int
    achievement_rate: float
    avg_rounds: Optional[float]
    recent_loops: List[AgentLoopRoundItem]
    timestamp: int

@router.get("/agent_loop/stats", response_model=AgentLoopStatsResponse)
async def get_agent_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(10, ge=1, le=50),
):
    """P3: Agent Loop 详细统计 — 目标达成率、平均轮次、最近执行列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("agent_loop_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 按 request_id 聚合（取 LOOP_END 或最后一行）
    from collections import defaultdict
    loop_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        loop_map[r.get("request_id", "")].append(r)

    loops = []
    for req_id, loop_rows in loop_map.items():
        # 找 LOOP_END 行
        end_rows = [r for r in loop_rows if str(r.get("phase", "")).upper() in ("LOOP_END", "LOOP_SUCCESS", "LOOP_FAILED")]
        summary_row = end_rows[-1] if end_rows else loop_rows[-1]

        total_rounds = max((r.get("round") or 0 for r in loop_rows), default=0)
        goal_achieved = bool(summary_row.get("goal_achieved"))
        total_actions = sum(r.get("action_count") or 0 for r in loop_rows)
        success_actions = sum(r.get("success_count") or 0 for r in loop_rows)
        failed_actions = sum(r.get("failed_count") or 0 for r in loop_rows)
        total_latency = sum(r.get("latency_ms") or 0 for r in loop_rows if r.get("latency_ms"))

        loops.append({
            "request_id": req_id,
            "session_id": summary_row.get("session_id"),
            "goal": (summary_row.get("goal") or "")[:80],
            "total_rounds": total_rounds,
            "goal_achieved": goal_achieved,
            "total_actions": total_actions,
            "success_actions": success_actions,
            "failed_actions": failed_actions,
            "total_latency_ms": total_latency or None,
            "ts_ms": summary_row.get("ts_ms") or 0,
        })

    total_loops = len(loops)
    achieved = sum(1 for l in loops if l["goal_achieved"])
    failed_loops = total_loops - achieved
    achievement_rate = round(achieved / total_loops * 100, 1) if total_loops > 0 else 0.0
    avg_rounds = round(sum(l["total_rounds"] for l in loops) / total_loops, 1) if total_loops > 0 else None

    # 最近 N 条（按 ts_ms 倒序）
    recent = sorted(loops, key=lambda l: l["ts_ms"], reverse=True)[:limit]

    return AgentLoopStatsResponse(
        window_minutes=window_minutes,
        total_loops=total_loops,
        achieved=achieved,
        failed=failed_loops,
        achievement_rate=achievement_rate,
        avg_rounds=avg_rounds,
        recent_loops=[AgentLoopRoundItem(**l) for l in recent],
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-26: GET /obs/memory/stats  — Memory 子系统详细统计
# ---------------------------------------------------------------------------
class MemorySubsystemItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]
    extra: Optional[Dict[str, Any]] = None

class MemoryStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[MemorySubsystemItem]
    # 聚合指标
    total_writes: int
    guard_reject_rate: Optional[float]
    compress_rate: Optional[float]
    distill_count: int
    timestamp: int

@router.get("/memory/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-26: Memory 子系统详细统计。

    覆盖四个 Memory 子系统：
    - EpisodicStore：写入量、成功率
    - MemoryGuard：拒绝率（NARRATIVE_ONLY 过滤 + 宪法校验拒绝）
    - ContextCompressor：压缩率（压缩前/后 token 数比值）
    - LongTermDistiller：蒸馏次数、蒸馏成功率
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── EpisodicStore 写入统计 ────────────────────────────────────────────
    try:
        episodic_rows = ledger.fetch_by_time_range("episodic_store_log", start_ms=since_ms, end_ms=now)
    except Exception:
        episodic_rows = []

    ep_total = len(episodic_rows)
    ep_success = sum(1 for r in episodic_rows if str(r.get("status", "")).upper() in ("OK", "WRITTEN", "SUCCESS"))
    ep_failed = ep_total - ep_success
    ep_rate = round(ep_success / ep_total * 100, 1) if ep_total > 0 else None
    ep_last = max((r.get("ts_ms") or 0 for r in episodic_rows), default=None)

    # ── MemoryGuard 拒绝统计 ──────────────────────────────────────────────
    try:
        guard_rows = ledger.fetch_by_time_range("memory_guard_log", start_ms=since_ms, end_ms=now)
    except Exception:
        guard_rows = []

    # 从 error_log 中补充 MemoryGuard 相关记录（MEMORY_GUARD_FILTERED 标签）
    try:
        error_rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        guard_error_rows = [
            r for r in error_rows
            if "MEMORY_GUARD" in str(r.get("stage", "")).upper()
            or "MEMORY_GUARD" in str(r.get("reason_code", "")).upper()
        ]
    except Exception:
        guard_error_rows = []

    all_guard_rows = guard_rows + guard_error_rows
    guard_total = len(all_guard_rows)
    guard_rejected = sum(
        1 for r in all_guard_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("reason_code", "")).upper().startswith("EPISODIC_")
        or str(r.get("reason_code", "")).upper().startswith("CANON_")
    )
    guard_reject_rate = round(guard_rejected / guard_total * 100, 1) if guard_total > 0 else None
    guard_last = max((r.get("ts_ms") or 0 for r in all_guard_rows), default=None)

    # ── ContextCompressor 压缩统计 ────────────────────────────────────────
    try:
        compress_rows = ledger.fetch_by_time_range("context_compress_log", start_ms=since_ms, end_ms=now)
    except Exception:
        compress_rows = []

    comp_total = len(compress_rows)
    comp_success = sum(1 for r in compress_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    comp_failed = comp_total - comp_success
    comp_rate = None
    if compress_rows:
        # 计算平均压缩率：(1 - output_tokens / input_tokens) * 100
        ratios = []
        for r in compress_rows:
            inp = r.get("input_tokens") or r.get("input_chars")
            out = r.get("output_tokens") or r.get("output_chars")
            if inp and out and inp > 0:
                ratios.append(1.0 - out / inp)
        if ratios:
            comp_rate = round(sum(ratios) / len(ratios) * 100, 1)
    comp_last = max((r.get("ts_ms") or 0 for r in compress_rows), default=None)

    # ── LongTermDistiller 蒸馏统计 ────────────────────────────────────────
    try:
        distill_rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
    except Exception:
        distill_rows = []

    distill_total = len(distill_rows)
    distill_success = sum(1 for r in distill_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    distill_failed = distill_total - distill_success
    distill_rate = round(distill_success / distill_total * 100, 1) if distill_total > 0 else None
    distill_last = max((r.get("ts_ms") or 0 for r in distill_rows), default=None)

    subsystems = [
        MemorySubsystemItem(
            subsystem="episodic_store",
            display_name="EpisodicStore 写入",
            total=ep_total,
            success=ep_success,
            failed=ep_failed,
            success_rate=ep_rate,
            p95_ms=None,
            last_ts=ep_last,
            extra={"write_count": ep_success},
        ),
        MemorySubsystemItem(
            subsystem="memory_guard",
            display_name="MemoryGuard 宪法守卫",
            total=guard_total,
            success=guard_total - guard_rejected,
            failed=guard_rejected,
            success_rate=(100.0 - guard_reject_rate) if guard_reject_rate is not None else None,
            p95_ms=None,
            last_ts=guard_last,
            extra={"reject_rate": guard_reject_rate, "rejected_count": guard_rejected},
        ),
        MemorySubsystemItem(
            subsystem="context_compressor",
            display_name="ContextCompressor 压缩",
            total=comp_total,
            success=comp_success,
            failed=comp_failed,
            success_rate=round(comp_success / comp_total * 100, 1) if comp_total > 0 else None,
            p95_ms=None,
            last_ts=comp_last,
            extra={"avg_compress_rate_pct": comp_rate},
        ),
        MemorySubsystemItem(
            subsystem="long_term_distiller",
            display_name="LongTermDistiller 蒸馏",
            total=distill_total,
            success=distill_success,
            failed=distill_failed,
            success_rate=distill_rate,
            p95_ms=None,
            last_ts=distill_last,
            extra={"distill_count": distill_success},
        ),
    ]

    return MemoryStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        total_writes=ep_success,
        guard_reject_rate=guard_reject_rate,
        compress_rate=comp_rate,
        distill_count=distill_success,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-30: GET /obs/vts/stats  — VTS 通道统计
# ---------------------------------------------------------------------------
class VtsEmotionItem(BaseModel):
    emotion_tag: str
    count: int
    pct: float

class VtsStatsResponse(BaseModel):
    window_minutes: int
    total_events: int
    applied: int
    degraded: int
    rejected: int
    apply_rate: Optional[float]
    degrade_rate: Optional[float]
    reject_rate: Optional[float]
    top_emotions: List[VtsEmotionItem]
    talk_gate_total: int
    talk_gate_allowed: int
    talk_gate_blocked: int
    timestamp: int

@router.get("/vts/stats", response_model=VtsStatsResponse)
async def get_vts_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """TODO-30: VTS 通道统计 — 表情应用率/降级率/拒绝率、Top 情感标签、Talk Gate 事件统计。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── VTS 表情事件统计 ──────────────────────────────────────────────────
    try:
        vts_rows = ledger.fetch_by_time_range("vts_event_log", start_ms=since_ms, end_ms=now)
    except Exception:
        vts_rows = []

    # 补充从 voice_event_log 中过滤 VTS 相关事件
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
        vts_voice_rows = [
            r for r in voice_rows
            if str(r.get("event_type", "")).upper().startswith("VTS")
        ]
    except Exception:
        vts_voice_rows = []

    all_vts_rows = vts_rows + vts_voice_rows
    total_events = len(all_vts_rows)

    applied = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("APPLIED", "OK", "SUCCESS")
        or str(r.get("result", "")).upper() == "APPLIED"
    )
    degraded = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("DEGRADED", "FALLBACK")
        or str(r.get("result", "")).upper() == "DEGRADED"
    )
    rejected = sum(
        1 for r in all_vts_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("result", "")).upper() == "REJECTED"
    )

    apply_rate = round(applied / total_events * 100, 1) if total_events > 0 else None
    degrade_rate = round(degraded / total_events * 100, 1) if total_events > 0 else None
    reject_rate = round(rejected / total_events * 100, 1) if total_events > 0 else None

    # Top 情感标签分布
    from collections import Counter
    emotion_counter = Counter(
        str(r.get("emotion_tag") or r.get("expression") or "UNKNOWN").upper()
        for r in all_vts_rows
    )
    top_emotions = [
        VtsEmotionItem(
            emotion_tag=tag,
            count=cnt,
            pct=round(cnt / total_events * 100, 1) if total_events > 0 else 0.0,
        )
        for tag, cnt in emotion_counter.most_common(8)
    ]

    # ── Talk Gate 事件统计 ────────────────────────────────────────────────
    try:
        talk_gate_rows = ledger.fetch_by_time_range("talk_gate_log", start_ms=since_ms, end_ms=now)
    except Exception:
        talk_gate_rows = []

    talk_gate_total = len(talk_gate_rows)
    talk_gate_allowed = sum(
        1 for r in talk_gate_rows
        if str(r.get("decision", "")).upper() in ("ALLOW", "ALLOWED", "APPROVE", "OK")
    )
    talk_gate_blocked = talk_gate_total - talk_gate_allowed

    return VtsStatsResponse(
        window_minutes=window_minutes,
        total_events=total_events,
        applied=applied,
        degraded=degraded,
        rejected=rejected,
        apply_rate=apply_rate,
        degrade_rate=degrade_rate,
        reject_rate=reject_rate,
        top_emotions=top_emotions,
        talk_gate_total=talk_gate_total,
        talk_gate_allowed=talk_gate_allowed,
        talk_gate_blocked=talk_gate_blocked,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-31: GET /obs/distillation/stats  — LongTermDistiller 蒸馏统计
# ---------------------------------------------------------------------------
class DistillationCycleItem(BaseModel):
    cycle_id: str
    topics_processed: int
    semantic_memories_created: int
    proposals_submitted: int
    proposals_skipped: int
    error_count: int
    ts_ms: int
    detail: Optional[str] = None

class DistillationStatsResponse(BaseModel):
    window_minutes: int
    total_cycles: int
    success_cycles: int
    failed_cycles: int
    success_rate: Optional[float]
    total_proposals_submitted: int
    total_semantic_created: int
    recent_cycles: List[DistillationCycleItem]
    timestamp: int

@router.get("/distillation/stats", response_model=DistillationStatsResponse)
async def get_distillation_stats(
    window_minutes: int = Query(1440, ge=1, le=43200, description="统计窗口（分钟，默认 24h）"),
    limit: int = Query(10, ge=1, le=50, description="最近循环条数"),
):
    """TODO-31: LongTermDistiller 蒸馏统计 — 循环次数、提案提交量、蒸馏成功率。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("distillation_cycle_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 补充从 distill_log 查询（兼容旧表名）
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total_cycles = len(rows)
    success_cycles = sum(
        1 for r in rows
        if (r.get("errors") in (None, [], "") or len(r.get("errors") or []) == 0)
        and int(r.get("proposals_submitted") or 0) >= 0
    )
    failed_cycles = total_cycles - success_cycles
    success_rate = round(success_cycles / total_cycles * 100, 1) if total_cycles > 0 else None

    total_proposals = sum(int(r.get("proposals_submitted") or 0) for r in rows)
    total_semantic = sum(int(r.get("semantic_memories_created") or 0) for r in rows)

    # 最近 N 条（倒序）
    recent_rows = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_cycles = [
        DistillationCycleItem(
            cycle_id=str(r.get("cycle_id") or r.get("request_id") or ""),
            topics_processed=int(r.get("topics_processed") or 0),
            semantic_memories_created=int(r.get("semantic_memories_created") or 0),
            proposals_submitted=int(r.get("proposals_submitted") or 0),
            proposals_skipped=int(r.get("proposals_skipped") or 0),
            error_count=len(r.get("errors") or []),
            ts_ms=int(r.get("ts_ms") or 0),
            detail=(r.get("detail") or "")[:200],
        )
        for r in recent_rows
    ]

    return DistillationStatsResponse(
        window_minutes=window_minutes,
        total_cycles=total_cycles,
        success_cycles=success_cycles,
        failed_cycles=failed_cycles,
        success_rate=success_rate,
        total_proposals_submitted=total_proposals,
        total_semantic_created=total_semantic,
        recent_cycles=recent_cycles,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-37: GET /obs/wechat/stats  — WeChat 通道统计
# ---------------------------------------------------------------------------
class WeChatRateLimitItem(BaseModel):
    wxid: str
    message_count: int
    last_ts: Optional[int]
    rate_limited: bool

class WeChatEventItem(BaseModel):
    request_id: Optional[str]
    wxid: Optional[str]
    direction: Optional[str]  # "inbound" | "outbound"
    status: str
    reason_code: Optional[str]
    ts_ms: int

class WeChatStatsResponse(BaseModel):
    window_minutes: int
    channel_status: str  # "UP" | "DEGRADED" | "DOWN" | "UNKNOWN"
    total_events: int
    inbound: int
    outbound: int
    success: int
    failed: int
    success_rate: Optional[float]
    rate_limited_count: int
    top_wxids: List[WeChatRateLimitItem]
    recent_events: List[WeChatEventItem]
    last_event_ts: Optional[int]
    timestamp: int

@router.get("/wechat/stats", response_model=WeChatStatsResponse)
async def get_wechat_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    limit: int = Query(20, ge=1, le=100, description="最近事件条数"),
):
    """TODO-37: WeChat 通道统计 — 通道健康状态、频率限制统计、最近 channel_io_log 事件。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── channel_io_log 中过滤 WeChat 相关事件 ────────────────────────────
    try:
        channel_rows = ledger.fetch_by_time_range("channel_io_log", start_ms=since_ms, end_ms=now)
        wechat_rows = [
            r for r in channel_rows
            if str(r.get("channel") or r.get("source") or "").upper() in ("WECHAT", "WX", "WEIXIN")
            or str(r.get("event_type") or "").upper().startswith("WECHAT")
        ]
    except Exception:
        wechat_rows = []

    # 补充从 voice_event_log 中过滤 WECHAT 相关事件
    try:
        voice_rows = ledger.fetch_by_time_range("voice_event_log", start_ms=since_ms, end_ms=now)
        wechat_voice_rows = [
            r for r in voice_rows
            if str(r.get("event_type") or "").upper().startswith("WECHAT")
        ]
    except Exception:
        wechat_voice_rows = []

    all_rows = wechat_rows + wechat_voice_rows
    total_events = len(all_rows)

    inbound = sum(
        1 for r in all_rows
        if str(r.get("direction") or r.get("event_type") or "").upper()
        in ("INBOUND", "RECEIVE", "RECV", "WECHAT_RECV")
    )
    outbound = total_events - inbound

    success = sum(
        1 for r in all_rows
        if str(r.get("status") or "").upper() in ("OK", "SUCCESS", "SENT", "DELIVERED")
    )
    failed = total_events - success
    success_rate = round(success / total_events * 100, 1) if total_events > 0 else None

    # 频率限制统计
    rate_limited_count = sum(
        1 for r in all_rows
        if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT")
        or str(r.get("status") or "").upper() == "RATE_LIMITED"
    )

    # Top wxid 统计（按消息量排序）
    from collections import Counter, defaultdict
    wxid_counter: Counter = Counter()
    wxid_last_ts: dict = defaultdict(int)
    wxid_rate_limited: dict = defaultdict(bool)
    for r in all_rows:
        wxid = str(r.get("wxid") or r.get("from_wxid") or r.get("session_id") or "unknown")
        wxid_counter[wxid] += 1
        ts = r.get("ts_ms") or 0
        if ts > wxid_last_ts[wxid]:
            wxid_last_ts[wxid] = ts
        if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT"):
            wxid_rate_limited[wxid] = True

    top_wxids = [
        WeChatRateLimitItem(
            wxid=wxid,
            message_count=cnt,
            last_ts=wxid_last_ts.get(wxid),
            rate_limited=bool(wxid_rate_limited.get(wxid, False)),
        )
        for wxid, cnt in wxid_counter.most_common(10)
    ]

    # 通道健康状态
    if total_events == 0:
        channel_status = "UNKNOWN"
    elif success_rate is not None and success_rate >= 80:
        channel_status = "UP"
    elif success_rate is not None and success_rate >= 50:
        channel_status = "DEGRADED"
    else:
        channel_status = "DOWN"

    # 最近事件列表（倒序）
    recent_sorted = sorted(all_rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_events = [
        WeChatEventItem(
            request_id=r.get("request_id"),
            wxid=str(r.get("wxid") or r.get("from_wxid") or r.get("session_id") or ""),
            direction=(
                "inbound"
                if str(r.get("direction") or r.get("event_type") or "").upper()
                in ("INBOUND", "RECEIVE", "RECV", "WECHAT_RECV")
                else "outbound"
            ),
            status=str(r.get("status") or "UNKNOWN").upper(),
            reason_code=r.get("reason_code"),
            ts_ms=int(r.get("ts_ms") or 0),
        )
        for r in recent_sorted
    ]

    last_event_ts = max((r.get("ts_ms") or 0 for r in all_rows), default=None)

    return WeChatStatsResponse(
        window_minutes=window_minutes,
        channel_status=channel_status,
        total_events=total_events,
        inbound=inbound,
        outbound=outbound,
        success=success,
        failed=failed,
        success_rate=success_rate,
        rate_limited_count=rate_limited_count,
        top_wxids=top_wxids,
        recent_events=recent_events,
        last_event_ts=last_event_ts,
        timestamp=now,
    )


@router.post("/wechat/reset", response_model=Dict[str, Any])
async def reset_wechat_channel(
    reason: Optional[str] = Query(None, description="重置原因"),
):
    """TODO-37: 手动重置 WeChat 通道状态（清除频率限制计数器）。"""
    try:
        from host_brain.channels.wechat import WeChatChannel  # noqa: PLC0415
        _channel = WeChatChannel.get_instance()
        if _channel is not None and hasattr(_channel, "reset_rate_limits"):
            _channel.reset_rate_limits()
            return {
                "status": "OK",
                "reason_code": "WECHAT_CHANNEL_RESET",
                "detail": f"WeChat 通道频率限制已重置（原因：{reason or 'manual'}）",
                "timestamp": _now_ms(),
            }
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        logger.warning("WeChat 通道重置失败: %s", exc)
        return {
            "status": "DEGRADED",
            "reason_code": "WECHAT_RESET_FAILED",
            "detail": str(exc)[:200],
            "timestamp": _now_ms(),
        }
    return {
        "status": "OK",
        "reason_code": "WECHAT_CHANNEL_RESET_NOOP",
        "detail": "WeChat 通道重置（无状态实例，已记录）",
        "timestamp": _now_ms(),
    }


# ---------------------------------------------------------------------------
# TODO-34: GET /obs/retention/stats  — AuditRetention 保留策略统计
# ---------------------------------------------------------------------------
class RetentionTableItem(BaseModel):
    table: str
    candidate_count: int
    deleted_count: int
    protected: bool

class RetentionStatsResponse(BaseModel):
    last_run_ts: Optional[int]
    last_run_status: Optional[str]
    total_deleted: int
    total_candidates: int
    total_protected: int
    tables: List[RetentionTableItem]
    timestamp: int

@router.get("/retention/stats", response_model=RetentionStatsResponse)
async def get_retention_stats(
    limit: int = Query(5, ge=1, le=20, description="最近执行记录条数"),
):
    """TODO-34: AuditRetention 保留策略执行统计。"""
    ledger = _get_ledger()
    now = _now_ms()

    # 从 error_log 中查询 S_AUDIT_RETENTION 阶段的记录
    try:
        rows = ledger.fetch_by_time_range("error_log", start_ms=0, end_ms=now)
        retention_rows = [
            r for r in rows
            if str(r.get("stage") or "").upper() == "S_AUDIT_RETENTION"
        ]
    except Exception:
        retention_rows = []

    # 取最近一次执行记录
    recent = sorted(retention_rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    last_run_ts = recent[0].get("ts_ms") if recent else None
    last_run_status = str(recent[0].get("reason_code") or "UNKNOWN") if recent else None

    # 解析最近一次执行的详情
    tables: list[RetentionTableItem] = []
    total_deleted = 0
    total_candidates = 0
    total_protected = 0

    if recent:
        try:
            detail_raw = recent[0].get("detail") or "{}"
            detail = json.loads(detail_raw) if isinstance(detail_raw, str) else {}
            candidate_counts = dict(detail.get("candidate_counts") or {})
            deleted_counts = dict(detail.get("deleted_counts") or {})
            protected_counts = dict(detail.get("protected_counts") or {})

            all_tables = set(candidate_counts) | set(deleted_counts) | set(protected_counts)
            for tbl in sorted(all_tables):
                cand = int(candidate_counts.get(tbl) or 0)
                deleted = int(deleted_counts.get(tbl) or 0)
                protected = tbl in protected_counts
                tables.append(RetentionTableItem(
                    table=tbl,
                    candidate_count=cand,
                    deleted_count=deleted,
                    protected=protected,
                ))
                total_candidates += cand
                total_deleted += deleted
                total_protected += int(protected_counts.get(tbl) or 0)
        except Exception:
            pass

    return RetentionStatsResponse(
        last_run_ts=last_run_ts,
        last_run_status=last_run_status,
        total_deleted=total_deleted,
        total_candidates=total_candidates,
        total_protected=total_protected,
        tables=tables,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-40: GET /obs/qa/stats  — QA 违规统计
# ---------------------------------------------------------------------------
class QaViolationCodeItem(BaseModel):
    violation_code: str
    count: int
    pct: float

class QaStatsResponse(BaseModel):
    window_minutes: int
    total_violations: int
    degraded_count: int
    rewritten_count: int
    degrade_rate: Optional[float]
    rewrite_success_rate: Optional[float]
    violation_codes: List[QaViolationCodeItem]
    timeseries_labels: List[str]
    timeseries_counts: List[int]
    recent_violations: List[Dict[str, Any]]
    timestamp: int

@router.get("/qa/stats", response_model=QaStatsResponse)
async def get_qa_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
    limit: int = Query(20, ge=1, le=100, description="最近违规条数"),
):
    """TODO-40: QA 违规统计 — 违规码分布、降级率、重写成功率、时序趋势、最近违规列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("qa_violation_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 兼容旧表名 qa_violation
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("qa_violation", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total = len(rows)
    degraded_count = sum(
        1 for r in rows
        if str(r.get("action") or r.get("result") or "").upper() in ("DEGRADED", "FALLBACK", "DOWNGRADE")
    )
    rewritten_count = sum(
        1 for r in rows
        if str(r.get("action") or r.get("result") or "").upper() in ("REWRITTEN", "REWRITE", "PATCHED")
    )
    degrade_rate = round(degraded_count / total * 100, 1) if total > 0 else None
    rewrite_success_rate = round(rewritten_count / (degraded_count + rewritten_count) * 100, 1) if (degraded_count + rewritten_count) > 0 else None

    # 违规码分布
    from collections import Counter
    code_counter = Counter(
        str(r.get("violation_code") or r.get("reason_code") or "UNKNOWN").upper()
        for r in rows
    )
    violation_codes = [
        QaViolationCodeItem(
            violation_code=code,
            count=cnt,
            pct=round(cnt / total * 100, 1) if total > 0 else 0.0,
        )
        for code, cnt in code_counter.most_common(10)
    ]

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_counts = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_counts[idx] += 1

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    # 最近 N 条（倒序）
    recent_sorted = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_violations = [
        {
            "request_id": r.get("request_id"),
            "node_id": r.get("node_id"),
            "violation_code": str(r.get("violation_code") or r.get("reason_code") or "UNKNOWN").upper(),
            "action": str(r.get("action") or r.get("result") or "").upper() or None,
            "detail": (r.get("detail") or "")[:200],
            "ts_ms": r.get("ts_ms"),
        }
        for r in recent_sorted
    ]

    return QaStatsResponse(
        window_minutes=window_minutes,
        total_violations=total,
        degraded_count=degraded_count,
        rewritten_count=rewritten_count,
        degrade_rate=degrade_rate,
        rewrite_success_rate=rewrite_success_rate,
        violation_codes=violation_codes,
        timeseries_labels=labels,
        timeseries_counts=ts_counts,
        recent_violations=recent_violations,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-41: GET /obs/llm/call-stats  — LLM 调用成本统计
# ---------------------------------------------------------------------------
class LlmModelCallItem(BaseModel):
    model_id: str
    node_id: Optional[str]
    call_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: Optional[float]
    p95_latency_ms: Optional[int]

class LlmCallStatsResponse(BaseModel):
    window_minutes: int
    total_calls: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    avg_latency_ms: Optional[float]
    models: List[LlmModelCallItem]
    timeseries_labels: List[str]
    timeseries_calls: List[int]
    timeseries_tokens: List[int]
    timestamp: int

@router.get("/llm/call-stats", response_model=LlmCallStatsResponse)
async def get_llm_call_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    interval_minutes: int = Query(5, ge=1, le=60, description="时序聚合间隔（分钟）"),
):
    """TODO-41: LLM 调用成本统计 — 按 model_id 分组的 token 消耗/平均延迟/调用次数。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("llm_call_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 兼容 llm_node_call_log 表名
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("llm_node_call_log", start_ms=since_ms, end_ms=now)
        except Exception:
            rows = []

    total_calls = len(rows)
    total_prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    total_completion = sum(int(r.get("completion_tokens") or 0) for r in rows)
    total_tokens = total_prompt + total_completion
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None

    # 按 model_id 分组
    from collections import defaultdict
    model_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        mid = str(r.get("model_id") or r.get("model") or "unknown")
        model_map[mid].append(r)

    models = []
    for mid, mrows in sorted(model_map.items()):
        m_prompt = sum(int(r.get("prompt_tokens") or 0) for r in mrows)
        m_completion = sum(int(r.get("completion_tokens") or 0) for r in mrows)
        m_latencies = [r["latency_ms"] for r in mrows if r.get("latency_ms") is not None]
        m_avg_lat = round(sum(m_latencies) / len(m_latencies), 1) if m_latencies else None
        m_p95 = None
        if m_latencies:
            sl = sorted(m_latencies)
            m_p95 = sl[max(0, int(len(sl) * 0.95) - 1)]
        node_ids = list({str(r.get("node_id") or "") for r in mrows if r.get("node_id")})
        models.append(LlmModelCallItem(
            model_id=mid,
            node_id=node_ids[0] if len(node_ids) == 1 else None,
            call_count=len(mrows),
            total_prompt_tokens=m_prompt,
            total_completion_tokens=m_completion,
            total_tokens=m_prompt + m_completion,
            avg_latency_ms=m_avg_lat,
            p95_latency_ms=m_p95,
        ))

    # 时序聚合
    interval_ms = interval_minutes * 60 * 1000
    bucket_count = max(1, (window_minutes * 60 * 1000) // interval_ms)
    ts_calls = [0] * bucket_count
    ts_tokens = [0] * bucket_count
    for r in rows:
        ts = r.get("ts_ms") or 0
        idx = min(int((ts - since_ms) // interval_ms), bucket_count - 1)
        if 0 <= idx < bucket_count:
            ts_calls[idx] += 1
            ts_tokens[idx] += int(r.get("prompt_tokens") or 0) + int(r.get("completion_tokens") or 0)

    labels = []
    for i in range(bucket_count):
        ts_val = since_ms + i * interval_ms
        d = __import__("datetime").datetime.fromtimestamp(ts_val / 1000)
        if window_minutes <= 60:
            labels.append(d.strftime("%H:%M"))
        elif window_minutes <= 1440:
            labels.append(d.strftime("%m/%d %H:%M"))
        else:
            labels.append(d.strftime("%m/%d"))

    return LlmCallStatsResponse(
        window_minutes=window_minutes,
        total_calls=total_calls,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        total_tokens=total_tokens,
        avg_latency_ms=avg_latency,
        models=models,
        timeseries_labels=labels,
        timeseries_calls=ts_calls,
        timeseries_tokens=ts_tokens,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-42: GET /obs/checkpoints  — CheckpointManager 状态查询
# ---------------------------------------------------------------------------
class CheckpointItem(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload_summary: Optional[str]

class CheckpointsResponse(BaseModel):
    total: int
    items: List[CheckpointItem]
    timestamp: int

class CheckpointDetailResponse(BaseModel):
    checkpoint_id: str
    request_id: Optional[str]
    interrupt_reason: Optional[str]
    resume_deadline_at: Optional[int]
    created_at_ms: Optional[int]
    is_expired: bool
    payload: Optional[Any]
    timestamp: int

@router.get("/checkpoints", response_model=CheckpointsResponse)
async def get_checkpoints(
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    include_expired: bool = Query(False, description="是否包含已超时的 checkpoint"),
):
    """TODO-42: 最近 checkpoint 列表（checkpoint_id/request_id/interrupt_reason/resume_deadline_at/是否已超时）。"""
    ledger = _get_ledger()
    now = _now_ms()

    try:
        rows = ledger.fetch_by_time_range("checkpoints", start_ms=0, end_ms=now)
    except Exception:
        rows = []

    # 兼容 graph_checkpoint 表名
    if not rows:
        try:
            rows = ledger.fetch_by_time_range("graph_checkpoint", start_ms=0, end_ms=now)
        except Exception:
            rows = []

    # 按创建时间倒序
    rows_sorted = sorted(rows, key=lambda r: r.get("created_at_ms") or r.get("ts_ms") or 0, reverse=True)

    items = []
    for r in rows_sorted:
        deadline = r.get("resume_deadline_at")
        is_expired = bool(deadline and int(deadline) < now)
        if not include_expired and is_expired:
            continue
        payload_raw = r.get("payload") or r.get("payload_json")
        payload_summary = None
        if payload_raw:
            try:
                ps = json.dumps(payload_raw) if not isinstance(payload_raw, str) else payload_raw
                payload_summary = ps[:120] + "…" if len(ps) > 120 else ps
            except Exception:
                pass
        items.append(CheckpointItem(
            checkpoint_id=str(r.get("checkpoint_id") or r.get("id") or ""),
            request_id=r.get("request_id"),
            interrupt_reason=r.get("interrupt_reason"),
            resume_deadline_at=int(deadline) if deadline else None,
            created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
            is_expired=is_expired,
            payload_summary=payload_summary,
        ))
        if len(items) >= limit:
            break

    return CheckpointsResponse(total=len(items), items=items, timestamp=now)


@router.get("/checkpoints/{checkpoint_id}", response_model=CheckpointDetailResponse)
async def get_checkpoint_detail(checkpoint_id: str):
    """TODO-42: 单个 checkpoint 详情（含完整 payload）。"""
    ledger = _get_ledger()
    now = _now_ms()

    rows = []
    for table in ("checkpoints", "graph_checkpoint"):
        try:
            all_rows = ledger.fetch_by_time_range(table, start_ms=0, end_ms=now)
            rows = [
                r for r in all_rows
                if str(r.get("checkpoint_id") or r.get("id") or "") == checkpoint_id
            ]
            if rows:
                break
        except Exception:
            continue

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"error": "checkpoint_not_found", "checkpoint_id": checkpoint_id},
        )

    r = rows[0]
    deadline = r.get("resume_deadline_at")
    is_expired = bool(deadline and int(deadline) < now)
    payload_raw = r.get("payload") or r.get("payload_json")
    payload = _safe_json(payload_raw) if payload_raw else None

    return CheckpointDetailResponse(
        checkpoint_id=checkpoint_id,
        request_id=r.get("request_id"),
        interrupt_reason=r.get("interrupt_reason"),
        resume_deadline_at=int(deadline) if deadline else None,
        created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0),
        is_expired=is_expired,
        payload=payload,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-43: GET /obs/rate-limit/backend-stats  — RateLimitStore 后端状态
# ---------------------------------------------------------------------------
class RateLimitKeyItem(BaseModel):
    key: str
    current_count: int
    ttl_ms: Optional[int]
    window_ms: Optional[int]

class RateLimitBackendStatsResponse(BaseModel):
    backend_type: str          # "memory" | "sqlite" | "redis"
    is_degraded: bool
    degrade_reason: Optional[str]
    total_keys: int
    active_keys: int
    top_keys: List[RateLimitKeyItem]
    timestamp: int

@router.get("/rate-limit/backend-stats", response_model=RateLimitBackendStatsResponse)
async def get_rate_limit_backend_stats(
    top_k: int = Query(20, ge=1, le=100, description="返回 Top-K 活跃 key"),
):
    """TODO-43: RateLimitStore 后端状态 — 当前后端类型/是否降级/各 key 当前计数/TTL。"""
    ledger = _get_ledger()
    now = _now_ms()

    # 尝试从 rate_limit_store_log 读取最新状态快照
    backend_type = "memory"
    is_degraded = False
    degrade_reason = None
    top_keys: List[RateLimitKeyItem] = []

    try:
        rows = ledger.fetch_by_time_range("rate_limit_store_log", start_ms=0, end_ms=now)
        if rows:
            latest = sorted(rows, key=lambda r: r.get("ts_ms") or 0, reverse=True)[0]
            backend_type = str(latest.get("backend_type") or "memory")
            is_degraded = bool(latest.get("is_degraded") or False)
            degrade_reason = latest.get("degrade_reason")
    except Exception:
        pass

    # 从 governor_log 中统计各 key 的速率限制计数（近 5 分钟）
    try:
        since_5m = now - 5 * 60 * 1000
        gov_rows = ledger.fetch_by_time_range("governor_log", start_ms=since_5m, end_ms=now)
        rl_rows = [
            r for r in gov_rows
            if str(r.get("reason_code") or "").upper().startswith("RATE_LIMIT")
        ]
        from collections import Counter
        key_counter = Counter(
            str(r.get("session_id") or r.get("wxid") or r.get("user_id") or "unknown")
            for r in rl_rows
        )
        top_keys = [
            RateLimitKeyItem(
                key=k,
                current_count=cnt,
                ttl_ms=None,
                window_ms=5 * 60 * 1000,
            )
            for k, cnt in key_counter.most_common(top_k)
        ]
    except Exception:
        pass

    total_keys = len(top_keys)
    active_keys = sum(1 for k in top_keys if k.current_count > 0)

    return RateLimitBackendStatsResponse(
        backend_type=backend_type,
        is_degraded=is_degraded,
        degrade_reason=degrade_reason,
        total_keys=total_keys,
        active_keys=active_keys,
        top_keys=top_keys,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# TODO-44: GET /obs/vault/stats  — ArtifactVault Manifest 统计
# ---------------------------------------------------------------------------
class VaultManifestItem(BaseModel):
    manifest_id: str
    artifact_type: Optional[str]
    status: str          # "ACTIVE" | "UNDONE" | "GC"
    created_at_ms: Optional[int]
    undone_at_ms: Optional[int]
    gc_at_ms: Optional[int]
    is_reversible: bool
    summary: Optional[str]

class VaultStatsResponse(BaseModel):
    total_manifests: int
    active_count: int
    undone_count: int
    gc_count: int
    reversible_count: int
    reversible_rate: Optional[float]
    recent_manifests: List[VaultManifestItem]
    timestamp: int

@router.get("/vault/stats", response_model=VaultStatsResponse)
async def get_vault_stats(
    limit: int = Query(20, ge=1, le=100, description="最近 manifest 条数"),
):
    """TODO-44: ArtifactVault Manifest 统计 — ACTIVE/UNDONE/GC 数量、可逆比例、最近 manifest 列表。"""
    ledger = _get_ledger()
    now = _now_ms()

    rows = []
    for table in ("vault_manifest", "artifact_vault_manifest"):
        try:
            fetched = ledger.fetch_by_time_range(table, start_ms=0, end_ms=now)
            if fetched:
                rows = fetched
                break
        except Exception:
            continue

    total = len(rows)
    active_count = sum(1 for r in rows if str(r.get("status") or "").upper() == "ACTIVE")
    undone_count = sum(1 for r in rows if str(r.get("status") or "").upper() in ("UNDONE", "ROLLED_BACK"))
    gc_count = sum(1 for r in rows if str(r.get("status") or "").upper() in ("GC", "GARBAGE_COLLECTED", "DELETED"))
    reversible_count = sum(1 for r in rows if bool(r.get("is_reversible") or r.get("reversible")))
    reversible_rate = round(reversible_count / total * 100, 1) if total > 0 else None

    # 最近 N 条（按创建时间倒序）
    recent_sorted = sorted(rows, key=lambda r: r.get("created_at_ms") or r.get("ts_ms") or 0, reverse=True)[:limit]
    recent_manifests = [
        VaultManifestItem(
            manifest_id=str(r.get("manifest_id") or r.get("id") or ""),
            artifact_type=r.get("artifact_type"),
            status=str(r.get("status") or "UNKNOWN").upper(),
            created_at_ms=int(r.get("created_at_ms") or r.get("ts_ms") or 0) or None,
            undone_at_ms=int(r.get("undone_at_ms") or 0) or None,
            gc_at_ms=int(r.get("gc_at_ms") or 0) or None,
            is_reversible=bool(r.get("is_reversible") or r.get("reversible")),
            summary=(r.get("summary") or r.get("detail") or "")[:120] or None,
        )
        for r in recent_sorted
    ]

    return VaultStatsResponse(
        total_manifests=total,
        active_count=active_count,
        undone_count=undone_count,
        gc_count=gc_count,
        reversible_count=reversible_count,
        reversible_rate=reversible_rate,
        recent_manifests=recent_manifests,
        timestamp=now,
    )



