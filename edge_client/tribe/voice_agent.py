from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor


@dataclass(slots=True)
class VoiceAgent:
    bridge: Any | None = None
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.voice",
            family="voice",
            capabilities=("voice_pipeline",),
        )
    )

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def publish_pipeline_started(
        self,
        *,
        request_id: str,
        session_id: str,
        ts_ms: int,
        status: str,
        reason_code: str | None,
        stage: str,
    ) -> None:
        self._publish_event(
            event_type="voice_pipeline_started",
            target_agent="ans_agent",
            payload={
                "request_id": str(request_id or ""),
                "session_id": str(session_id or ""),
                "ts_ms": int(ts_ms),
                "status": str(status or ""),
                "reason_code": reason_code,
                "stage": str(stage or ""),
            },
        )

    def publish_pipeline_ended(
        self,
        *,
        request_id: str,
        session_id: str,
        ts_ms: int,
        status: str,
        reason_code: str | None,
        stage: str,
    ) -> None:
        self._publish_event(
            event_type="voice_pipeline_ended",
            target_agent="ans_agent",
            payload={
                "request_id": str(request_id or ""),
                "session_id": str(session_id or ""),
                "ts_ms": int(ts_ms),
                "status": str(status or ""),
                "reason_code": reason_code,
                "stage": str(stage or ""),
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


__all__ = ["VoiceAgent"]
