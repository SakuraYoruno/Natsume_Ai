from __future__ import annotations

from edge_client.gateway.models import MsgType, Platform, UnifiedMessage
from edge_client.gateway.voice_asr import GatewayVoiceAsr


def _qq_voice_message(*, media_url: str | None = None, file_token: str = "token_001.amr") -> UnifiedMessage:
    return UnifiedMessage(
        msg_id="qq_voice_1",
        platform=Platform.QQ,
        from_id="10001",
        to_id="230749497",
        conversation_id="qq_dm_10001",
        msg_type=MsgType.VOICE,
        content="[语音消息]",
        ts_ms=1711114444000,
        media_url=media_url,
        raw_extra={
            "message": [
                {"type": "record", "data": {"file": file_token}},
            ]
        },
    )


def test_gateway_voice_asr_extracts_qq_record_file_token_from_raw_extra() -> None:
    asr = GatewayVoiceAsr()
    msg = _qq_voice_message(media_url=None, file_token="voice_token_777.amr")

    token = asr._extract_qq_record_file_token(msg, "")

    assert token == "voice_token_777.amr"


def test_gateway_voice_asr_resolve_media_for_decode_uses_qq_record_resolver(monkeypatch) -> None:
    asr = GatewayVoiceAsr()
    msg = _qq_voice_message(media_url="record_token_xxx")

    monkeypatch.setattr(asr, "_try_resolve_qq_record_to_local", lambda _msg, _media_ref: "D:/tmp/voice_1.wav")
    monkeypatch.setattr(asr, "_download_remote_media", lambda _url: None)

    resolved = asr._resolve_media_for_decode(msg, "record_token_xxx")

    assert resolved == "D:/tmp/voice_1.wav"
