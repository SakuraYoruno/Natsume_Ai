"""Web UI路由模块 - 链路可视化面板数据API

实现链路可视化面板的Web UI数据接口，提供时间线聚合、状态分布、链路拓扑等可视化数据。
"""

from datetime import datetime
from typing import Annotated, Dict, List, Optional, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.core.ids import new_request_id
from host_brain.app.observability_runtime.projection_runtime import (
    SESSION_INDEX_KEY,
    SESSION_KIND,
    TRACE_KIND,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    read_all_from_store,
    read_from_store,
    response_meta,
)

# Web UI API路由
router = APIRouter(prefix="/ui", tags=["web-ui"])

# 数据模型定义
class TimelineAggregationResponse(BaseModel):
    """时间线聚合查询响应"""
    request_id: str = Field(..., description="请求ID")
    start_time: int = Field(..., description="开始时间戳(毫秒)")
    end_time: int = Field(..., description="结束时间戳(毫秒)")
    interval: str = Field(..., description="聚合间隔")
    intervals: List[Dict[str, Any]] = Field(..., description="时间间隔数据")
    total_requests: int = Field(..., description="总请求数")
    success_rate: float = Field(..., description="成功率")
    avg_duration: float = Field(..., description="平均耗时")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

class StatusDistributionResponse(BaseModel):
    """状态分布统计响应"""
    request_id: str = Field(..., description="请求ID")
    start_time: int = Field(..., description="开始时间戳(毫秒)")
    end_time: int = Field(..., description="结束时间戳(毫秒)")
    status_counts: Dict[str, int] = Field(..., description="状态计数")
    total_requests: int = Field(..., description="总请求数")
    distribution_percentage: Dict[str, float] = Field(..., description="分布百分比")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

class RequestDetailResponse(BaseModel):
    """请求详情响应"""
    request_id: str = Field(..., description="请求ID")
    timestamp: int = Field(..., description="请求时间戳(毫秒)")
    status: str = Field(..., description="状态")
    duration: float = Field(..., description="耗时(毫秒)")
    request_type: str = Field(..., description="请求类型")
    user_id: Optional[str] = Field(None, description="用户ID")
    error_message: Optional[str] = Field(None, description="错误信息")
    trace_data: Dict[str, Any] = Field(..., description="追踪数据")
    query_timestamp: int = Field(..., description="查询时间戳(毫秒)")
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

class LinkTopologyResponse(BaseModel):
    """链路拓扑响应"""
    request_id: str = Field(..., description="请求ID")
    start_time: int = Field(..., description="开始时间戳(毫秒)")
    end_time: int = Field(..., description="结束时间戳(毫秒)")
    nodes: List[Dict[str, Any]] = Field(..., description="节点列表")
    edges: List[Dict[str, Any]] = Field(..., description="边列表")
    clusters: List[Dict[str, Any]] = Field(..., description="集群列表")
    timestamp: int = Field(..., description="响应时间戳(毫秒)")
    schema_version: str = "Natsume.operator.query_projection.v1"
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"

@router.get("/timeline", response_model=TimelineAggregationResponse)
def get_timeline_aggregation(
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    interval: str = Query("1h", description="聚合间隔: 1m,5m,15m,1h,1d"),
    status: Optional[str] = Query(None, description="状态过滤"),
    request_type: Optional[str] = Query(None, description="请求类型过滤"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
    *,
    projection_store: Annotated[
        Any,
        Depends(require_obs_query_projection_store),
    ],
):
    """Aggregate the already-materialized public session index."""
    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)
    payload, meta = read_from_store(
        projection_store,
        SESSION_KIND,
        SESSION_INDEX_KEY,
    )
    rows = [
        dict(row)
        for row in list((payload or {}).get("items") or [])
        if isinstance(row, dict)
        and start_ts <= int(row.get("ts_ms") or 0) <= end_ts
    ]
    if status:
        allowed = {value.strip().lower() for value in status.split(",") if value.strip()}
        rows = [row for row in rows if str(row.get("status") or "").lower() in allowed]
    if request_type:
        allowed_types = {
            value.strip().lower() for value in request_type.split(",") if value.strip()
        }
        rows = [
            row
            for row in rows
            if str(row.get("request_type") or row.get("channel") or "unknown").lower()
            in allowed_types
        ]
    interval_ms = _parse_interval_ms(interval)
    intervals: list[dict[str, Any]] = []
    bucket_start = start_ts
    while bucket_start < end_ts:
        bucket_end = min(bucket_start + interval_ms, end_ts)
        bucket = [
            row
            for row in rows
            if bucket_start <= int(row.get("ts_ms") or 0) < bucket_end
        ]
        successes = sum(
            1
            for row in bucket
            if str(row.get("status") or "").lower() in {"ok", "approve", "success"}
        )
        durations = [
            int(row.get("duration_ms") or 0)
            for row in bucket
            if int(row.get("duration_ms") or 0) > 0
        ]
        intervals.append(
            {
                "start_time": bucket_start,
                "end_time": bucket_end,
                "total": len(bucket),
                "success": successes,
                "failure": len(bucket) - successes,
                "success_rate": round(successes / len(bucket), 4) if bucket else 0.0,
                "avg_duration": round(sum(durations) / len(durations), 2)
                if durations
                else 0.0,
            }
        )
        bucket_start = bucket_end
    successes = sum(item["success"] for item in intervals)
    durations = [
        int(row.get("duration_ms") or 0)
        for row in rows
        if int(row.get("duration_ms") or 0) > 0
    ]
    return TimelineAggregationResponse(
        request_id=new_request_id(),
        start_time=start_ts,
        end_time=end_ts,
        interval=interval,
        intervals=intervals,
        total_requests=len(rows),
        success_rate=round(successes / len(rows), 4) if rows else 0.0,
        avg_duration=round(sum(durations) / len(durations), 2) if durations else 0.0,
        timestamp=int(datetime.now().timestamp() * 1000),
        **response_meta(meta),
    )

@router.get("/status-distribution", response_model=StatusDistributionResponse)
def get_status_distribution(
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    request_type: Optional[str] = Query(None, description="请求类型过滤"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
    *,
    projection_store: Annotated[
        Any,
        Depends(require_obs_query_projection_store),
    ],
):
    """Aggregate status from the public durable session projection."""

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)
    payload, meta = read_from_store(
        projection_store,
        SESSION_KIND,
        SESSION_INDEX_KEY,
    )
    allowed_types = {
        value.strip().lower() for value in str(request_type or "").split(",") if value.strip()
    }
    rows = [
        dict(row)
        for row in list((payload or {}).get("items") or [])
        if isinstance(row, dict)
        and start_ts <= int(row.get("ts_ms") or 0) <= end_ts
        and (
            not allowed_types
            or str(row.get("request_type") or row.get("channel") or "unknown").lower()
            in allowed_types
        )
    ][: max(1, int(limit))]
    status_counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status") or row.get("decision") or "UNKNOWN").upper()
        status_counts[key] = status_counts.get(key, 0) + 1
    total = len(rows)
    distribution = {
        key: round(count / total * 100, 2) if total else 0.0
        for key, count in status_counts.items()
    }
    return StatusDistributionResponse(
        request_id=new_request_id(),
        start_time=start_ts,
        end_time=end_ts,
        status_counts=dict(sorted(status_counts.items())),
        total_requests=total,
        distribution_percentage=dict(sorted(distribution.items())),
        timestamp=int(datetime.now().timestamp() * 1000),
        **response_meta(meta),
    )

@router.get("/request/{request_id}", response_model=RequestDetailResponse)
def get_request_detail(
    request_id: str,
    *,
    projection_store: Annotated[
        Any,
        Depends(require_obs_query_projection_store),
    ],
) -> dict:
    """Read request detail from the durable public Trace projection."""
    payload, meta = read_from_store(projection_store, TRACE_KIND, request_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "request_projection_not_ready", **response_meta(meta)},
        )
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    status = str(payload.get("status") or "UNKNOWN")
    duration = float(summary.get("total_latency_ms") or payload.get("duration_ms") or 0)
    return RequestDetailResponse(
        request_id=request_id,
        timestamp=int(payload.get("ts_ms") or payload.get("timestamp") or 0),
        status=status,
        duration=duration,
        request_type=str(summary.get("request_type") or "unknown"),
        user_id=None,
        error_message=(str(summary.get("reason_code") or "") or None)
        if status.upper() in {"REJECT", "FAILED", "ERROR"}
        else None,
        trace_data=payload,
        query_timestamp=int(datetime.now().timestamp() * 1000),
        **response_meta(meta),
    )

@router.get("/link-topology", response_model=LinkTopologyResponse)
def get_link_topology(
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    min_connections: int = Query(1, description="最小连接数阈值"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
    *,
    projection_store: Annotated[
        Any,
        Depends(require_obs_query_projection_store),
    ],
):
    """Build topology solely from fresh public Trace projection documents."""

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)
    traces, meta = read_all_from_store(projection_store, TRACE_KIND)
    request_sources: dict[str, set[str]] = {}
    for trace in traces[: max(1, int(limit))]:
        ts_ms = int(trace.get("ts_ms") or trace.get("timestamp") or 0)
        if ts_ms and not start_ts <= ts_ms <= end_ts:
            continue
        request_id = str(trace.get("request_id") or "").strip()
        if not request_id:
            continue
        sources: set[str] = set()
        for field in ("nodes", "timeline", "events"):
            for raw in list(trace.get(field) or []):
                if not isinstance(raw, dict):
                    continue
                source = str(
                    raw.get("source")
                    or raw.get("stage")
                    or raw.get("kind")
                    or raw.get("type")
                    or ""
                ).strip()
                if source:
                    sources.add(source[:120])
        if sources:
            request_sources[request_id] = sources
    all_sources = set().union(*request_sources.values()) if request_sources else set()
    nodes = [
        {"id": source, "label": source.replace("_", " ").title(), "type": "trace_stage"}
        for source in sorted(all_sources)
    ]
    edge_counts: dict[tuple[str, str], int] = {}
    for sources in request_sources.values():
        ordered = sorted(sources)
        for index, source in enumerate(ordered):
            for target in ordered[index + 1 :]:
                edge_counts[(source, target)] = edge_counts.get((source, target), 0) + 1
    edges = [
        {"source": pair[0], "target": pair[1], "weight": count, "label": f"{count} requests"}
        for pair, count in sorted(edge_counts.items())
        if count >= max(1, int(min_connections))
    ]
    clusters_by_prefix: dict[str, list[str]] = {}
    for source in all_sources:
        clusters_by_prefix.setdefault(source.split("_")[0], []).append(source)
    clusters = [
        {"id": prefix, "label": prefix.title(), "members": sorted(members)}
        for prefix, members in sorted(clusters_by_prefix.items())
    ]
    return LinkTopologyResponse(
        request_id=new_request_id(),
        start_time=start_ts,
        end_time=end_ts,
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        timestamp=int(datetime.now().timestamp() * 1000),
        **response_meta(meta),
    )

@router.get("/search-requests")
def search_requests(
    query: str = Query(..., description="搜索关键词"),
    start_time: datetime = Query(..., description="开始时间"),
    end_time: datetime = Query(..., description="结束时间"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页大小"),
    limit: int = Query(100, ge=1, le=10000, description="最大返回记录数"),
    offset: int = Query(0, ge=0, description="分页偏移量"),
    *,
    projection_store: Annotated[
        Any,
        Depends(require_obs_query_projection_store),
    ],
):
    """Search public identifiers and statuses in the session projection."""
    payload, meta = read_from_store(
        projection_store,
        SESSION_KIND,
        SESSION_INDEX_KEY,
    )
    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)
    keyword = str(query or "").strip().lower()
    rows = []
    for raw in list((payload or {}).get("items") or []):
        if not isinstance(raw, dict):
            continue
        ts_ms = int(raw.get("ts_ms") or 0)
        if not start_ts <= ts_ms <= end_ts:
            continue
        public_values = (
            raw.get("request_id"),
            raw.get("session_id"),
            raw.get("status"),
            raw.get("decision"),
            raw.get("reason_code"),
            raw.get("channel"),
        )
        if keyword and keyword not in " ".join(str(value or "") for value in public_values).lower():
            continue
        rows.append(
            {
                "request_id": str(raw.get("request_id") or ""),
                "timestamp": ts_ms,
                "status": str(raw.get("decision") or raw.get("status") or "UNKNOWN").upper(),
                "type": str(raw.get("request_type") or raw.get("channel") or "unknown"),
                "reason_code": raw.get("reason_code"),
            }
        )
    start = (page - 1) * page_size
    return {
        "results": rows[start : start + page_size],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        **response_meta(meta),
    }


def _parse_interval_ms(value: str) -> int:
    normalized = str(value or "").strip().lower()
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    for suffix, multiplier in multipliers.items():
        if normalized.endswith(suffix):
            try:
                return max(1, int(normalized[:-1])) * multiplier
            except ValueError:
                break
    return 3_600_000
