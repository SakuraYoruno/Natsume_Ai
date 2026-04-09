from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor


@dataclass(slots=True)
class SomaticAgent:
    bridge: Any | None = None
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.somatic",
            family="somatic",
            capabilities=("body_sense", "emotion_inference"),
        )
    )

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def publish_body_sense_updated(
        self,
        *,
        request_id: str,
        session_id: str,
        seq: int | None,
        ts_ms: int,
        resource_metrics: dict[str, Any],
        upstream_status: str,
        upstream_reason_code: str | None,
    ) -> None:
        self._publish_event(
            event_type="body_sense_updated",
            target_agent="limbic_agent",
            payload={
                "request_id": str(request_id or ""),
                "session_id": str(session_id or ""),
                "seq": seq,
                "ts_ms": int(ts_ms),
                "resource_metrics": dict(resource_metrics or {}),
                "upstream_status": str(upstream_status or ""),
                "upstream_reason_code": upstream_reason_code,
            },
        )

    def publish_emotion_inference_updated(
        self,
        *,
        request_id: str,
        session_id: str,
        seq: int | None,
        emotion_tag: str,
        emotion_intensity: float | None,
        mood_state: str | None,
        emotion_source: str | None,
        emotion_terms: list[str],
    ) -> None:
        payload: dict[str, Any] = {
            "request_id": str(request_id or ""),
            "session_id": str(session_id or ""),
            "seq": seq,
            "emotion_tag": str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
            "emotion_intensity": (
                max(0.0, min(1.0, float(emotion_intensity)))
                if isinstance(emotion_intensity, (int, float))
                else None
            ),
            "mood_state": str(mood_state).strip().lower() if mood_state else None,
            "emotion_source": str(emotion_source).strip() if emotion_source else None,
            "emotion_terms": list(emotion_terms or []),
        }
        self._publish_event(
            event_type="emotion_inference_updated",
            target_agent="limbic_agent",
            payload=payload,
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


__all__ = ["SomaticAgent"]
