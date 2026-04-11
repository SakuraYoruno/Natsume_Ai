from __future__ import annotations

from edge_client.gateway.adapters.wechat_adapter import WeChatAdapter
from edge_client.gateway.models import MsgType
from edge_client.services.wx_db_service import WxMessage


def test_wechat_adapter_parse_inbound_voice_uses_placeholder_and_media_url() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)

    message = adapter.parse_inbound(
        {
            "msg_id": "wx-msg-voice-1",
            "from_wxid": "wxid_alice",
            "to_wxid": "wxid_bot",
            "conversation_id": "wxid_alice",
            "msg_type": 34,
            "content": "",
            "media_path": "D:/runtime/edge/wx_voice_1.silk",
            "ts_ms": 1774453000123,
        }
    )

    assert message is not None
    assert message.msg_type == MsgType.VOICE
    assert message.content == "[语音消息]"
    assert message.media_url == "D:/runtime/edge/wx_voice_1.silk"


def test_wechat_adapter_convert_wx_message_voice_uses_placeholder_when_content_empty() -> None:
    adapter = WeChatAdapter(None, self_wxid=None)
    wx_message = WxMessage(
        msg_id="wx-db-voice-1",
        from_wxid="wxid_alice",
        to_wxid="wxid_bot",
        conversation_id="wxid_alice",
        content="",
        msg_type=34,
        ts_ms=1774453000555,
        is_sender=False,
        media_url="D:/runtime/edge/wx_voice_db_1.silk",
        raw={},
    )

    message = adapter.convert_wx_message(wx_message)

    assert message is not None
    assert message.msg_type == MsgType.VOICE
    assert message.content == "[语音消息]"
    assert message.media_url == "D:/runtime/edge/wx_voice_db_1.silk"
