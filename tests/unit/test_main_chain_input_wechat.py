from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.graph.main_chain import run_main_chain_from_input


class _FakeChain:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run_as_sse_adapter(self, **kwargs):
        for event in self._events:
            yield event


def test_run_main_chain_from_input_wechat_approve_sends_narrative_reply():
    fake_chain = _FakeChain(
        [
            'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","narrative":"已收到"}\n\n',
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
                request_id="req-test-approve",
                session_id="wxid_user_a",
                source="WECHAT",
                text="你好",
                payload={
                    "from_wxid": "wxid_user_a",
                    "to_wxid": "wxid_bot",
                    "content": "你好",
                    "msg_type": "text",
                },
                ts_ms=1_800_000_000_000,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["action"] == "send_text"
    assert kwargs["target"] == "wxid_user_a"
    assert kwargs["content"] == "已收到"


def test_run_main_chain_from_input_wechat_reject_uses_fallback_reply():
    fake_chain = _FakeChain(
        [
            'data: {"phase":"COMMIT","done":true,"decision":"REJECT","reason_code":"MAIN_CHAIN_PRECHECK_FAILED"}\n\n',
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
    with patch.dict(
        "os.environ",
        {
            "HOST_INPUT_WECHAT_FALLBACK_ON_REJECT": "true",
            "HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT": "系统繁忙，请稍后重试",
        },
        clear=False,
    ), patch("host_brain.app.dependencies.get_main_chain", return_value=fake_chain), patch(
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
                request_id="req-test-reject",
                session_id="wxid_user_b",
                source="WECHAT",
                text="在吗",
                payload={
                    "from_wxid": "wxid_user_b",
                    "to_wxid": "wxid_bot",
                    "content": "在吗",
                    "msg_type": "text",
                },
                ts_ms=1_800_000_000_001,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["action"] == "send_text"
    assert kwargs["target"] == "wxid_user_b"
    assert kwargs["content"] == "系统繁忙，请稍后重试"
