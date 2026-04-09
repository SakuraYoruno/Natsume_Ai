"""Unified Message Gateway — 消息路由器。

将标准化后的 UnifiedMessage 路由到 Host Brain，
复用现有 EdgeInputReporter 的 HMAC 签名、失败缓存、后台重试等能力。

新增 GATEWAY 来源类型，在 payload 中通过 source="GATEWAY" 区分。
"""

from __future__ import annotations

import logging
from typing import Any

from edge_client.channels.input_reporter import EdgeInputReporter, InputReportResult
from edge_client.gateway.models import UnifiedMessage
from edge_client.tribe.channel_agent import ChannelAgent

logger = logging.getLogger("Natsume.edge.gateway.router")


class GatewayRouter:
    """将 UnifiedMessage 路由到 Host Brain。

    内部复用 EdgeInputReporter 的 HTTP POST 通道（/v1/host/input），
    通过 source="GATEWAY" 字段让 Host 侧区分来源。

    Args:
        reporter: EdgeInputReporter 实例（由外部注入，避免重复创建连接）
    """

    def __init__(self, reporter: EdgeInputReporter, tribe_bridge: Any | None = None) -> None:
        self._reporter = reporter
        self._tribe_bridge = tribe_bridge
        self._channel_agent = ChannelAgent(bridge=tribe_bridge)
        self._stats: dict[str, int] = {
            "routed": 0,
            "failed": 0,
            "cached": 0,
        }

    def set_tribe_bridge(self, bridge: Any | None) -> None:
        self._tribe_bridge = bridge
        self._channel_agent.set_bridge(bridge)

    def route(self, msg: UnifiedMessage) -> InputReportResult:
        """将消息路由到 Host Brain。

        Args:
            msg: 已标准化的 UnifiedMessage

        Returns:
            InputReportResult（成功/失败/已缓存）
        """
        payload = msg.as_report_payload()

        from edge_client.channels.input_reporter import InputSourceType
        result = self._reporter._send(payload, InputSourceType.GATEWAY)

        if result.success:
            self._stats["routed"] += 1
        elif result.cached:
            self._stats["cached"] += 1
        else:
            self._stats["failed"] += 1

        logger.debug(
            "GatewayRouter: platform=%s msg_id=%s → %s",
            msg.platform.value, msg.msg_id,
            "ok" if result.success else ("cached" if result.cached else "failed"),
        )
        self._channel_agent.on_message_received(
            platform=msg.platform.value,
            conversation_id=msg.conversation_id,
            msg_id=msg.msg_id,
            msg_type=msg.msg_type.value,
            is_group=bool(msg.is_group),
            ts_ms=int(msg.ts_ms),
            routed_success=bool(result.success),
            cached=bool(result.cached),
        )
        if (not result.success) or bool(result.cached):
            self._channel_agent.on_gateway_degraded(
                platform=msg.platform.value,
                conversation_id=msg.conversation_id,
                msg_id=msg.msg_id,
                reason_code=result.reason_code,
                detail=result.detail,
                cached=bool(result.cached),
            )
        return result

    def stats(self) -> dict[str, Any]:
        """返回路由统计快照。"""
        return dict(self._stats)
