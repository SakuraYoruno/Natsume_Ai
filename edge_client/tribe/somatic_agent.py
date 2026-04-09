from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor

_log = logging.getLogger("Natsume.edge.tribe.somatic_agent")


@dataclass(slots=True)
class SomaticAgent:
    bridge: Any | None = None
    vts_driver: Any | None = None  # T-126/T-127：VTubeStudioDriver 实例，由 bootstrap 注入
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.somatic",
            family="somatic",
            capabilities=("body_sense", "emotion_inference"),
        )
    )

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def set_vts_driver(self, vts_driver: Any | None) -> None:
        """注入 VTubeStudioDriver，用于接收下行情绪指令后驱动表情。"""
        self.vts_driver = vts_driver

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def on_emotion_state_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理来自 LimbicAgent 的下行情绪状态指令，驱动 vts_driver 表情。

        T-126：Phase 6 下行链路核心方法。
        host_brain LimbicAgent 处理完 EMOTION_INFERENCE_UPDATED 后，
        通过 EdgeCommandPusher → /internal/tribe_command → 此方法，
        最终调用 vts_driver.set_expression() 驱动 VTubeStudio 表情。

        Args:
            payload: {
                "emotion_tag": str,       # 情绪标签，如 "JOY"/"SAD"/"NEUTRAL"
                "intensity": float|None,  # 情绪强度 0.0~1.0
                "mood_state": str|None,   # 心情状态，如 "upbeat"/"steady"
                "session_id": str|None,   # 会话 ID（调试用）
            }

        Returns:
            {"ok": bool, "reason": str|None, "emotion_tag": str}
        """
        try:
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            intensity = payload.get("intensity")
            intensity_f: float | None = (
                max(0.0, min(1.0, float(intensity)))
                if isinstance(intensity, (int, float))
                else None
            )
            session_id = str(payload.get("session_id") or "").strip() or None

            driver = self.vts_driver
            if driver is None:
                _log.debug(
                    "SomaticAgent.on_emotion_state_command: vts_driver 未注入，跳过表情驱动"
                    " emotion_tag=%s session=%s", emotion_tag, session_id,
                )
                return {"ok": False, "reason": "VTS_DRIVER_UNAVAILABLE", "emotion_tag": emotion_tag}

            # 调用 vts_driver.set_expression()，intensity 可选
            if intensity_f is not None:
                result = driver.set_expression(emotion_tag, intensity=intensity_f)
            else:
                result = driver.set_expression(emotion_tag)

            ok = bool(getattr(result, "success", True) if hasattr(result, "success") else True)
            _log.info(
                "SomaticAgent 下行情绪指令已执行: emotion_tag=%s intensity=%s ok=%s session=%s",
                emotion_tag, intensity_f, ok, session_id,
            )
            return {"ok": ok, "reason": None, "emotion_tag": emotion_tag}
        except Exception as exc:
            _log.warning("SomaticAgent.on_emotion_state_command 异常: %s", exc)
            return {"ok": False, "reason": str(exc), "emotion_tag": str(payload.get("emotion_tag") or "NEUTRAL")}

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
