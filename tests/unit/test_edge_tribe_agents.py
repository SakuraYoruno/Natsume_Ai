from __future__ import annotations

from edge_client.tribe.channel_agent import ChannelAgent
from edge_client.tribe.peripheral_agent import PeripheralAgent
from edge_client.tribe.somatic_agent import SomaticAgent
from edge_client.tribe.voice_agent import VoiceAgent


class _Bridge:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish_event(self, **kwargs):  # noqa: ANN003
        self.events.append(dict(kwargs))
        return {"ok": True}


def test_channel_agent_emits_message_and_gateway_events() -> None:
    bridge = _Bridge()
    agent = ChannelAgent(bridge=bridge)

    agent.on_message_received(
        platform="wechat",
        conversation_id="conv_1",
        msg_id="msg_1",
        msg_type="text",
        is_group=False,
        ts_ms=123,
        routed_success=True,
        cached=False,
    )
    agent.on_gateway_degraded(
        platform="wechat",
        conversation_id="conv_1",
        msg_id="msg_1",
        reason_code="INPUT_REPORT_FAILED",
        detail="timeout",
        cached=True,
    )

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "channel_message_received" in event_types
    assert "gateway_degraded" in event_types


def test_somatic_agent_emits_body_and_emotion_events() -> None:
    bridge = _Bridge()
    agent = SomaticAgent(bridge=bridge)

    agent.publish_body_sense_updated(
        request_id="req_1",
        session_id="sess_1",
        seq=1,
        ts_ms=123,
        resource_metrics={"queue_depth": 1},
        upstream_status="ACCEPTED",
        upstream_reason_code=None,
    )
    agent.publish_emotion_inference_updated(
        request_id="req_1",
        session_id="sess_1",
        seq=1,
        emotion_tag="happy",
        emotion_intensity=0.8,
        mood_state="calm",
        emotion_source="edge_explicit",
        emotion_terms=["开心"],
        emotion_modality="explicit",
        emotion_confidence=1.0,
        emotion_inference_degraded=False,
        acoustic_emotion_available=False,
    )

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "body_sense_updated" in event_types
    assert "emotion_inference_updated" in event_types
    emotion_event = next(item for item in bridge.events if str(item.get("event_type")) == "emotion_inference_updated")
    payload = dict(emotion_event.get("payload") or {})
    assert payload["emotion_modality"] == "explicit"
    assert payload["emotion_confidence"] == 1.0
    assert payload["emotion_inference_degraded"] is False
    assert payload["acoustic_emotion_available"] is False


def test_voice_agent_emits_pipeline_events() -> None:
    bridge = _Bridge()
    agent = VoiceAgent(bridge=bridge)

    agent.publish_pipeline_started(
        request_id="req_voice",
        session_id="sess_voice",
        ts_ms=1000,
        status="ACCEPTED",
        reason_code=None,
        stage="S_VOICE_TTS_QUEUE",
    )
    agent.publish_pipeline_ended(
        request_id="req_voice",
        session_id="sess_voice",
        ts_ms=1001,
        status="ACCEPTED",
        reason_code=None,
        stage="S_VOICE_TTS_QUEUE",
    )

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "voice_pipeline_started" in event_types
    assert "voice_pipeline_ended" in event_types


def test_peripheral_agent_emits_platform_events() -> None:
    bridge = _Bridge()
    agent = PeripheralAgent(bridge=bridge)

    agent.publish_platform_connected(platform="wechat", detail="adapter online")
    agent.publish_platform_disconnected(platform="wechat", detail="adapter offline")

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "platform_connected" in event_types
    assert "platform_disconnected" in event_types
