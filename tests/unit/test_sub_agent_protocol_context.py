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


def test_dispatcher_repair_agent_success_uses_can_retry_true(monkeypatch) -> None:
    """repair_agent 修复成功（can_retry=True）时，SubAgentResponse.success 应为 True。"""
    dispatcher = SubAgentDispatcher()

    def _repair_handler(payload, context):  # noqa: ANN001
        return {
            "agent_artifacts": {
                "repair_agent": {
                    "summary": "修复完成，可以重试。",
                    "confidence": 0.9,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "修复完成，可以重试。"},
            "repair_agent_trace": {
                "agent_id": "repair_agent",
                "success": True,
                "all_success": True,
                "can_retry": True,
                "detail": "llm_planner:pip_install ok=1/1",
                "actions": [],
                "ok_actions": 1,
                "ts_ms": 0,
            },
        }

    dispatcher._handler_map = {"repair_agent": _repair_handler}

    req = SubAgentRequest(
        agent_id="repair_agent",
        task="修复 requests 缺失",
        request_id="req-repair-001",
    )
    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is True, "can_retry=True 时 success 应为 True"


def test_dispatcher_repair_agent_success_uses_can_retry_false(monkeypatch) -> None:
    """repair_agent 修复失败（can_retry=False）时，SubAgentResponse.success 应为 False，即使有 final_answer。"""
    dispatcher = SubAgentDispatcher()

    def _repair_handler(payload, context):  # noqa: ANN001
        return {
            "agent_artifacts": {
                "repair_agent": {
                    "summary": "所有修复步骤均失败，无法自动重试。",
                    "confidence": 0.4,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "所有修复步骤均失败，无法自动重试。"},
            "repair_agent_trace": {
                "agent_id": "repair_agent",
                "success": False,
                "all_success": False,
                "can_retry": False,
                "detail": "llm_planner:pip_install ok=0/1",
                "actions": [],
                "ok_actions": 0,
                "ts_ms": 0,
            },
        }

    dispatcher._handler_map = {"repair_agent": _repair_handler}

    req = SubAgentRequest(
        agent_id="repair_agent",
        task="修复某依赖",
        request_id="req-repair-002",
    )
    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is False, "can_retry=False 时 success 应为 False，即使 final_answer 非空"
