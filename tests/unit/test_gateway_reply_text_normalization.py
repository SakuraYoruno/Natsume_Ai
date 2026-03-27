from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.app.graph.input_source_strategy import (
    CommitResult,
    GatewayInputSourceStrategy,
    InputSourceRuntimeContext,
)
from host_brain.app.handlers.gateway_orchestrator_handler import (
    _normalize_gateway_commit_narrative,
)


def test_gateway_commit_narrative_extracts_visible_text_from_contract_json() -> None:
    raw_text = (
        '{"narrative":{"text":"你好呀。"},"intent_draft":{},'
        '"meta":{"draft_only":true}}'
    )

    narrative = _normalize_gateway_commit_narrative(raw_text=raw_text)

    assert narrative == "你好呀。"


def test_gateway_post_chain_sends_normalized_text_reply() -> None:
    strategy = GatewayInputSourceStrategy()
    context = InputSourceRuntimeContext(
        request_id="req-gateway-normalize",
        source_upper="GATEWAY",
        payload={
            "platform": "qq",
            "conversation_id": "qq_dm_1",
        },
        environ={
            "HOST_INPUT_GATEWAY_AUTO_REPLY": "true",
            "HOST_INPUT_GATEWAY_DIRECT_REPLY_ONLY": "false",
        },
    )
    policy = strategy.build_policy(context=context)
    commit_result = CommitResult(
        decision="APPROVE",
        reply_narrative=(
            '{"narrative":{"text":"你好呀。"},"intent_draft":{},'
            '"meta":{"draft_only":true}}'
        ),
    )
    post_gateway_reply = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            status="sent",
            reason_code=None,
            detail="",
        )
    )

    with patch("host_brain.channels.edge_rpc_client.post_gateway_reply", post_gateway_reply):
        asyncio.run(
            strategy.post_chain(
                context=context,
                policy=policy,
                commit_result=commit_result,
            )
        )

    assert post_gateway_reply.await_count == 1
    assert post_gateway_reply.await_args.kwargs["content"] == "你好呀。"
