"""
schemas 包 —— 核心数据结构与枚举类型，同时兼容 HTTP 请求/响应模型导出
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .memory_types import (
    ProposalType,
    ProposerClass,
    RiskLevel,
    EvidenceLevel,
    EpisodicRecord,
    CanonProposal,
)
from .routing_types import (
    TaskType,
    LatencyBudget,
    ModelTarget,
    RouteDecision,
)
from .context_pack import ContextPack


# ---------------------------------------------------------------------------
# HTTP 请求/响应 Pydantic 模型（内联，避免循环导入）
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = Field(..., description="Service status")
    version: str = Field(..., description="Application version")
    timestamp: int = Field(..., description="Response timestamp in ms")
    details: Dict[str, Any] = Field(default_factory=dict, description="Health details")


class ReadinessResponse(BaseModel):
    ready: bool = Field(..., description="Whether the service is ready")
    status: str = Field(..., description="Readiness status")
    reason_code: Optional[str] = Field(None, description="Readiness reason code")
    timestamp: int = Field(..., description="Response timestamp in ms")
    summary: Dict[str, Any] = Field(default_factory=dict, description="Readiness summary")


class ObservabilityRequest(BaseModel):
    request_id: str = Field(..., description="Request id to inspect")


class ObservabilityResponse(BaseModel):
    request_id: str = Field(..., description="Request id")
    trace_status: str = Field(..., description="Trace status")
    timeline_total: int = Field(..., description="Timeline event count")
    sources: Dict[str, int] = Field(..., description="Timeline sources")
    timeline: List[Dict[str, Any]] = Field(..., description="Timeline rows")
    timestamp: int = Field(..., description="Response timestamp in ms")


class ChatRequest(BaseModel):
    """POST /v1/chat request body."""
    message: str = Field(..., description="User message")
    session_id: Optional[str] = Field(None, description="Optional session id")
    thread_id: Optional[str] = Field(None, description="Optional thread id")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="Optional intent draft")
    scene_type: Optional[str] = Field(None, description="Optional scene type override")
    mood_state: Optional[str] = Field(None, description="Optional mood state")
    emotion_tag: Optional[str] = Field(None, description="Optional emotion tag")
    emotion_intensity: Optional[float] = Field(None, description="Optional emotion intensity [0,1]")
    emotion_context: Optional[Dict[str, Any]] = Field(None, description="Optional emotion context")
    emotion_range_hint: Optional[str] = Field(None, description="Optional emotion range hint")


class ChatResponse(BaseModel):
    """POST /v1/chat response body."""
    request_id: str = Field(..., description="Request id")
    narrative: Optional[str] = Field(None, description="Narrative response")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="Intent draft")
    decision: Optional[str] = Field(None, description="Governor decision")
    reason_code: Optional[str] = Field(None, description="Reason code")
    approval_token_ref: Optional[str] = Field(None, description="Approval token reference")
    approval_tier: Optional[str] = Field(None, description="Approval tier")
    quality: Dict[str, Any] = Field(default_factory=dict, description="Quality summary")
    audit_refs: Dict[str, Any] = Field(default_factory=dict, description="Audit references")
    timestamp: int = Field(..., description="Response timestamp in ms")


class MetricsResponse(BaseModel):
    """GET /metrics response body."""
    uptime_seconds: int = Field(..., description="Service uptime in seconds")
    total_requests: int = Field(..., description="Total request count")
    total_chat_requests: int = Field(..., description="Total chat request count")
    total_rejections: int = Field(..., description="Total rejection count")
    total_errors: int = Field(..., description="Total error count")
    reason_code_distribution: Dict[str, int] = Field(default_factory=dict, description="Reason code distribution")
    timestamp: int = Field(..., description="Response timestamp in ms")


class EdgeEventsRequest(BaseModel):
    """POST /v1/edge/events request body."""
    events: List[Dict[str, Any]] = Field(..., description="Edge event list")


class EdgeEventsResponse(BaseModel):
    """POST /v1/edge/events response body."""
    success: bool = Field(..., description="Whether all events were accepted")
    total: int = Field(..., description="Total event count")
    accepted: int = Field(..., description="Accepted event count")
    rejected: int = Field(..., description="Rejected event count")
    reason_code: Optional[str] = Field(None, description="Failure reason code")
    detail: Optional[str] = Field(None, description="Failure detail")
    rejected_indices: List[int] = Field(default_factory=list, description="Rejected event indices")
    timestamp: int = Field(..., description="Response timestamp in ms")


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error type")
    reason_code: str = Field(..., description="Reason code")
    detail: str = Field(..., description="Error detail")
    request_id: Optional[str] = Field(None, description="Related request id")
    timestamp: int = Field(..., description="Error timestamp in ms")


__all__ = [
    # 枚举与数据结构
    "ProposalType",
    "ProposerClass",
    "RiskLevel",
    "EvidenceLevel",
    "EpisodicRecord",
    "CanonProposal",
    "TaskType",
    "LatencyBudget",
    "ModelTarget",
    "RouteDecision",
    "ContextPack",
    # HTTP 模型
    "ChatRequest",
    "ChatResponse",
    "MetricsResponse",
    "EdgeEventsRequest",
    "EdgeEventsResponse",
    "ErrorResponse",
    "HealthResponse",
    "ReadinessResponse",
    "ObservabilityRequest",
    "ObservabilityResponse",
]
