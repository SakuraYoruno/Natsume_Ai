"""Unified Message Gateway — FastAPI Webhook Server。

监听端口 19000，接收各平台推送的消息 Webhook，
经过适配器解析 → 标准化 → 路由到 Host Brain。

端点：
  POST /v1/gateway/{platform}/webhook   接收平台消息推送
  POST /v1/gateway/reply                接收 Host Brain 的回复指令
  GET  /v1/gateway/health               健康检查
  GET  /v1/gateway/stats                统计信息
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from edge_client.gateway.models import Platform, UnifiedReply
from edge_client.gateway.normalizer import MessageNormalizer
from edge_client.gateway.router import GatewayRouter
from edge_client.gateway.wechat_query_routes import router as _wechat_query_router

logger = logging.getLogger("Natsume.edge.gateway.server")

_GATEWAY_START_TIME = time.time()


def create_gateway_app(
    *,
    router: GatewayRouter,
    normalizer: MessageNormalizer | None = None,
    adapters: dict[str, Any] | None = None,
    outbound: Any | None = None,
) -> FastAPI:
    """创建 Gateway FastAPI 应用。

    Args:
        router: GatewayRouter 实例（必填）
        normalizer: MessageNormalizer 实例，None 时使用默认配置
        adapters: {platform_value: AbstractPlatformAdapter} 字典
        outbound: OutboundDispatcher 实例（Phase 2 接入）

    Returns:
        FastAPI 应用实例
    """
    _normalizer = normalizer or MessageNormalizer()
    _adapters: dict[str, Any] = adapters or {}
    _outbound = outbound

    app = FastAPI(
        title="Natsume Unified Message Gateway",
        description="外部消息统一网关 — 将微信/QQ/飞书/企微消息路由到 Host Brain",
        version="0.1.0",
    )

    # Phase 2: WeChat query routes (GET/POST /v1/gateway/wechat/query)
    app.include_router(_wechat_query_router)

    # ------------------------------------------------------------------
    # POST /v1/gateway/{platform}/webhook — 接收平台消息推送
    # ------------------------------------------------------------------

    @app.post("/v1/gateway/{platform}/webhook")
    async def platform_webhook(platform: str, request: Request) -> JSONResponse:
        """接收平台消息推送 Webhook。"""
        # 1. 解析请求体
        try:
            raw_payload: dict[str, Any] = await request.json()
        except Exception as exc:
            logger.warning("Gateway webhook: JSON 解析失败 platform=%s: %s", platform, exc)
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

        # 2. 查找适配器
        adapter = _adapters.get(platform)
        if adapter is None:
            logger.warning("Gateway webhook: 未找到适配器 platform=%s", platform)
            raise HTTPException(
                status_code=404,
                detail=f"No adapter registered for platform: {platform}",
            )

        # 3. 适配器解析
        try:
            msg = adapter.parse_inbound(raw_payload)
        except Exception as exc:
            logger.error("Gateway webhook: 适配器解析异常 platform=%s: %s", platform, exc)
            raise HTTPException(status_code=500, detail=f"Adapter parse error: {exc}")

        if msg is None:
            # 适配器认为此消息不需要处理（系统通知等）
            return JSONResponse({"status": "ignored"})

        # 4. 标准化（去重 + 脱敏 + 校验）
        normalized = _normalizer.normalize(msg)
        if normalized is None:
            return JSONResponse({"status": "filtered"})

        # 5. 路由到 Host Brain
        result = router.route(normalized)

        return JSONResponse({
            "status": "accepted" if result.success else "cached",
            "msg_id": msg.msg_id,
            "platform": platform,
        })

    # ------------------------------------------------------------------
    # POST /v1/gateway/reply — 接收 Host Brain 的回复指令
    # ------------------------------------------------------------------

    @app.post("/v1/gateway/reply")
    async def receive_reply(request: Request) -> JSONResponse:
        """接收 Host Brain 的回复指令，分发到对应平台。"""
        if _outbound is None:
            raise HTTPException(
                status_code=503,
                detail="OutboundDispatcher not configured (Phase 2)",
            )
        try:
            payload: dict[str, Any] = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

        try:
            platform = Platform(payload.get("platform", ""))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown platform: {payload.get('platform')}",
            )

        from edge_client.gateway.models import MsgType
        reply = UnifiedReply(
            conversation_id=str(payload.get("conversation_id", "")),
            platform=platform,
            msg_type=MsgType(payload.get("msg_type", "text")),
            content=str(payload.get("content", "")),
            media_path=payload.get("media_path"),
            reply_to_msg_id=payload.get("reply_to_msg_id"),
        )

        wait_result = bool(payload.get("wait_result", True))
        timeout_ms_raw = payload.get("timeout_ms", 22000)
        try:
            timeout_ms = max(1000, int(timeout_ms_raw))
        except (TypeError, ValueError):
            timeout_ms = 22000

        async def _dispatch_reply() -> bool:
            return await asyncio.to_thread(_outbound.dispatch, reply)

        if not wait_result:
            asyncio.create_task(_dispatch_reply())
            return JSONResponse({
                "status": "queued",
                "conversation_id": reply.conversation_id,
                "platform": platform.value,
            })

        try:
            success = await asyncio.wait_for(
                _dispatch_reply(),
                timeout=float(timeout_ms) / 1000.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Gateway reply timeout platform=%s conversation=%s timeout_ms=%d",
                platform.value,
                reply.conversation_id,
                timeout_ms,
            )
            return JSONResponse({
                "status": "failed",
                "conversation_id": reply.conversation_id,
                "platform": platform.value,
                "detail": f"send timeout after {timeout_ms}ms",
            })
        except Exception as exc:
            logger.error(
                "Gateway reply dispatch error platform=%s conversation=%s err=%s",
                platform.value,
                reply.conversation_id,
                exc,
            )
            return JSONResponse({
                "status": "failed",
                "conversation_id": reply.conversation_id,
                "platform": platform.value,
                "detail": str(exc),
            })
        return JSONResponse({
            "status": "sent" if success else "failed",
            "conversation_id": reply.conversation_id,
            "platform": platform.value,
        })

    # ------------------------------------------------------------------
    # GET /v1/gateway/health — 健康检查
    # ------------------------------------------------------------------

    @app.get("/v1/gateway/health")
    async def health() -> JSONResponse:
        """Gateway 健康检查。"""
        adapter_health = {}
        for platform_val, adapter in _adapters.items():
            try:
                adapter_health[platform_val] = adapter.health()
            except Exception as exc:
                adapter_health[platform_val] = {"status": "error", "detail": str(exc)}

        return JSONResponse({
            "status": "ok",
            "uptime_s": round(time.time() - _GATEWAY_START_TIME, 1),
            "adapters": adapter_health,
            "outbound_ready": _outbound is not None,
        })

    # ------------------------------------------------------------------
    # GET /v1/gateway/stats — 统计信息
    # ------------------------------------------------------------------

    @app.get("/v1/gateway/stats")
    async def stats() -> JSONResponse:
        """Gateway 统计信息。"""
        return JSONResponse({
            "normalizer": _normalizer.stats(),
            "router": router.stats(),
        })

    return app
