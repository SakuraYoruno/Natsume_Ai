from __future__ import annotations

from edge_client.gateway.models import MsgType, Platform, UnifiedMessage
from edge_client.gateway.normalizer import MessageNormalizer


def test_gateway_normalizer_backfills_attachment_from_media_url() -> None:
    normalizer = MessageNormalizer()
    msg = UnifiedMessage(
        msg_id="qq-msg-attach-001",
        platform=Platform.QQ,
        from_id="10001",
        to_id="20002",
        conversation_id="qq_dm_10001",
        msg_type=MsgType.IMAGE,
        content="你看这张图",
        ts_ms=1700000000000,
        media_url="D:/tmp/demo.png",
    )

    normalized = normalizer.normalize(msg)

    assert normalized is not None
    assert len(normalized.attachments) == 1
    assert normalized.attachments[0].type == "image"
    assert normalized.attachments[0].local_path == "D:/tmp/demo.png"
