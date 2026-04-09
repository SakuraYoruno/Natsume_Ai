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
