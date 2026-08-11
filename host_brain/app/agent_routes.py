"""Visual Agent HTTP compatibility boundary.

``POST /v1/agent/run`` used to start a second, route-local execution runtime.  That
runtime could plan repeatedly and call ClawdBot/Edge without a HostCommand,
immutable TaskGraph, node grant, durable attempt, or acceptance receipt.  The
route remains present so existing clients receive a deterministic SSE terminal
event, but execution is fail-closed until a Visual Agent proposal is admitted as
a governed TaskGraph revision.
"""

from __future__ import annotations

import time
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from host_brain.app.vision.clawdbot_protocol import (
    VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.core.ids import new_request_id

DEFAULT_MAX_ROUNDS = 5
DEFAULT_ROUND_TIMEOUT_SEC = 30
DEFAULT_TOTAL_TIMEOUT_SEC = 120
# ---------------------------------------------------------------------------
# 路由实例
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/v1/agent", tags=["agent"])


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------

class AgentRunRequest(BaseModel):
    """POST /v1/agent/run 请求体。"""

    # 自动化目标（必填）
    goal: str = Field(..., description="自动化目标，自然语言描述，如'填写并提交登录表单'")

    # 会话 ID（可选，不传则自动生成）
    session_id: Optional[str] = Field(
        None,
        description="兼容会话 ID；仅回显为拒绝事件关联标识，不绑定执行端。",
    )

    # Agent Loop 参数
    max_rounds: int = Field(
        DEFAULT_MAX_ROUNDS,
        ge=1,
        le=20,
        description=f"兼容字段；默认 {DEFAULT_MAX_ROUNDS}，执行关闭期间忽略。",
    )
    round_timeout_sec: float = Field(
        DEFAULT_ROUND_TIMEOUT_SEC,
        ge=5.0,
        le=120.0,
        description=f"兼容字段；默认 {DEFAULT_ROUND_TIMEOUT_SEC}s，执行关闭期间忽略。",
    )
    total_timeout_sec: float = Field(
        DEFAULT_TOTAL_TIMEOUT_SEC,
        ge=10.0,
        le=600.0,
        description=f"兼容字段；默认 {DEFAULT_TOTAL_TIMEOUT_SEC}s，执行关闭期间忽略。",
    )

    # 图片上传授权（与 /v1/chat 的非 text 门禁保持一致）
    image_upload_consent: bool = Field(
        False,
        description="兼容字段；执行关闭期间不会截图或上传。",
    )

    # 开发/测试模式
    use_mock: bool = Field(
        False,
        description="兼容字段；生产执行关闭期间忽略，不会构造 Mock ClawdBot。",
    )

    # Edge RPC 模式（推荐）：通过 Edge RPC 网关执行截图和操作
    use_edge_rpc: bool = Field(
        False,
        description="兼容字段；生产执行关闭期间忽略，不会连接 Edge RPC。",
    )

    # Edge RPC 地址（可选，覆盖环境变量）
    edge_rpc_base_url: Optional[str] = Field(
        None,
        description="兼容字段；生产执行关闭期间忽略且不会回显。",
    )

    # ClawdBot 地址（可选，覆盖环境变量，use_edge_rpc=False 时生效）
    clawdbot_base_url: Optional[str] = Field(
        None,
        description="兼容字段；生产执行关闭期间忽略且不会回显。",
    )


class AgentInfoResponse(BaseModel):
    """GET /v1/agent/info 响应体。"""
    version: str
    execution_available: bool
    reason_code: str
    default_max_rounds: int
    default_round_timeout_sec: float
    default_total_timeout_sec: float
    clawdbot_base_url: str
    mock_mode_available: bool
    timestamp: int


async def _agent_loop_sse_generator(
    *,
    request_id: str,
    session_id: str,
    goal: str,
    max_rounds: int,
    round_timeout_sec: float,
    total_timeout_sec: float,
    image_upload_consent: bool,
    use_mock: bool,
    use_edge_rpc: bool,
    clawdbot_base_url: str,
    edge_rpc_base_url: str,
) -> AsyncGenerator[str, None]:
    """Return a stable rejection without resolving or invoking execution ports."""

    import json

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # Compatibility-only inputs are deliberately not used to resolve a model,
    # Ledger, Edge session, HTTP client, or automation executor.
    _ = (
        max_rounds,
        round_timeout_sec,
        total_timeout_sec,
        image_upload_consent,
        use_mock,
        use_edge_rpc,
        clawdbot_base_url,
        edge_rpc_base_url,
    )

    yield _sse({
        "phase": "AGENT_INIT",
        "request_id": request_id,
        "session_id": session_id,
        "goal": goal,
        "client_mode": "governed_admission_required",
        "client_url": None,
        "llm_available": False,
        "side_effects_executed": False,
        "reason_code": VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
    })
    yield _sse({
        "phase": "LOOP_DONE",
        "request_id": request_id,
        "goal": goal,
        "goal_achieved": False,
        "final_status": "REJECTED",
        "reason_code": VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
        "side_effects_executed": False,
        "total_rounds": 0,
        "total_latency_ms": 0,
        "round_summaries": [],
    })


# ---------------------------------------------------------------------------
# POST /v1/agent/run — 启动 Agent Loop
# ---------------------------------------------------------------------------

@router.post("/run")
async def agent_run(body: AgentRunRequest, request: Request) -> StreamingResponse:
    """Reject the legacy loop while preserving the two-event SSE envelope.

    SSE 事件序列：
      AGENT_INIT → governed admission required
      LOOP_DONE  → REJECTED, side_effects_executed=false
    """
    del request
    request_id = new_request_id()
    session_id = str(body.session_id or request_id).strip() or request_id

    return StreamingResponse(
        _agent_loop_sse_generator(
            request_id=request_id,
            session_id=session_id,
            goal=body.goal,
            max_rounds=body.max_rounds,
            round_timeout_sec=body.round_timeout_sec,
            total_timeout_sec=body.total_timeout_sec,
            image_upload_consent=body.image_upload_consent,
            use_mock=body.use_mock,
            use_edge_rpc=body.use_edge_rpc,
            clawdbot_base_url="",
            edge_rpc_base_url="",
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )


# ---------------------------------------------------------------------------
# GET /v1/agent/info — 返回 Agent Loop 配置信息
# ---------------------------------------------------------------------------

@router.get("/info", response_model=AgentInfoResponse)
async def agent_info() -> AgentInfoResponse:
    """Return compatibility metadata; direct loop execution is unavailable."""
    return AgentInfoResponse(
        version="v1",
        execution_available=False,
        reason_code=VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
        default_max_rounds=DEFAULT_MAX_ROUNDS,
        default_round_timeout_sec=DEFAULT_ROUND_TIMEOUT_SEC,
        default_total_timeout_sec=DEFAULT_TOTAL_TIMEOUT_SEC,
        clawdbot_base_url="",
        mock_mode_available=False,
        timestamp=int(time.time() * 1000),
    )
