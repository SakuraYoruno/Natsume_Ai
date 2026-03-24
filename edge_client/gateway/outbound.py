"""Unified Message Gateway — 回复分发器。

接收 Host Brain 的 UnifiedReply，根据平台路由到对应适配器的 send_reply 方法。
"""

from __future__ import annotations

import logging
from typing import Any

from edge_client.gateway.adapters.base import AbstractPlatformAdapter
from edge_client.gateway.models import Platform, UnifiedReply

logger = logging.getLogger("Natsume.edge.gateway.outbound")


class OutboundDispatcher:
    """回复分发器。

    维护 {Platform: AbstractPlatformAdapter} 映射，
    将 UnifiedReply 路由到对应平台的发送接口。

    Args:
        adapters: 平台适配器字典 {Platform: adapter}
    """

    def __init__(self, adapters: dict[Platform, AbstractPlatformAdapter] | None = None) -> None:
        self._adapters: dict[Platform, AbstractPlatformAdapter] = adapters or {}
        self._stats: dict[str, int] = {
            "dispatched": 0,
            "failed": 0,
            "no_adapter": 0,
        }

    def register(self, adapter: AbstractPlatformAdapter) -> None:
        """注册平台适配器。"""
        self._adapters[adapter.platform_id] = adapter
        logger.info("OutboundDispatcher: 注册适配器 platform=%s", adapter.platform_id.value)

    def dispatch(self, reply: UnifiedReply) -> bool:
        """将回复分发到对应平台。

        Args:
            reply: 标准化回复

        Returns:
            True 表示发送成功
        """
        adapter = self._adapters.get(reply.platform)
        if adapter is None:
            logger.warning(
                "OutboundDispatcher: 未找到适配器 platform=%s", reply.platform.value
            )
            self._stats["no_adapter"] += 1
            return False

        try:
            success = adapter.send_reply(reply)
            if success:
                self._stats["dispatched"] += 1
            else:
                self._stats["failed"] += 1
            return success
        except Exception as exc:
            logger.error(
                "OutboundDispatcher: 分发异常 platform=%s conversation=%s: %s",
                reply.platform.value, reply.conversation_id, exc,
            )
            self._stats["failed"] += 1
            return False

    def stats(self) -> dict[str, Any]:
        """返回分发统计快照。"""
        return dict(self._stats)
