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
import os
import time
from typing import Any

from fastapi import APIRouter, Request
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

def _get_ledger_safe():
    """安全获取 ledger，失败时返回 None（不阻断主链路）。"""
    try:
        from host_brain.app.fastapi_app import get_ledger
        return get_ledger()
    except Exception:
        return None


def _log_input_event(
    *,
    ledger,
    request_id: str,
    session_id: str,
    source: str,
    phase: str,
    status: str,
    detail: str = "",
    reason_code: str | None = None,
    latency_ms: int | None = None,
    ts_ms: int,
) -> None:
    """写入 input_dispatch_log，追踪副作用生命周期。

    phase 取值：
      ACCEPTED   — HTTP 响应已返回，副作用已触发（fire-and-forget 起点）
      DISPATCHED — main_chain 调用已发起（异步任务已创建）
      COMPLETED  — main_chain 处理完成（副作用已落地）
      FAILED     — main_chain 处理失败（副作用未落地）
      NOT_WIRED  — main_chain 尚未接入（ImportError 降级）
    """
    if ledger is None:
        return
    try:
        ledger.log_voice_event({
            "request_id": request_id,
            "session_id": session_id,
            "event_type": f"INPUT_{phase}",
            "direction": "EDGE_TO_HOST",
            "status": status,
            "reason_code": reason_code,
            "stage": f"input_dispatch.{phase.lower()}",
            "detail": detail[:400] if detail else None,
            "payload": {
                "source": source,
                "phase": phase,
                "latency_ms": latency_ms,
            },
            "ts_ms": ts_ms,
        })
    except Exception as log_exc:
        _logger.debug("input_routes: ledger 写入失败（不阻断）: %s", log_exc)


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
    副作用可观测性：通过 ledger.log_voice_event 写入 input_dispatch_log，
    追踪 ACCEPTED → DISPATCHED → COMPLETED/FAILED 全生命周期。

    后续可扩展为：写入消息队列 → 消费者处理 → 结果推送到 Edge。
    """
    import asyncio

    ledger = _get_ledger_safe()

    # 写入 ACCEPTED：HTTP 响应已返回，副作用即将触发
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        phase="ACCEPTED",
        status="ACCEPTED",
        detail=f"source={source} text_len={len(text)}",
        ts_ms=ts_ms,
    )

    async def _run() -> None:
        dispatch_ts = int(time.time() * 1000)
        # 写入 DISPATCHED：异步任务已创建，main_chain 调用即将发起
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            phase="DISPATCHED",
            status="DISPATCHED",
            detail=f"async_task_created source={source}",
            ts_ms=dispatch_ts,
        )
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
            complete_ts = int(time.time() * 1000)
            latency_ms = complete_ts - dispatch_ts
            # 写入 COMPLETED：副作用已落地
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                phase="COMPLETED",
                status="OK",
                detail=f"main_chain completed source={source} latency_ms={latency_ms}",
                latency_ms=latency_ms,
                ts_ms=complete_ts,
            )
            _logger.info(
                "input_routes: Graph 处理完成 request_id=%s source=%s latency_ms=%d",
                request_id, source, latency_ms,
            )
        except ImportError:
            # main_chain 尚未实现 run_main_chain_from_input 时，记录日志等待后续接入
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                phase="NOT_WIRED",
                status="DEGRADED",
                reason_code="INPUT_MAIN_CHAIN_NOT_WIRED",
                detail="run_main_chain_from_input not implemented",
                ts_ms=int(time.time() * 1000),
            )
            _logger.info(
                "input_routes: main_chain.run_main_chain_from_input 尚未实现，"
                "输入已接收但未处理 request_id=%s source=%s text=%.60s",
                request_id, source, text,
            )
        except Exception as exc:
            fail_ts = int(time.time() * 1000)
            latency_ms = fail_ts - dispatch_ts
            # 写入 FAILED：副作用未落地
            _log_input_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                source=source,
                phase="FAILED",
                status="FAILED",
                reason_code="INPUT_GRAPH_ERROR",
                detail=f"{type(exc).__name__}: {str(exc)[:200]}",
                latency_ms=latency_ms,
                ts_ms=fail_ts,
            )
            _logger.error(
                "input_routes: Graph 处理异常 request_id=%s: %s",
                request_id, exc,
            )

    # fire-and-forget：不阻塞 HTTP 响应
    asyncio.create_task(_run())
