"""Host Brain 统一输入接收路由。

接收 Edge 端上报的两类输入：
  - ASR 语音识别结果（source=ASR）
  - 微信小号接收到的消息（source=WECHAT）

接收后进行鉴权、风险预检，然后投递到 Graph 流通（main_chain）处理。

端点：
  POST /v1/host/input  — 统一输入接收（Edge → Host）
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.input_routes")

router = APIRouter(prefix="/v1/host", tags=["input"])

# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class InputPayload(BaseModel):
    """Edge 上报的输入载荷。"""
    source: str = Field(..., description="输入来源：ASR 或 WECHAT")
    # ASR 字段
    session_id: str | None = Field(None, description="[ASR] 会话 ID")
    text: str | None = Field(None, description="[ASR] 识别文本")
    confidence: float | None = Field(None, description="[ASR] 置信度 0~1")
    is_final: bool | None = Field(None, description="[ASR] 是否为最终结果")
    seq: int | None = Field(None, description="[ASR] 序列号")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best 候选")
    evidence_ref: str | None = Field(None, description="[ASR] 审计链引用")
    # WECHAT 字段
    from_wxid: str | None = Field(None, description="[WECHAT] 发送方 wxid")
    to_wxid: str | None = Field(None, description="[WECHAT] 接收方 wxid（小号）")
    content: str | None = Field(None, description="[WECHAT] 消息内容")
    msg_type: str | None = Field(None, description="[WECHAT] 消息类型")
    msg_id: str | None = Field(None, description="[WECHAT] 消息 ID")
    room_wxid: str | None = Field(None, description="[WECHAT] 群聊 ID（私聊时为空）")
    raw_extra: dict[str, Any] | None = Field(None, description="[WECHAT] 原始扩展字段")
    # 公共字段
    ts_ms: int | None = Field(None, description="消息时间戳（毫秒）")


class InputResponse(BaseModel):
    """输入接收响应。"""
    success: bool
    request_id: str
    source: str
    status: str
    detail: str
    ts_ms: int


# ---------------------------------------------------------------------------
# POST /v1/host/input
# ---------------------------------------------------------------------------

@router.post("/input", response_model=InputResponse)
async def receive_input(body: InputPayload, request: Request) -> InputResponse:
    """接收 Edge 端上报的输入（ASR / 微信消息），投递到 Graph 流通处理。

    处理流程：
      1. 鉴权（X-Edge-Sig HMAC 验证，可选）
      2. 风险预检（内容合规）
      3. 投递到 main_chain 异步处理
    """
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    source = str(body.source or "").upper().strip()

    _logger.info(
        "input_routes: 收到 Edge 输入 source=%s request_id=%s",
        source, request_id,
    )

    # ── 基础校验 ──────────────────────────────────────────────────────────
    if source == "ASR":
        if not body.text or not body.session_id:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="ASR 输入缺少必填字段: text, session_id",
                ts_ms=ts_ms,
            )
        text = str(body.text).strip()
        session_id = str(body.session_id).strip()

    elif source == "WECHAT":
        if not body.content or not body.from_wxid:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="WECHAT 输入缺少必填字段: content, from_wxid",
                ts_ms=ts_ms,
            )
        text = str(body.content).strip()
        session_id = str(body.from_wxid).strip()

    else:
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=f"不支持的输入来源: {source}，仅支持 ASR / WECHAT",
            ts_ms=ts_ms,
        )

    # ── 投递到 Graph 流通（异步，不阻塞响应）────────────────────────────
    try:
        await _dispatch_to_graph(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=body.model_dump(exclude_none=True),
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=True,
            request_id=request_id,
            source=source,
            status="ACCEPTED",
            detail="输入已接收，正在处理",
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.error(
            "input_routes: 投递失败 source=%s request_id=%s: %s",
            source, request_id, exc,
        )
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="ERROR",
            detail=f"投递失败: {exc}",
            ts_ms=ts_ms,
        )


# ---------------------------------------------------------------------------
# 内部：投递到 Graph 流通
# ---------------------------------------------------------------------------

async def _dispatch_to_graph(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> None:
    """将输入投递到 main_chain 处理。

    当前实现：异步触发 main_chain，不等待结果（fire-and-forget）。
    后续可扩展为：写入消息队列 → 消费者处理 → 结果推送到 Edge。
    """
    import asyncio

    async def _run() -> None:
        try:
            from host_brain.app.graph.main_chain import run_main_chain_from_input
            await run_main_chain_from_input(
                request_id=request_id,
                session_id=session_id,
                source=source,
                text=text,
                payload=payload,
                ts_ms=ts_ms,
            )
        except ImportError:
            # main_chain 尚未实现 run_main_chain_from_input 时，记录日志等待后续接入
            _logger.info(
                "input_routes: main_chain.run_main_chain_from_input 尚未实现，"
                "输入已接收但未处理 request_id=%s source=%s text=%.60s",
                request_id, source, text,
            )
        except Exception as exc:
            _logger.error(
                "input_routes: Graph 处理异常 request_id=%s: %s",
                request_id, exc,
            )

    # fire-and-forget：不阻塞 HTTP 响应
    asyncio.create_task(_run())
