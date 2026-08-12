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

    def _publish_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        target_agent: str | None = None,
    ) -> None:
        bridge = self.bridge
        # Bridge 不可用时静默降级为未上报，不重放或篡改语音状态；真实执行链仍由
        # VoicePipeline/SpeechQueue 的持有者收敛，避免观察通道反向控制设备。
        if bridge is None or not hasattr(bridge, "publish_event"):
            return
        bridge.publish_event(
            event_type=event_type,
            source_agent=self.descriptor.agent_id,
            target_agent=target_agent,
            payload=dict(payload),
        )


__all__ = ["VoiceAgent"]
