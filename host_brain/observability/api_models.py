"""Canonical Pydantic response models for the observability HTTP API.

Route modules import and re-export these names for compatibility.  Keeping the
DTOs transport-only and dependency-free prevents route-owner splits from
creating parallel, subtly different OpenAPI schemas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def _model_dump_compat(value: Any) -> Any:
    """Serialize an observability DTO across the supported Pydantic versions."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


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
    reason_code: Optional[str] = None
    detail: Optional[str] = None
    public_summary: Optional[str] = None


class ServicesHealthResponse(BaseModel):
    total: int
    up: int
    degraded: int
    down: int
    not_tested: int = 0
    unknown: int
    services: List[ServiceHealthItem]
    timestamp: int
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"


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
    projection_version: int = 0
    cursor: int = 0
    freshness_ts_ms: int = 0
    degraded_state: str = "REBUILDING"


class SessionItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    ts_ms: int
    status: str
    decision: Optional[str] = None
    reason_code: Optional[str] = None
    node_count: Optional[int] = None
    total_latency_ms: Optional[int] = None
    input_preview: Optional[str] = None


class SessionsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[SessionItem]
    timestamp: int
    projection: Optional[Dict[str, Any]] = None


class TribeEventItem(BaseModel):
    event_id: str
    event_type: str
    source_agent: str
    target_agent: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    schema_version: str
    ts_ms: int


class TribeEventsResponse(BaseModel):
    total: int
    items: List[TribeEventItem]
    timestamp: int
    projection: Optional[Dict[str, Any]] = None


class TraceNode(BaseModel):
    node_id: str
    node_type: str
    display_name: str
    side: Optional[str] = None
    provider: Optional[str] = None
    model_id: Optional[str] = None
    status: str
    reason_code: Optional[str] = None
    latency_ms: Optional[int] = None
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    ts_ms: Optional[int] = None
    error: Optional[str] = None
    group_id: Optional[str] = None
    owner_node_id: Optional[str] = None
    subgraph_id: Optional[str] = None
    parent_node_id: Optional[str] = None


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
    summary: Optional[Dict[str, Any]] = None
    subgraphs: List[Dict[str, Any]] = Field(default_factory=list)
    stream_events: List[Dict[str, Any]] = Field(default_factory=list)
    ts_ms: int
    timestamp: int
    projection: Optional[Dict[str, Any]] = None


class LiveTraceFeedItem(BaseModel):
    session: Any
    trace: Optional[TraceResponse] = None
    error: Optional[str] = None


class TraceLiveFeedSnapshot(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    items: List[LiveTraceFeedItem] = Field(default_factory=list)
    count: int
    timestamp: int
    projection: Optional[Dict[str, Any]] = None


class TraceLiveFeedNodeDelta(BaseModel):
    change_type: Literal["added", "updated"]
    node: TraceNode
    previous_status: Optional[str] = None


class TraceLiveFeedDeltaItem(BaseModel):
    request_id: str
    session_id: Optional[str] = None
    session: Optional[Any] = None
    trace_status: Optional[str] = None
    total_latency_ms: Optional[int] = None
    latest_node: Optional[TraceNode] = None
    changes: List[TraceLiveFeedNodeDelta] = Field(default_factory=list)
    change_count: int = 0


class TraceLiveFeedDelta(BaseModel):
    type: Literal["delta"] = "delta"
    items: List[TraceLiveFeedDeltaItem] = Field(default_factory=list)
    count: int
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


class StreamEventItem(BaseModel):
    request_id: str
    graph_id: str
    phase: str
    event_type: str
    node_id: Optional[str] = None
    chunk: Optional[str] = None
    ts_ms: int


class TraceStreamEventsResponse(BaseModel):
    request_id: str
    events: List[StreamEventItem]
    count: int
    timestamp: int
    projection: Optional[Dict[str, Any]] = None


class GraphSchemaNode(BaseModel):
    node_id: str
    display_name: Optional[str] = None


class GraphSchemaEdge(BaseModel):
    source: str
    target: str


class GraphSchemaResponse(BaseModel):
    graph_id: str
    version: str
    entry_node: Optional[str] = None
    active_nodes: List[str] = Field(default_factory=list)
    nodes: List[GraphSchemaNode] = Field(default_factory=list)
    edges: List[GraphSchemaEdge] = Field(default_factory=list)
    graph_meta: Optional[Dict[str, Any]] = None
    timestamp: int


__all__ = [
    "GraphSchemaEdge",
    "GraphSchemaNode",
    "GraphSchemaResponse",
    "LiveTraceFeedItem",
    "LlmNodeItem",
    "LlmNodesResponse",
    "MetricsTimeseriesResponse",
    "ServiceHealthItem",
    "ServicesHealthResponse",
    "SessionItem",
    "SessionsResponse",
    "StreamEventItem",
    "TimeseriesPoint",
    "TraceEdge",
    "TraceLiveFeedDelta",
    "TraceLiveFeedDeltaItem",
    "TraceLiveFeedNodeDelta",
    "TraceLiveFeedSnapshot",
    "TraceNode",
    "TraceResponse",
    "TraceStreamEventsResponse",
    "TribeEventItem",
    "TribeEventsResponse",
]
