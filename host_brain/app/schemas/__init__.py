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
from .agent_envelope import (
    AGENT_ENVELOPE_SCHEMA_VERSION,
    AgentEnvelope,
)


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
    person_id: Optional[str] = Field(None, description="Optional canonical person id")
    session_id: Optional[str] = Field(None, description="Optional session id")
    thread_id: Optional[str] = Field(None, description="Optional thread id")
    intent_draft: Optional[Dict[str, Any]] = Field(None, description="Optional intent draft")
    scene_type: Optional[str] = Field(None, description="Optional scene type override")
    mood_state: Optional[str] = Field(None, description="Optional mood state")
    emotion_tag: Optional[str] = Field(None, description="Optional emotion tag")
    emotion_intensity: Optional[float] = Field(None, description="Optional emotion intensity [0,1]")
    emotion_context: Optional[Dict[str, Any]] = Field(None, description="Optional emotion context")
    emotion_range_hint: Optional[str] = Field(None, description="Optional emotion range hint")
    input_source: Optional[str] = Field(None, description="Optional input source, e.g. ASR/WECHAT")
    suppress_edge_tts: Optional[bool] = Field(None, description="Optional per-request flag to suppress edge TTS")
    suppress_edge_vts: Optional[bool] = Field(None, description="Optional per-request flag to suppress edge VTS")
    reply_channel: Optional[str] = Field(None, description="Optional reply channel, e.g. WECHAT")
    reply_target: Optional[str] = Field(None, description="Optional reply target id for channel delivery")
    render_target_lang: Optional[str] = Field(
        None,
        description="Legacy language hint, e.g. zh/ja. Output modality should prefer render_profile.",
    )
    render_profile: Optional[str] = Field(
        None,
        description="Optional output render profile, e.g. zh_text for text or ja_voice for Japanese voice.",
    )
    response_form: Optional[str] = Field(
        None,
        description="Optional desired response form, e.g. text or voice.",
    )
    render_policy: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional compressed reply policy for the text surface layer.",
    )
    surface_task_control: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional compressed surface task controls used before final ReplyControlPacket compile.",
    )
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="Optional structured inbound attachments")
    multimodal_observations: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional structured multimodal observations derived from attachments",
    )
    attachment_summary: Optional[str] = Field(
        None,
        description="Optional concise multimodal summary for chat context and memory metadata",
    )
    # [任务4] SCENE_VISION_UI 专用字段：视觉上下文
    # 支持格式：
    #   image_base64: str       — Base64 编码的截图（优先级最高）
    #   image_url: str          — 图片 URL
    #   image_mime: str         — 图片 MIME 类型（默认 "image/png"）
    #   screen_description: str — 文字描述的界面内容（降级模式）
    #   app_name: str           — 应用程序名称
    #   os_info: str            — 操作系统信息
    #   resolution: str         — 屏幕分辨率（如 "1920x1080"）
    #   error_text: str         — 界面中的错误信息
    #   selected_text: str      — 用户选中的文本
    vision_context: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional vision context for SCENE_VISION_UI (image_base64/image_url/screen_description etc.)",
    )
    # 响应格式：text（自然语言建议，给用户看）或 actions（结构化指令，给 ClawdBot 执行）
    # 默认 text，兼容现有行为。
    # 设为 actions 时，夏目会分析截图并生成 ActionPlan JSON，
    # COMMIT 事件中的 action_plan 字段包含完整指令序列。
    response_format: str = Field(
        "text",
        description="视觉场景响应格式：'text'（自然语言建议）或 'actions'（ClawdBot 可执行指令序列）",
    )

    # 图片上传隐私授权标志
    # 屏幕截图属高敏隐私内容，默认禁止上传至云端 API。
    # 需要上传原图时，必须显式传入 true 才允许发送图片。
    # 若为 false（默认），即使 vision_context 中包含 image_base64/image_url，
    # 也只会使用文字描述模式（screen_description / message）进行分析。
    image_upload_consent: bool = Field(
        False,
        description=(
            "用户隐私授权标志：是否允许将 vision_context 中的图片发送给云端 API。"
            "屏幕截图属高敏隐私内容，默认 false（禁止上传）。"
            "仅在用户明确知晓并同意图片将被发送至第三方服务时，才应传入 true。"
        ),
    )


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
    "AGENT_ENVELOPE_SCHEMA_VERSION",
    "AgentEnvelope",
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
