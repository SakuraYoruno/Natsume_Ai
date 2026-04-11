from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.input_routes import InputPayload, receive_input


def test_receive_input_smoke_gateway_dispatches_to_graph():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.app.input_routes._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="GATEWAY",
                    platform="wechat",
                    conversation_id="wxid_user_a",
                    content="hello",
                    msg_type="text",
                ),
                request=MagicMock(),
            )
        )

    assert response.success is True
    assert response.status == "ACCEPTED"
    assert response.source == "GATEWAY"
    assert dispatch_mock.await_count == 1
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["source"] == "GATEWAY"
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


def test_receive_input_smoke_asr_non_final_is_ignored():
    dispatch_mock = AsyncMock(return_value=None)

    with patch("host_brain.app.input_routes._dispatch_to_graph", dispatch_mock):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="ASR",
                    session_id="asr_session_partial",
                    text="partial mic text",
                    confidence=0.92,
                    is_final=False,
                    seq=8,
                ),
                request=MagicMock(),
            )
        )

    assert response.success is True
    assert response.status == "IGNORED"
    assert response.source == "ASR"
    assert dispatch_mock.await_count == 0


def test_receive_input_smoke_asr_low_confidence_is_ignored():
    dispatch_mock = AsyncMock(return_value=None)

    with patch.dict("os.environ", {"HOST_INPUT_ASR_MIN_CONFIDENCE": "0.95"}), patch(
        "host_brain.app.input_routes._dispatch_to_graph",
        dispatch_mock,
    ):
        response = asyncio.run(
            receive_input(
                body=InputPayload(
                    source="ASR",
                    session_id="asr_session_low_conf",
                    text="hello from mic",
                    confidence=0.80,
                    is_final=True,
                    seq=9,
                ),
                request=MagicMock(),
            )
        )

    assert response.success is True
    assert response.status == "IGNORED"
    assert response.source == "ASR"
    assert "noise gate" in response.detail
    assert dispatch_mock.await_count == 0
