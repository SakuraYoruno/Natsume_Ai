"""Unified Message Gateway — 平台适配器抽象基类。

每个平台（微信/QQ/飞书/企微）实现一个 AbstractPlatformAdapter 子类，
负责：
  - Inbound：将平台原始消息转换为 UnifiedMessage
  - Outbound：将 UnifiedReply 通过平台 API 发送出去
  - Health：返回适配器健康状态

拟人化发送：
  所有适配器可通过 send_text_humanized() 使用公共拟人化模块，
  自动将长文本拆分为多段并模拟打字延迟。
  子类只需提供 _send_text_segment() 方法即可接入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from edge_client.gateway.models import Platform, UnifiedMessage, UnifiedReply


class AbstractPlatformAdapter(ABC):
    # 平台实现的最小边界：将已认证的外部业务形状映射为 UnifiedMessage，并
    # 尝试执行已经由 Dispatcher/Outbox 授权的 UnifiedReply；bool 只表示
    # 适配器本地确认，不能扩展为远端最终送达证明。
    """平台适配器抽象基类。

    子类必须实现：
      - platform_id: 返回平台枚举值
      - parse_inbound: 将平台原始 payload 转换为 UnifiedMessage
      - send_reply: 将 UnifiedReply 发送到平台

    可选实现（拟人化发送）：
      - _send_text_segment(segment, reply): 发送单段文字
        实现后可调用 send_text_humanized(reply) 获得分段拟人化发送能力
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

    def _send_text_segment(self, segment: str, reply: UnifiedReply) -> bool:
        # 分段发送是可选呈现能力；子类必须显式实现单段投递，防止基类悄悄
        # 回退为整条回复而破坏上层对部分发送和失败位置的判断。
        """发送单段文字到平台（子类覆盖以启用拟人化发送）。

        默认实现直接调用 send_reply（不分段）。
        子类应覆盖此方法，实现平台特定的单段文字发送逻辑。

        Args:
            segment: 单段文字内容
            reply: 原始 UnifiedReply（提供 conversation_id 等上下文）

        Returns:
            True 表示发送成功
        """
        raise NotImplementedError(
            f"{type(self).__name__} 未实现 _send_text_segment，"
            "无法使用 send_text_humanized"
        )

    def send_text_humanized(self, reply: UnifiedReply) -> bool:
        """使用公共拟人化模块分段发送文字消息。

        自动拆分长文本 → 逐段发送（模拟打字延迟 + 段间停顿）。
        子类只需实现 _send_text_segment() 即可获得此能力。

        Args:
            reply: 标准化回复

        Returns:
            True 表示全部发送成功
        """
        # 拟人化分段只是一种呈现策略，不改变 UnifiedReply 的平台/会话归属；任一
        # 分段失败会把整个发送返回为失败，调用 durable outbox 决定后续重试或 DLQ。
        from edge_client.gateway.humanize_sender import (
            humanize_send_sync,
            HumanizeSendConfig,
        )

        config = HumanizeSendConfig.from_env()
        result = humanize_send_sync(
            text=reply.content,
            send_fn=lambda seg: self._send_text_segment(seg, reply),
            config=config,
        )
        # 任一段失败即返回整体失败，让 durable outbox 保留可恢复的失败事实；
        # 不把已发送的前缀误记作整个回复已经完成。
        return result.success

    def health(self) -> dict[str, Any]:
        """返回适配器健康状态（子类可覆盖）。"""
        return {
            "platform": self.platform_id.value,
            "status": "ok",
        }
