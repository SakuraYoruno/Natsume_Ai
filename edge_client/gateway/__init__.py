"""Unified Message Gateway — 外部消息统一网关。

将微信、QQ、飞书、企微等外部平台的消息统一转换为 UnifiedMessage，
路由到 Host Brain，并将回复分发回对应平台。

端口：19000（独立于 Edge RPC Gateway :17800）
"""

from edge_client.gateway.models import (
    MsgType,
    Platform,
    UnifiedMessage,
    UnifiedReply,
)

__all__ = [
    "Platform",
    "MsgType",
    "UnifiedMessage",
    "UnifiedReply",
]
