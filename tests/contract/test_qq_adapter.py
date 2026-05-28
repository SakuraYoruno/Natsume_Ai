"""QQ 适配器（OneBot v11）— 单元测试。

覆盖范围：
  T-QQ-INBOUND-01~08  : parse_inbound 私聊/群聊/自过滤/@检测/notice/request/空消息
  T-QQ-OUTBOUND-01~07 : send_reply TEXT/IMAGE(file)/IMAGE(url)/IMAGE(base64)/VOICE/不支持类型
  T-QQ-HEALTH-01~02   : health 正常/异常
  T-QQ-UTIL-01~03     : 内部工具方法
"""

from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from edge_client.gateway.adapters.qq_adapter import QQAdapter
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_ob_message(
    *,
    message_type: str = "private",
    user_id: int = 789012,
    self_id: int = 123456,
    group_id: int | None = None,
    message_id: int = 999,
    raw_message: str = "你好",
    message: list[dict[str, Any]] | None = None,
    nickname: str = "张三",
    time_: int = 1700000000,
    post_type: str = "message",
) -> dict[str, Any]:
    """构造 OneBot v11 上报 payload。"""
    if message is None:
        message = [{"type": "text", "data": {"text": raw_message}}]
    payload: dict[str, Any] = {
        "post_type": post_type,
        "message_type": message_type,
        "time": time_,
        "self_id": self_id,
        "user_id": user_id,
        "message_id": message_id,
        "raw_message": raw_message,
        "message": message,
        "sender": {"user_id": user_id, "nickname": nickname},
    }
    if group_id is not None:
        payload["group_id"] = group_id
    return payload


def _adapter(self_id: str = "123456") -> QQAdapter:
    return QQAdapter("http://127.0.0.1:3000", self_id=self_id)


# ===========================================================================
# T-QQ-INBOUND: parse_inbound 测试
# ===========================================================================

class TestQQAdapterInbound(unittest.TestCase):
    """parse_inbound 系列测试。"""

    def test_private_message(self) -> None:
        """T-QQ-INBOUND-01: 私聊消息正常解析。"""
        adapter = _adapter()
        payload = _make_ob_message(message_type="private", user_id=789012)
        msg = adapter.parse_inbound(payload)

        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.platform, Platform.QQ)
        self.assertEqual(msg.from_id, "789012")
        self.assertEqual(msg.conversation_id, "qq_dm_789012")
        self.assertEqual(msg.msg_type, MsgType.TEXT)
        self.assertEqual(msg.content, "你好")
        self.assertFalse(msg.is_group)
        self.assertEqual(msg.sender_name, "张三")
        self.assertEqual(msg.ts_ms, 1700000000000)

    def test_group_message(self) -> None:
        """T-QQ-INBOUND-02: 群聊消息正常解析。"""
        adapter = _adapter()
        payload = _make_ob_message(
            message_type="group", user_id=789012, group_id=111222,
        )
        msg = adapter.parse_inbound(payload)

        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.conversation_id, "qq_group_111222")
        self.assertTrue(msg.is_group)

    def test_filter_self_message(self) -> None:
        """T-QQ-INBOUND-03: 过滤自己发出的消息。"""
        adapter = _adapter(self_id="789012")
        payload = _make_ob_message(user_id=789012)
        msg = adapter.parse_inbound(payload)
        self.assertIsNone(msg)

    def test_filter_self_id_from_payload(self) -> None:
        """T-QQ-INBOUND-04: 通过 payload self_id 过滤自己的消息。"""
        adapter = QQAdapter("http://127.0.0.1:3000", self_id=None)
        payload = _make_ob_message(user_id=123456, self_id=123456)
        msg = adapter.parse_inbound(payload)
        self.assertIsNone(msg)

    def test_at_self_detection(self) -> None:
        """T-QQ-INBOUND-05: 群消息中 @自己 被正确标记。"""
        adapter = _adapter()
        payload = _make_ob_message(
            message_type="group",
            user_id=789012,
            group_id=111222,
            raw_message="@Bot 你好",
            message=[
                {"type": "at", "data": {"qq": "123456"}},
                {"type": "text", "data": {"text": " 你好"}},
            ],
        )
        msg = adapter.parse_inbound(payload)

        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertTrue(msg.raw_extra.get("_at_self"))
        self.assertEqual(msg.content, "你好")

    def test_at_all_detection(self) -> None:
        """T-QQ-INBOUND-05b: @全体成员 也标记为 at_self。"""
        adapter = _adapter()
        payload = _make_ob_message(
            message_type="group",
            user_id=789012,
            group_id=111222,
            raw_message="@全体成员 公告",
            message=[
                {"type": "at", "data": {"qq": "all"}},
                {"type": "text", "data": {"text": " 公告"}},
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertTrue(msg.raw_extra.get("_at_self"))

    def test_image_message_type(self) -> None:
        """T-QQ-INBOUND-06: 图片消息类型识别。"""
        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            message=[
                {"type": "image", "data": {"url": "http://example.com/img.jpg"}},
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.msg_type, MsgType.IMAGE)
        self.assertEqual(len(msg.attachments), 1)
        self.assertEqual(msg.attachments[0].type, "image")

    def test_image_message_prefers_remote_url_over_file_token(self) -> None:
        """QQ 图片若同时带 file token 和 url，应优先保留可远程访问的 url。"""
        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            raw_message="[CQ:image,file=ABC.jpg,url=http://example.com/img.jpg]",
            message=[
                {"type": "image", "data": {"file": "ABC.jpg", "url": "http://example.com/img.jpg"}},
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.msg_type, MsgType.IMAGE)
        self.assertEqual(msg.media_url, "http://example.com/img.jpg")
        self.assertEqual(msg.attachments[0].remote_url, "http://example.com/img.jpg")

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.get")
    def test_image_message_resolves_onebot_get_image_url(self, mock_get: MagicMock) -> None:
        """QQ 图片若只有 file token，应通过 get_image 换出真正 url。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "ok",
            "retcode": 0,
            "data": {"url": "http://example.com/resolved.jpg"},
        }
        mock_get.return_value = mock_resp

        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            raw_message="[CQ:image,file=ABC.jpg]",
            message=[
                {"type": "image", "data": {"file": "ABC.jpg"}},
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.media_url, "http://example.com/resolved.jpg")
        self.assertEqual(msg.attachments[0].remote_url, "http://example.com/resolved.jpg")

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.get")
    def test_image_message_resolves_onebot_local_path_to_data_url(self, mock_get: MagicMock) -> None:
        """若 get_image 只返回本地路径，适配器应编码成 data url 供 host 多模态读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sticker.png"
            path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+tmfQAAAAASUVORK5CYII="))
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "status": "ok",
                "retcode": 0,
                "data": {"file": str(path)},
            }
            mock_get.return_value = mock_resp

            adapter = _adapter()
            payload = _make_ob_message(
                user_id=789012,
                raw_message="[CQ:image,file=ABC.png]",
                message=[
                    {"type": "image", "data": {"file": "ABC.png"}},
                ],
            )
            msg = adapter.parse_inbound(payload)
            assert msg is not None
            assert msg.media_url is not None
            self.assertTrue(msg.media_url.startswith("data:image/png;base64,"))
            self.assertEqual(msg.attachments[0].remote_url, msg.media_url)

    def test_image_segment_with_animation_summary_is_emoji_message(self) -> None:
        """NapCat 会把 QQ 动画表情上报成 image+summary；应按 emoji 社交输入处理。"""
        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            raw_message="[CQ:image,summary=&#91;动画表情&#93;,url=http://example.com/sticker.gif]",
            message=[
                {
                    "type": "image",
                    "data": {
                        "summary": "[动画表情]",
                        "url": "http://example.com/sticker.gif",
                    },
                },
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.msg_type, MsgType.EMOJI)
        self.assertEqual(msg.content, "[表情消息]")
        self.assertEqual(msg.media_url, "http://example.com/sticker.gif")
        self.assertEqual(len(msg.attachments), 1)
        self.assertEqual(msg.attachments[0].type, "emoji")
        self.assertEqual(msg.attachments[0].remote_url, "http://example.com/sticker.gif")
        self.assertEqual(msg.attachments[0].extra.get("qq_summary"), "[动画表情]")
        self.assertTrue(msg.attachments[0].extra.get("qq_is_expression"))

        payload_without_segment_summary = _make_ob_message(
            user_id=789012,
            raw_message="[CQ:image,summary=&#91;动画表情&#93;,url=http://example.com/sticker2.gif]",
            message=[
                {"type": "image", "data": {"url": "http://example.com/sticker2.gif"}},
            ],
        )
        msg_without_segment_summary = adapter.parse_inbound(payload_without_segment_summary)
        assert msg_without_segment_summary is not None
        self.assertEqual(msg_without_segment_summary.msg_type, MsgType.EMOJI)
        self.assertEqual(msg_without_segment_summary.attachments[0].type, "emoji")

    def test_mface_message_type(self) -> None:
        """QQ 自定义表情应识别为 emoji 附件。"""
        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            raw_message="[CQ:mface,summary=hello,url=http://example.com/sticker.png]",
            message=[
                {
                    "type": "mface",
                    "data": {
                        "summary": "hello",
                        "url": "http://example.com/sticker.png",
                    },
                },
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.msg_type, MsgType.EMOJI)
        self.assertEqual(len(msg.attachments), 1)
        self.assertEqual(msg.attachments[0].type, "emoji")
        self.assertEqual(msg.attachments[0].remote_url, "http://example.com/sticker.png")

    def test_face_message_type_uses_emoji_attachment(self) -> None:
        """QQ face 段即使没有远端 URL，也应落为 emoji 附件。"""
        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            raw_message="[CQ:face,id=14]",
            message=[
                {
                    "type": "face",
                    "data": {"id": "14"},
                },
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.msg_type, MsgType.EMOJI)
        self.assertEqual(len(msg.attachments), 1)
        self.assertEqual(msg.attachments[0].type, "emoji")

    def test_voice_message_type(self) -> None:
        """T-QQ-INBOUND-07: 语音消息类型识别。"""
        adapter = _adapter()
        payload = _make_ob_message(
            user_id=789012,
            message=[
                {"type": "record", "data": {"file": "voice.silk"}},
            ],
        )
        msg = adapter.parse_inbound(payload)
        assert msg is not None
        self.assertEqual(msg.msg_type, MsgType.VOICE)
        self.assertEqual(len(msg.attachments), 1)
        self.assertEqual(msg.attachments[0].type, "voice")

    def test_skip_non_message_post_type(self) -> None:
        """T-QQ-INBOUND-08: 非 message/notice/request 事件被跳过。"""
        adapter = _adapter()
        payload = {"post_type": "meta_event", "meta_event_type": "heartbeat"}
        msg = adapter.parse_inbound(payload)
        self.assertIsNone(msg)
        self.assertEqual(adapter._stats["inbound_skipped"], 1)


# ===========================================================================
# T-QQ-NOTICE/REQUEST: notice / request 事件测试
# ===========================================================================

class TestQQAdapterNoticeRequest(unittest.TestCase):
    """notice / request 事件处理测试。"""

    def test_group_recall_notice(self) -> None:
        """T-QQ-NOTICE-01: 群消息撤回事件被记录。"""
        adapter = _adapter()
        payload = {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": 111222,
            "user_id": 789012,
            "operator_id": 789012,
            "message_id": 999,
        }
        msg = adapter.parse_inbound(payload)
        self.assertIsNone(msg)
        self.assertEqual(adapter._stats["inbound_notice"], 1)

    def test_friend_request(self) -> None:
        """T-QQ-REQUEST-01: 加好友请求被记录。"""
        adapter = _adapter()
        payload = {
            "post_type": "request",
            "request_type": "friend",
            "user_id": 999888,
            "comment": "我是小明",
        }
        msg = adapter.parse_inbound(payload)
        self.assertIsNone(msg)
        self.assertEqual(adapter._stats["inbound_request"], 1)


# ===========================================================================
# T-QQ-OUTBOUND: send_reply 测试
# ===========================================================================

class TestQQAdapterOutbound(unittest.TestCase):
    """send_reply 系列测试。"""

    def _make_reply(
        self,
        *,
        conversation_id: str = "qq_dm_789012",
        msg_type: MsgType = MsgType.TEXT,
        content: str = "回复内容",
        media_path: str | None = None,
        media_data: bytes | None = None,
    ) -> UnifiedReply:
        return UnifiedReply(
            conversation_id=conversation_id,
            platform=Platform.QQ,
            msg_type=msg_type,
            content=content,
            media_path=media_path,
            media_data=media_data,
        )

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_text_private(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-01: 发送私聊文本消息。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply()
        result = adapter.send_reply(reply)

        self.assertTrue(result)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertIn("send_private_msg", call_args[0][0])
        payload = call_args[1]["json"]
        self.assertEqual(payload["user_id"], 789012)
        self.assertEqual(payload["message"][0]["type"], "text")

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_text_group(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-02: 发送群聊文本消息。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(conversation_id="qq_group_111222")
        result = adapter.send_reply(reply)

        self.assertTrue(result)
        call_args = mock_post.call_args
        self.assertIn("send_group_msg", call_args[0][0])
        self.assertEqual(call_args[1]["json"]["group_id"], 111222)

    @patch.dict(os.environ, {"EDGE_QQ_SHORT_TEXT_DIRECT_BYPASS_LEN": "32"}, clear=False)
    @patch("edge_client.gateway.humanize_sender.humanize_send_sync", side_effect=AssertionError("humanize should be bypassed"))
    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_short_text_bypasses_humanize(
        self,
        mock_post: MagicMock,
        _mock_humanize: MagicMock,
    ) -> None:
        """短文本主回复优先直发，不走拟人化分段。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(content="你是")
        result = adapter.send_reply(reply)

        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_image_file(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-03: 发送本地文件图片。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(
            msg_type=MsgType.IMAGE,
            media_path="C:/images/test.jpg",
        )
        result = adapter.send_reply(reply)
        self.assertTrue(result)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["message"][0]["type"], "image")
        self.assertTrue(payload["message"][0]["data"]["file"].startswith("file:///"))

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_image_url(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-04: 通过 URL 发送图片。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(
            msg_type=MsgType.IMAGE,
            content="https://example.com/image.jpg",
        )
        result = adapter.send_reply(reply)
        self.assertTrue(result)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["message"][0]["data"]["file"], "https://example.com/image.jpg")

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_image_url_with_caption_in_same_message(self, mock_post: MagicMock) -> None:
        """图片推荐卡应把封面和说明文本放进同一条 OneBot 消息。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(
            msg_type=MsgType.IMAGE,
            content="1. 桌游咖 3\n详情：https://www.lightnovel.fun/cn/detail/1143317",
            media_path="https://example.com/image.jpg",
        )
        result = adapter.send_reply(reply)
        self.assertTrue(result)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["message"][0]["type"], "image")
        self.assertEqual(payload["message"][0]["data"]["file"], "https://example.com/image.jpg")
        self.assertEqual(payload["message"][1]["type"], "text")
        self.assertIn("桌游咖 3", payload["message"][1]["data"]["text"])
        self.assertNotIn("example.com/image.jpg", payload["message"][1]["data"]["text"])

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_image_base64(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-05: 通过 base64 发送图片。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        test_data = b"\x89PNG\r\n\x1a\nfake_image_data"
        adapter = _adapter()
        reply = self._make_reply(
            msg_type=MsgType.IMAGE,
            content="",
            media_data=test_data,
        )
        result = adapter.send_reply(reply)
        self.assertTrue(result)

        payload = mock_post.call_args[1]["json"]
        file_uri = payload["message"][0]["data"]["file"]
        self.assertTrue(file_uri.startswith("base64://"))
        decoded = base64.b64decode(file_uri.removeprefix("base64://"))
        self.assertEqual(decoded, test_data)

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_voice(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-06: 发送语音消息。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(
            msg_type=MsgType.VOICE,
            media_path="C:/audio/voice.silk",
        )
        result = adapter.send_reply(reply)
        self.assertTrue(result)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["message"][0]["type"], "record")
        self.assertTrue(payload["message"][0]["data"]["file"].startswith("file:///"))

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_file_private_uses_upload_private_file(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-06b: 私聊文件走 OneBot 文件上传接口。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(b"fake epub")
            file_path = tmp.name
        try:
            adapter = _adapter()
            reply = self._make_reply(
                msg_type=MsgType.FILE,
                media_path=file_path,
            )
            reply.raw_extra["file_name"] = "book.epub"
            result = adapter.send_reply(reply)
        finally:
            os.unlink(file_path)

        self.assertTrue(result)
        call_args = mock_post.call_args
        self.assertIn("upload_private_file", call_args[0][0])
        payload = call_args[1]["json"]
        self.assertEqual(payload["user_id"], 789012)
        self.assertEqual(payload["name"], "book.epub")
        self.assertTrue(payload["file"].endswith(".epub"))

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_file_group_uses_upload_group_file(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-06c: 群文件走 OneBot 群文件上传接口。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"hello")
            file_path = tmp.name
        try:
            adapter = _adapter()
            reply = self._make_reply(
                conversation_id="qq_group_111222",
                msg_type=MsgType.FILE,
                media_path=file_path,
            )
            result = adapter.send_reply(reply)
        finally:
            os.unlink(file_path)

        self.assertTrue(result)
        call_args = mock_post.call_args
        self.assertIn("upload_group_file", call_args[0][0])
        payload = call_args[1]["json"]
        self.assertEqual(payload["group_id"], 111222)

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_send_emoji_as_primary_reply(self, mock_post: MagicMock) -> None:
        """EMOJI 主回复应作为正式可发送类型处理。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok", "retcode": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply(
            msg_type=MsgType.EMOJI,
            content="😊",
        )
        result = adapter.send_reply(reply)

        self.assertTrue(result)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["message"][0]["type"], "text")
        self.assertEqual(payload["message"][0]["data"]["text"], "😊")

    def test_unsupported_msg_type(self) -> None:
        """T-QQ-OUTBOUND-07: 不支持的消息类型返回 False。"""
        adapter = _adapter()
        reply = self._make_reply(msg_type=MsgType.VIDEO)
        result = adapter.send_reply(reply)
        self.assertFalse(result)

    def test_unknown_conversation_id(self) -> None:
        """T-QQ-OUTBOUND-08: 无法识别的 conversation_id 返回 False。"""
        adapter = _adapter()
        reply = self._make_reply(conversation_id="unknown_format_123")
        # TEXT 类型，但 conversation_id 格式不对
        result = adapter.send_reply(reply)
        self.assertFalse(result)

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.post")
    def test_api_returns_failure(self, mock_post: MagicMock) -> None:
        """T-QQ-OUTBOUND-09: OneBot API 返回失败结果。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "failed", "retcode": -1}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        adapter = _adapter()
        reply = self._make_reply()
        result = adapter.send_reply(reply)
        self.assertFalse(result)
        self.assertEqual(adapter._stats["outbound_failed"], 1)


# ===========================================================================
# T-QQ-HEALTH: health 测试
# ===========================================================================

class TestQQAdapterHealth(unittest.TestCase):
    """health 方法测试。"""

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.Client")
    def test_health_ok(self, mock_client_cls: MagicMock) -> None:
        """T-QQ-HEALTH-01: OneBot 状态正常。"""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"good": True, "online": True}
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        adapter = _adapter()
        status = adapter.health()

        self.assertEqual(status["platform"], "qq")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["self_id"], "123456")

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.Client")
    def test_health_ok_nested_data_good(self, mock_client_cls: MagicMock) -> None:
        """T-QQ-HEALTH-01b: OneBot v11 get_status(data.good=true) 判定为 ok。"""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "ok",
            "retcode": 0,
            "data": {"online": True, "good": True, "stat": {}},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        adapter = _adapter()
        status = adapter.health()

        self.assertEqual(status["platform"], "qq")
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["self_id"], "123456")

    @patch("edge_client.gateway.adapters.qq_adapter.httpx.Client")
    def test_health_error(self, mock_client_cls: MagicMock) -> None:
        """T-QQ-HEALTH-02: OneBot 连接失败。"""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        mock_client.post.side_effect = Exception("Connection refused")
        mock_client.get.side_effect = Exception("Connection refused")

        adapter = _adapter()
        status = adapter.health()

        self.assertEqual(status["status"], "error")
        self.assertIn("Connection refused", status["detail"])


# ===========================================================================
# T-QQ-UTIL: 内部工具方法测试
# ===========================================================================

class TestQQAdapterUtils(unittest.TestCase):
    """内部工具方法测试。"""

    def test_extract_text_filters_at(self) -> None:
        """T-QQ-UTIL-01: _extract_text_content 过滤 at 段。"""
        payload = {
            "message": [
                {"type": "at", "data": {"qq": "123456"}},
                {"type": "text", "data": {"text": " 你好世界 "}},
                {"type": "text", "data": {"text": " 吃饭了吗 "}},
            ],
        }
        content = QQAdapter._extract_text_content(payload, "fallback")
        self.assertEqual(content, "你好世界 吃饭了吗")

    def test_extract_text_fallback(self) -> None:
        """T-QQ-UTIL-02: message 非数组时回退到 raw_message。"""
        payload = {"message": "not a list"}
        content = QQAdapter._extract_text_content(payload, "原始消息")
        self.assertEqual(content, "原始消息")

    def test_resolve_media_uri_priority(self) -> None:
        """T-QQ-UTIL-03: _resolve_media_uri 优先级 media_path > URL > base64。"""
        # media_path 优先
        reply = UnifiedReply(
            conversation_id="qq_dm_1",
            platform=Platform.QQ,
            msg_type=MsgType.IMAGE,
            content="https://fallback.com/img.jpg",
            media_path="C:/test.jpg",
            media_data=b"binary_data",
        )
        uri = QQAdapter._resolve_media_uri(reply)
        self.assertIsNotNone(uri)
        assert uri is not None
        self.assertTrue(uri.startswith("file:///"))

        # 无 media_path 时走 URL
        reply2 = UnifiedReply(
            conversation_id="qq_dm_1",
            platform=Platform.QQ,
            msg_type=MsgType.IMAGE,
            content="https://example.com/img.jpg",
            media_data=b"binary_data",
        )
        uri2 = QQAdapter._resolve_media_uri(reply2)
        self.assertEqual(uri2, "https://example.com/img.jpg")

        # media_path 本身是 URL 时直接走 URL，而不是 file:///https://...
        reply2b = UnifiedReply(
            conversation_id="qq_dm_1",
            platform=Platform.QQ,
            msg_type=MsgType.IMAGE,
            content="图文说明",
            media_path="https://example.com/path.jpg",
        )
        uri2b = QQAdapter._resolve_media_uri(reply2b)
        self.assertEqual(uri2b, "https://example.com/path.jpg")

        # 无 media_path 无 URL 时走 base64
        reply3 = UnifiedReply(
            conversation_id="qq_dm_1",
            platform=Platform.QQ,
            msg_type=MsgType.IMAGE,
            content="",
            media_data=b"binary_data",
        )
        uri3 = QQAdapter._resolve_media_uri(reply3)
        self.assertIsNotNone(uri3)
        assert uri3 is not None
        self.assertTrue(uri3.startswith("base64://"))


if __name__ == "__main__":
    unittest.main()
