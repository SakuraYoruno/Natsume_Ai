from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.input_routes import InputPayload, receive_input


def test_receive_input_smoke_wechat_dispatches_to_graph():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.app.input_routes._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="WECHAT",
                    from_wxid="wxid_user_a",
                    to_wxid="wxid_bot",
                    content="hello",
                    msg_type="text",
                ),
                request=MagicMock(),
            )
        )

    assert response.success is True
    assert response.status == "ACCEPTED"
    assert response.source == "WECHAT"
    assert dispatch_mock.await_count == 1
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["source"] == "WECHAT"
    assert kwargs["session_id"] == "wxid_user_a"
    assert kwargs["text"] == "hello"


def test_receive_input_smoke_asr_dispatches_to_graph():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.app.input_routes._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="ASR",
                    session_id="asr_session_1",
                    text="hello from mic",
                    confidence=0.92,
                    is_final=True,
                    seq=7,
                ),
                request=MagicMock(),
            )
        )

    assert response.success is True
    assert response.status == "ACCEPTED"
    assert response.source == "ASR"
    assert dispatch_mock.await_count == 1
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["source"] == "ASR"
    assert kwargs["session_id"] == "asr_session_1"
    assert kwargs["text"] == "hello from mic"
