from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor


@dataclass(slots=True)
class PeripheralAgent:
    bridge: Any | None = None
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.peripheral",
            family="peripheral",
            capabilities=("platform_adapter",),
        )
    )

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def publish_platform_connected(self, *, platform: str, detail: str | None = None) -> None:
        self._publish_event(
            event_type="platform_connected",
            target_agent="cortex_agent",
            payload={
                "platform": str(platform or ""),
                "detail": detail,
            },
        )

    def publish_platform_disconnected(self, *, platform: str, detail: str | None = None) -> None:
        self._publish_event(
            event_type="platform_disconnected",
            target_agent="cortex_agent",
            payload={
                "platform": str(platform or ""),
                "detail": detail,
            },
        )

    def _publish_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        target_agent: str | None = None,
    ) -> None:
        bridge = self.bridge
        if bridge is None or not hasattr(bridge, "publish_event"):
            return
        bridge.publish_event(
            event_type=event_type,
            source_agent=self.descriptor.agent_id,
            target_agent=target_agent,
            payload=dict(payload),
        )


__all__ = ["PeripheralAgent"]
