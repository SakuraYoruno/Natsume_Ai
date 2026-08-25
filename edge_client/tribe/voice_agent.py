from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor
from edge_client.tribe.event_publication import publish_tribe_observation


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

    _publish_event = publish_tribe_observation

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
        # started 表示 Edge 已进入语音链路阶段，不表示合成或扬声器副作用已经发生；
        # request/session 只用于把后续终态与同一业务链关联起来。
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
        # ended 是一次本地管线终态的观察投影，status/reason 保留失败原因；Host 不应
        # 仅根据此事件推断远端平台、VTS 或音频设备都已完成。
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

__all__ = ["VoiceAgent"]
