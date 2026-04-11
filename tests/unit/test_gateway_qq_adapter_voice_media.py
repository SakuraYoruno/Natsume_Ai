from __future__ import annotations

from edge_client.gateway.adapters.qq_adapter import QQAdapter
from edge_client.gateway.models import MsgType


def test_qq_adapter_parse_inbound_extracts_voice_media_url_and_placeholder_content() -> None:
    adapter = QQAdapter("http://127.0.0.1:3000", self_id="230749497")

    message = adapter.parse_inbound(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 101,
            "user_id": "10001",
            "self_id": "230749497",
            "time": 1711111111,
            "raw_message": "",
            "message": [
                {"type": "record", "data": {"file": "D:/runtime/edge/voice_001.silk"}},
            ],
        }
    )

    assert message is not None
    assert message.msg_type == MsgType.VOICE
    assert message.media_url == "D:/runtime/edge/voice_001.silk"
    assert message.content == "[语音消息]"


def test_qq_adapter_parse_inbound_prefers_voice_url_when_file_absent() -> None:
    adapter = QQAdapter("http://127.0.0.1:3000", self_id="230749497")

    message = adapter.parse_inbound(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 102,
            "user_id": "10002",
            "group_id": "8899",
            "self_id": "230749497",
            "time": 1711112222,
            "raw_message": "",
            "message": [
                {"type": "record", "data": {"url": "http://cdn.example.com/v/102.silk"}},
            ],
        }
    )

    assert message is not None
    assert message.msg_type == MsgType.VOICE
    assert message.media_url == "http://cdn.example.com/v/102.silk"
    assert message.content == "[语音消息]"
