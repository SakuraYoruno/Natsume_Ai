from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor


@dataclass(slots=True)
class ChannelAgent:
    bridge: Any | None = None
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.channel",
            family="channel",
            capabilities=("message_ingress", "gateway_health"),
        )
    )
    # T-128：门控状态，由 CortexAgent 下行指令控制
    _gate_open: bool = field(default=True, init=False, repr=False)

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def on_message_received(
        self,
        *,
        platform: str,
        conversation_id: str,
        msg_id: str,
        msg_type: str,
        is_group: bool,
        ts_ms: int,
        routed_success: bool,
        cached: bool,
    ) -> None:
        self._publish_event(
            event_type="channel_message_received",
            target_agent="cortex_agent",
            payload={
                "platform": platform,
                "conversation_id": conversation_id,
                "msg_id": msg_id,
                "msg_type": msg_type,
                "is_group": bool(is_group),
                "ts_ms": int(ts_ms),
                "routed_success": bool(routed_success),
                "cached": bool(cached),
            },
        )

    def on_gateway_degraded(
        self,
        *,
        platform: str,
        conversation_id: str,
        msg_id: str,
        reason_code: str | None,
        detail: str | None,
        cached: bool,
    ) -> None:
        self._publish_event(
            event_type="gateway_degraded",
            source_agent="edge.gateway",
            target_agent="ans_agent",
            payload={
                "platform": platform,
                "conversation_id": conversation_id,
                "msg_id": msg_id,
                "reason_code": reason_code,
                "detail": detail,
                "cached": bool(cached),
            },
        )

    def on_gate_control_command(
        self,
        *,
        gate_open: bool,
        reason: str | None = None,
        platform: str | None = None,
        ts_ms: int = 0,
    ) -> dict[str, Any]:
        """处理来自 CortexAgent 的门控指令（下行）。

        T-128：CortexAgent 在平台连接/断线或进入谨慎模式时，
        通过 EdgeCommandPusher 推送门控指令到 edge_client。
        ChannelAgent 缓存门控状态，供 gateway/router.py 查询决策。

        Args:
            gate_open: True 表示开放消息路由，False 表示暂停路由
            reason: 门控原因（调试用）
            platform: 指定平台（None 表示全局门控）
            ts_ms: 指令时间戳
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.channel_agent")
        prev = self._gate_open
        self._gate_open = bool(gate_open)
        _log.info(
            "ChannelAgent 门控状态变更: %s → %s reason=%s platform=%s ts_ms=%d",
            prev, self._gate_open, reason, platform, int(ts_ms or 0),
        )
        return {
            "accepted": True,
            "gate_open": self._gate_open,
            "prev_gate_open": prev,
            "reason": reason,
            "platform": platform,
            "ts_ms": int(ts_ms or 0),
        }

    @property
    def gate_open(self) -> bool:
        """当前门控状态：True 表示允许消息路由，False 表示暂停。"""
        return self._gate_open

    def _publish_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        target_agent: str | None = None,
        source_agent: str | None = None,
    ) -> None:
        bridge = self.bridge
        if bridge is None or not hasattr(bridge, "publish_event"):
            return
        bridge.publish_event(
            event_type=event_type,
            source_agent=source_agent or self.descriptor.agent_id,
            target_agent=target_agent,
            payload=dict(payload),
        )


__all__ = ["ChannelAgent"]
