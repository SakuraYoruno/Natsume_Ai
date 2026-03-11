"""Agent Loop REST API 路由。

端点：
  POST /v1/agent/run   — 启动 Agent Loop，返回 SSE 流式进度
  GET  /v1/agent/info  — 返回 Agent Loop 配置信息（版本、默认参数等）

设计原则：
  - 与 /v1/chat 保持一致的 SSE 响应格式（data: {...}\\n\\n）
  - llm_runtime 通过 resolve_host_llm_runtime_async 自动解析（与 chat_handler_vision 一致）
  - ClawdBot 地址通过环境变量 CLAWDBOT_BASE_URL 配置，默认 http://localhost:7788
  - use_mock=True 时使用 MockClawdbotClient（开发/测试模式，无需真实 ClawdBot）
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from host_brain.app.vision.agent_loop import (
    ClawdbotClient,
    MockClawdbotClient,
    run_agent_loop,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_ROUND_TIMEOUT_SEC,
    DEFAULT_TOTAL_TIMEOUT_SEC,
)
from host_brain.app.dependencies import get_ledger
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.agent_routes")

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
        description="会话 ID，用于 ClawdBot 识别目标窗口/进程。不传则自动生成。",
    )

    # Agent Loop 参数
    max_rounds: int = Field(
        DEFAULT_MAX_ROUNDS,
        ge=1,
        le=20,
        description=f"最大循环轮数（默认 {DEFAULT_MAX_ROUNDS}，最大 20）",
    )
    round_timeout_sec: float = Field(
        DEFAULT_ROUND_TIMEOUT_SEC,
        ge=5.0,
        le=120.0,
        description=f"单轮超时秒数（默认 {DEFAULT_ROUND_TIMEOUT_SEC}s）",
    )
    total_timeout_sec: float = Field(
        DEFAULT_TOTAL_TIMEOUT_SEC,
        ge=10.0,
        le=600.0,
        description=f"总超时秒数（默认 {DEFAULT_TOTAL_TIMEOUT_SEC}s）",
    )

    # 图片上传授权（与 chat_handler_vision 保持一致）
    image_upload_consent: bool = Field(
        False,
        description="是否授权将截图上传给云端 LLM 分析（默认 False，截图仅在本地处理）",
    )

    # 开发/测试模式
    use_mock: bool = Field(
        False,
        description="是否使用 Mock ClawdBot（开发/测试模式，不发送真实 HTTP 请求）",
    )

    # ClawdBot 地址（可选，覆盖环境变量）
    clawdbot_base_url: Optional[str] = Field(
        None,
        description="ClawdBot 服务地址（可选，默认读取 CLAWDBOT_BASE_URL 环境变量）",
    )


class AgentInfoResponse(BaseModel):
    """GET /v1/agent/info 响应体。"""
    version: str
    default_max_rounds: int
    default_round_timeout_sec: float
    default_total_timeout_sec: float
    clawdbot_base_url: str
    mock_mode_available: bool
    timestamp: int


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_clawdbot_base_url(override: Optional[str] = None) -> str:
    """获取 ClawdBot 服务地址（优先级：请求参数 > 环境变量 > 默认值）。"""
    if override and override.strip():
        return override.strip().rstrip("/")
    env_url = str(os.environ.get("CLAWDBOT_BASE_URL") or "").strip().rstrip("/")
    return env_url or "http://localhost:7788"


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
    clawdbot_base_url: str,
) -> AsyncGenerator[str, None]:
    import json

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # ── 获取 ledger 实例（失败不阻断主流程）────────────────────────────
    try:
        ledger = get_ledger()
    except Exception as exc:
        _logger.warning("agent_routes: ledger 获取失败，持久化将被跳过: %s", exc)
        ledger = None

    # ── 解析 LLM 运行时配置 ──────────────────────────────────────────────
    try:
        from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async
        llm_runtime: Dict[str, Any] = await resolve_host_llm_runtime_async(
            scene_type="SCENE_VISION_UI",
        )
    except Exception as exc:
        _logger.warning("agent_routes: llm_runtime 解析失败，使用空配置: %s", exc)
        llm_runtime = {"available": False}

    # ── 构建 ClawdBot 客户端 ─────────────────────────────────────────────
    if use_mock:
        clawdbot_client = MockClawdbotClient()
        _logger.info(
            "agent_routes: 使用 MockClawdbotClient（开发模式）",
            extra={"request_id": request_id},
        )
    else:
        clawdbot_client = ClawdbotClient(
            base_url=clawdbot_base_url,
            timeout_sec=round_timeout_sec,
        )
        _logger.info(
            "agent_routes: 使用真实 ClawdbotClient，地址=%s",
            clawdbot_base_url,
            extra={"request_id": request_id},
        )

    # ── 推送 AGENT_INIT 事件（路由层元信息）────────────────────────────
    yield _sse({
        "phase": "AGENT_INIT",
        "request_id": request_id,
        "session_id": session_id,
        "goal": goal,
        "max_rounds": max_rounds,
        "round_timeout_sec": round_timeout_sec,
        "total_timeout_sec": total_timeout_sec,
        "image_upload_consent": image_upload_consent,
        "use_mock": use_mock,
        "clawdbot_base_url": clawdbot_base_url if not use_mock else "mock://",
        "llm_available": bool(llm_runtime.get("available", False)),
    })

    # ── 调用 Agent Loop，透传所有 SSE 事件 ──────────────────────────────
    try:
        async for sse_line in run_agent_loop(
            request_id=request_id,
            session_id=session_id,
            goal=goal,
            llm_runtime=llm_runtime,
            clawdbot_client=clawdbot_client,
            max_rounds=max_rounds,
            round_timeout_sec=round_timeout_sec,
            total_timeout_sec=total_timeout_sec,
            image_upload_allowed=image_upload_consent,
            use_mock=False,  # 客户端已在上面构建，不再重复 mock
            ledger=ledger,
        ):
            yield sse_line
    except Exception as exc:
        _logger.error(
            "agent_routes: Agent Loop 异常: %s",
            exc,
            extra={"request_id": request_id},
        )
        yield _sse({
            "phase": "LOOP_DONE",
            "request_id": request_id,
            "goal": goal,
            "goal_achieved": False,
            "final_status": f"ROUTE_ERROR:{type(exc).__name__}",
            "total_rounds": 0,
            "total_latency_ms": 0,
            "round_summaries": [],
            "error": str(exc),
        })


# ---------------------------------------------------------------------------
# POST /v1/agent/run — 启动 Agent Loop
# ---------------------------------------------------------------------------

@router.post("/run")
async def agent_run(body: AgentRunRequest, request: Request) -> StreamingResponse:
    """启动 Agent Loop，返回 SSE 流式进度。

    SSE 事件序列：
      AGENT_INIT      → 路由层初始化信息
      LOOP_START      → Agent Loop 开始
      ROUND_START     → 每轮开始
      SCREENSHOT_DONE → 截图完成
      PLAN_GENERATED  → ActionPlan 生成
      EXECUTE_DONE    → ClawdBot 执行完毕
      ROUND_DONE      → 每轮结束
      LOOP_DONE       → 循环结束（含最终状态）
    """
    request_id = new_request_id()
    session_id = str(body.session_id or request_id).strip() or request_id
    clawdbot_base_url = _get_clawdbot_base_url(body.clawdbot_base_url)

    _logger.info(
        "agent_run: goal=%r session_id=%s max_rounds=%d use_mock=%s",
        body.goal[:80],
        session_id,
        body.max_rounds,
        body.use_mock,
        extra={"request_id": request_id},
    )

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
            clawdbot_base_url=clawdbot_base_url,
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
    """返回 Agent Loop 当前配置信息（版本、默认参数、ClawdBot 地址等）。"""
    return AgentInfoResponse(
        version="v1",
        default_max_rounds=DEFAULT_MAX_ROUNDS,
        default_round_timeout_sec=DEFAULT_ROUND_TIMEOUT_SEC,
        default_total_timeout_sec=DEFAULT_TOTAL_TIMEOUT_SEC,
        clawdbot_base_url=_get_clawdbot_base_url(),
        mock_mode_available=True,
        timestamp=int(time.time() * 1000),
    )
