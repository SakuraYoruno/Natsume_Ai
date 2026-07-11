from __future__ import annotations

from edge_client.channels.input_reporter import InputReportResult, InputSourceType
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage
from edge_client.gateway.router import GatewayRouter


class _StubReporter:
    def __init__(self) -> None:
        self.last_payload: dict | None = None
        self.last_source: InputSourceType | None = None

    def report_gateway_payload(self, payload):  # noqa: ANN001
        self.last_payload = dict(payload)
        self.last_source = InputSourceType.GATEWAY
        return InputReportResult(
            success=True,
            source=InputSourceType.GATEWAY,
            reason_code=None,
            detail="ok",
            cached=False,
        )


class _StubVoiceAsr:
    def __init__(self, result: dict | None) -> None:
        self._result = result
        self.called = False

    def transcribe(self, _msg: UnifiedMessage) -> dict | None:
        self.called = True
        return self._result


def _voice_msg() -> UnifiedMessage:
    return UnifiedMessage(
        msg_id="gw_voice_1",
        platform=Platform.QQ,
        from_id="10001",
        to_id="20001",
        conversation_id="qq_dm_10001",
        msg_type=MsgType.VOICE,
        content="[语音消息]",
        ts_ms=1234567890,
        media_url="D:/runtime/voice_1.silk",
        raw_extra={"from_gateway": True},
    )


def test_gateway_router_applies_voice_asr_transcript_into_payload() -> None:
    reporter = _StubReporter()
    voice_asr = _StubVoiceAsr(
        {
            "text": "你好 夏目",
            "confidence": 0.93,
            "language": "zh",
            "media_ref": "D:/runtime/voice_1.silk",
            "segments": 2,
        }
    )
    router = GatewayRouter(reporter=reporter, voice_asr=voice_asr)

    result = router.route(_voice_msg())

    assert result.success is True
    assert voice_asr.called is True
    assert reporter.last_source == InputSourceType.GATEWAY
    assert reporter.last_payload is not None
    assert reporter.last_payload["content"] == "你好 夏目"
    assert reporter.last_payload["raw_extra"]["from_gateway"] is True
    assert reporter.last_payload["raw_extra"]["gateway_voice_asr"]["text"] == "你好 夏目"
    assert reporter.last_payload["raw_extra"]["gateway_voice_asr"]["confidence"] == 0.93


def test_gateway_router_keeps_original_content_when_voice_asr_returns_none() -> None:
    reporter = _StubReporter()
    voice_asr = _StubVoiceAsr(None)
    router = GatewayRouter(reporter=reporter, voice_asr=voice_asr)

    result = router.route(_voice_msg())

    assert result.success is True
    assert voice_asr.called is True
    assert reporter.last_payload is not None
    assert reporter.last_payload["content"] == "[语音消息]"
    assert "gateway_voice_asr" not in reporter.last_payload["raw_extra"]
