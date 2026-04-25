from __future__ import annotations

from host_brain.app.input_routes import _build_gateway_multimodal_placeholder_text


def test_gateway_multimodal_placeholder_keeps_media_social_neutral() -> None:
    assert _build_gateway_multimodal_placeholder_text(
        content="",
        attachments=[{"type": "image"}],
    ) == "[图片消息]"
    assert _build_gateway_multimodal_placeholder_text(
        content="",
        attachments=[{"type": "emoji"}],
    ) == "[表情消息]"
    assert "看一下" not in _build_gateway_multimodal_placeholder_text(
        content="",
        attachments=[{"type": "image"}],
    )


def test_gateway_multimodal_placeholder_preserves_user_caption() -> None:
    assert _build_gateway_multimodal_placeholder_text(
        content="这是什么",
        attachments=[{"type": "image"}],
    ) == "这是什么"


def test_gateway_multimodal_placeholder_uses_neutral_attachment_markers() -> None:
    assert _build_gateway_multimodal_placeholder_text(content="", attachments=[{"type": "video"}]) == "[视频消息]"
    assert _build_gateway_multimodal_placeholder_text(content="", attachments=[{"type": "voice"}]) == "[语音消息]"
    assert _build_gateway_multimodal_placeholder_text(content="", attachments=[{"type": "file"}]) == "[文件消息]"
    assert _build_gateway_multimodal_placeholder_text(content="", attachments=[{"type": "unknown"}]) == "[附件消息]"
    assert _build_gateway_multimodal_placeholder_text(
        content="",
        attachments=[{"type": "image"}, {"type": "file"}, {"type": "video"}, {"type": "emoji"}],
    ) == "[多附件消息]"
