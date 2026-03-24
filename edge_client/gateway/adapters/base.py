"""Unified Message Gateway — 平台适配器抽象基类。

每个平台（微信/QQ/飞书/企微）实现一个 AbstractPlatformAdapter 子类，
负责：
  - Inbound：将平台原始消息转换为 UnifiedMessage
  - Outbound：将 UnifiedReply 通过平台 API 发送出去
  - Health：返回适配器健康状态
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from edge_client.gateway.models import Platform, UnifiedMessage, UnifiedReply


class AbstractPlatformAdapter(ABC):
    """平台适配器抽象基类。

    子类必须实现：
      - platform_id: 返回平台枚举值
      - parse_inbound: 将平台原始 payload 转换为 UnifiedMessage
      - send_reply: 将 UnifiedReply 发送到平台
    """

    @property
    @abstractmethod
    def platform_id(self) -> Platform:
        """返回此适配器对应的平台枚举值。"""
        ...

    @abstractmethod
    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        """将平台原始 payload 转换为 UnifiedMessage。

        Args:
            raw_payload: 平台推送的原始消息字典

        Returns:
            UnifiedMessage，或 None（表示此消息不需要处理，如系统通知等）
        """
        ...

    @abstractmethod
    def send_reply(self, reply: UnifiedReply) -> bool:
        """将 UnifiedReply 发送到平台。

        Args:
            reply: 标准化回复

        Returns:
            True 表示发送成功，False 表示失败
        """
        ...

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态（子类可覆盖）。"""
        return {
            "platform": self.platform_id.value,
            "status": "ok",
        }
