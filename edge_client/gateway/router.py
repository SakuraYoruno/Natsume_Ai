"""Unified Message Gateway — 消息路由器。

将标准化后的 UnifiedMessage 路由到 Host Brain，
复用现有 EdgeInputReporter 的 HMAC 签名、失败缓存、后台重试等能力。

新增 GATEWAY 来源类型，在 payload 中通过 source="GATEWAY" 区分。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from edge_client.channels.input_reporter import EdgeInputReporter, InputReportResult
from edge_client.gateway.models import UnifiedMessage

logger = logging.getLogger("Natsume.edge.gateway.router")


class GatewayRouter:
    """将 UnifiedMessage 路由到 Host Brain。

    内部复用 EdgeInputReporter 的 HTTP POST 通道（/v1/host/input），
    通过 source="GATEWAY" 字段让 Host 侧区分来源。

    Args:
        reporter: EdgeInputReporter 实例（由外部注入，避免重复创建连接）
    """

    def __init__(self, reporter: EdgeInputReporter) -> None:
        self._reporter = reporter
        self._stats: dict[str, int] = {
            "routed": 0,
            "failed": 0,
            "cached": 0,
        }

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
        return result

    def stats(self) -> dict[str, Any]:
        """返回路由统计快照。"""
        return dict(self._stats)
