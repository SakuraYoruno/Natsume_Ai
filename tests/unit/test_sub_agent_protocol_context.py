from __future__ import annotations

import asyncio

from host_brain.app.sub_agent_protocol import SubAgentDispatcher, SubAgentRequest


def test_dispatcher_merges_request_context_into_handler_context(monkeypatch) -> None:
    dispatcher = SubAgentDispatcher()
    captured: dict[str, object] = {}

    def _handler(payload, context):  # noqa: ANN001
        captured["payload"] = dict(payload)
        captured["context"] = dict(context)
        return {
            "agent_artifacts": {
                "code_agent": {
                    "summary": "ok",
                    "confidence": 0.8,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "ok"},
        }

    dispatcher._handler_map = {"code_agent": _handler}

    req = SubAgentRequest(
        agent_id="code_agent",
        task="write code",
        request_id="req-ctx-001",
        session_id="session-ctx-001",
        context={"gateway": "GW", "custom": "VALUE"},
    )

    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is True
    assert captured["context"]["gateway"] == "GW"
    assert captured["context"]["custom"] == "VALUE"
