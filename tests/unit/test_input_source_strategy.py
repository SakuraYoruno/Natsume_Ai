from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.app.graph.input_source_strategy import (
    AsrInputSourceStrategy,
    CommitResult,
    InputSourceRuntimeContext,
    InputSourceStrategy,
    WechatInputSourceStrategy,
    resolve_input_source_strategy,
)


def test_resolve_input_source_strategy_returns_expected_strategy():
    assert isinstance(resolve_input_source_strategy("WECHAT"), WechatInputSourceStrategy)
    assert isinstance(resolve_input_source_strategy("ASR"), AsrInputSourceStrategy)
    assert isinstance(resolve_input_source_strategy("UNKNOWN"), InputSourceStrategy)


def test_wechat_strategy_build_policy_prefers_display_name_target():
    strategy = WechatInputSourceStrategy()
    context = InputSourceRuntimeContext(
        request_id="req-strategy-build-policy",
        source_upper="WECHAT",
        payload={
            "from_wxid": "wxid_user_a",
            "raw_extra": {
                "sender_name": "三少爷的剑",
            },
        },
        environ={
            "HOST_INPUT_WECHAT_AUTO_REPLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT": "收到",
        },
    )

    policy = strategy.build_policy(context=context)

    assert policy.reply_target == "三少爷的剑"
    assert policy.reply_channel == "WECHAT"
    assert policy.reply_target_candidates == ["wxid_user_a", "三少爷的剑"]


def test_wechat_strategy_pre_chain_direct_reply_sends_text_and_short_circuits():
    strategy = WechatInputSourceStrategy()
    context = InputSourceRuntimeContext(
        request_id="req-strategy-pre-chain",
        source_upper="WECHAT",
        payload={
            "from_wxid": "wxid_user_b",
        },
        environ={
            "HOST_INPUT_WECHAT_AUTO_REPLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY": "true",
        },
    )
    policy = strategy.build_policy(context=context)
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch("host_brain.channels.edge_rpc_client.post_wechat_command", post_wechat_command):
        should_stop = asyncio.run(strategy.pre_chain(context=context, policy=policy))

    assert should_stop is True
    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["action"] == "send_text"
    assert kwargs["target"] == "wxid_user_b"
    assert kwargs["target_candidates"] == ["wxid_user_b"]
    assert kwargs["content"] == "收到"


def test_wechat_strategy_post_chain_approve_sends_narrative():
    strategy = WechatInputSourceStrategy()
    context = InputSourceRuntimeContext(
        request_id="req-strategy-post-chain",
        source_upper="WECHAT",
        payload={
            "from_wxid": "wxid_user_c",
        },
        environ={
            "HOST_INPUT_WECHAT_AUTO_REPLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY": "false",
        },
    )
    policy = strategy.build_policy(context=context)
    commit_result = CommitResult(
        decision="APPROVE",
        reply_narrative="好的，收到",
    )
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch("host_brain.channels.edge_rpc_client.post_wechat_command", post_wechat_command):
        asyncio.run(
            strategy.post_chain(
                context=context,
                policy=policy,
                commit_result=commit_result,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["target"] == "wxid_user_c"
    assert kwargs["content"] == "好的，收到"


def test_default_strategy_noop_hooks():
    strategy = resolve_input_source_strategy("UNKNOWN")
    context = InputSourceRuntimeContext(
        request_id="req-strategy-default",
        source_upper="UNKNOWN",
        payload={},
        environ={},
    )
    policy = strategy.build_policy(context=context)
    should_stop = asyncio.run(strategy.pre_chain(context=context, policy=policy))
    asyncio.run(
        strategy.post_chain(
            context=context,
            policy=policy,
            commit_result=CommitResult(),
        )
    )
    assert should_stop is False


def test_wechat_strategy_pre_chain_skips_filehelper_when_blocked():
    strategy = WechatInputSourceStrategy()
    context = InputSourceRuntimeContext(
        request_id="req-strategy-filehelper",
        source_upper="WECHAT",
        payload={
            "from_wxid": "filehelper",
        },
        environ={
            "HOST_INPUT_WECHAT_AUTO_REPLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY": "true",
            "HOST_INPUT_WECHAT_BLOCK_FILEHELPER": "true",
        },
    )
    policy = strategy.build_policy(context=context)
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch("host_brain.channels.edge_rpc_client.post_wechat_command", post_wechat_command):
        should_stop = asyncio.run(strategy.pre_chain(context=context, policy=policy))

    assert should_stop is True
    assert post_wechat_command.await_count == 0


def test_wechat_strategy_post_chain_reject_uses_fallback_text():
    strategy = WechatInputSourceStrategy()
    context = InputSourceRuntimeContext(
        request_id="req-strategy-fallback",
        source_upper="WECHAT",
        payload={
            "from_wxid": "wxid_user_d",
        },
        environ={
            "HOST_INPUT_WECHAT_AUTO_REPLY": "true",
            "HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY": "false",
            "HOST_INPUT_WECHAT_FALLBACK_ON_REJECT": "true",
            "HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT": "系统繁忙",
        },
    )
    policy = strategy.build_policy(context=context)
    commit_result = CommitResult(
        decision="REJECT",
        reason_code="TIMEOUT",
        detail="timeout",
        reply_narrative="",
    )
    post_wechat_command = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="ACCEPTED",
            reason_code=None,
            detail="",
        )
    )

    with patch("host_brain.channels.edge_rpc_client.post_wechat_command", post_wechat_command):
        asyncio.run(
            strategy.post_chain(
                context=context,
                policy=policy,
                commit_result=commit_result,
            )
        )

    assert post_wechat_command.await_count == 1
    kwargs = post_wechat_command.await_args.kwargs
    assert kwargs["target"] == "wxid_user_d"
    assert kwargs["content"] == "系统繁忙"
