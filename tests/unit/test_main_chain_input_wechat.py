from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.graph.main_chain import run_main_chain_from_input


class _FakeChain:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run_as_sse_adapter(self, **kwargs):
        _ = kwargs
        for event in self._events:
            yield event


def test_run_main_chain_from_input_wechat_direct_reply_without_llm_chain():
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )
    get_main_chain_mock = MagicMock()

    with patch(
        "host_brain.channels.edge_rpc_client.post_wechat_command",
        post_wechat_command,
    ), patch(
        "host_brain.app.dependencies.get_main_chain",
        get_main_chain_mock,
    ):
        asyncio.run(
            run_main_chain_from_input(
                request_id="req-test-direct-reply",
                session_id="wxid_user_a",
                source="WECHAT",
                text="hello",
                payload={
                    "from_wxid": "wxid_user_a",
                    "to_wxid": "wxid_bot",
                    "content": "hello",
                    "msg_type": "text",
                },
                ts_ms=1_800_000_000_000,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["action"] == "send_text"
    assert kwargs["target"] == "wxid_user_a"
    assert kwargs["content"] == "收到"
    get_main_chain_mock.assert_not_called()


def test_run_main_chain_from_input_wechat_direct_reply_supports_custom_text():
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch.dict(
        "os.environ",
        {
            "HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT": "busy",
        },
        clear=False,
    ), patch(
        "host_brain.channels.edge_rpc_client.post_wechat_command",
        post_wechat_command,
    ):
        asyncio.run(
            run_main_chain_from_input(
                request_id="req-test-direct-reply-custom",
                session_id="wxid_user_b",
                source="WECHAT",
                text="ping",
                payload={
                    "from_wxid": "wxid_user_b",
                    "to_wxid": "wxid_bot",
                    "content": "ping",
                    "msg_type": "text",
                },
                ts_ms=1_800_000_000_001,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["action"] == "send_text"
    assert kwargs["target"] == "wxid_user_b"
    assert kwargs["content"] == "busy"


def test_run_main_chain_from_input_wechat_prefers_display_target_candidate():
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch(
        "host_brain.channels.edge_rpc_client.post_wechat_command",
        post_wechat_command,
    ):
        asyncio.run(
            run_main_chain_from_input(
                request_id="req-test-target-candidate",
                session_id="wxid_user_c",
                source="WECHAT",
                text="hello",
                payload={
                    "from_wxid": "wxid_user_c",
                    "to_wxid": "wxid_bot",
                    "content": "hello",
                    "msg_type": "text",
                    "raw_extra": {
                        "sender_name": "灏忔槑",
                    },
                },
                ts_ms=1_800_000_000_002,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["target"] == "灏忔槑"
    assert kwargs["target_candidates"] == ["wxid_user_c", "灏忔槑"]


def test_run_main_chain_from_input_wechat_skips_filehelper_target_when_blocked():
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch.dict(
        "os.environ",
        {
            "HOST_INPUT_WECHAT_BLOCK_FILEHELPER": "true",
        },
        clear=False,
    ), patch(
        "host_brain.channels.edge_rpc_client.post_wechat_command",
        post_wechat_command,
    ):
        asyncio.run(
            run_main_chain_from_input(
                request_id="req-test-filehelper-skip",
                session_id="filehelper",
                source="WECHAT",
                text="hello",
                payload={
                    "from_wxid": "filehelper",
                    "to_wxid": "wxid_bot",
                    "content": "hello",
                    "msg_type": "text",
                },
                ts_ms=1_800_000_000_003,
            )
        )

    assert post_wechat_command.await_count == 0


def test_run_main_chain_from_input_asr_never_triggers_wechat_reply():
    fake_chain = _FakeChain(
        [
            'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","narrative":"ok"}\n\n',
        ]
    )
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )
    with patch("host_brain.app.dependencies.get_main_chain", return_value=fake_chain), patch(
        "host_brain.app.dependencies.get_obs_metrics_collector",
        return_value=MagicMock(),
    ), patch(
        "host_brain.app.graph_config_builder.pre_intent_classifier",
        return_value="SCENE_CHAT",
    ), patch(
        "host_brain.channels.edge_rpc_client.post_wechat_command",
        post_wechat_command,
    ):
        asyncio.run(
            run_main_chain_from_input(
                request_id="req-test-asr-isolated",
                session_id="asr_session_1",
                source="ASR",
                text="hello from mic",
                payload={
                    "session_id": "asr_session_1",
                    "text": "hello from mic",
                    "from_wxid": "wxid_should_not_use",
                },
                ts_ms=1_800_000_000_004,
            )
        )

    assert post_wechat_command.await_count == 0
