from __future__ import annotations

import asyncio

from host_brain.app.sub_agent_protocol import SubAgentDispatcher, SubAgentRequest


def test_dispatcher_merges_request_context_into_handler_context() -> None:
    dispatcher = SubAgentDispatcher()
    captured: dict[str, object] = {}

    def _handler(payload, context):  # noqa: ANN001
        captured["payload"] = dict(payload)
        captured["context"] = dict(context)
        return {
            "agent_artifacts": {
                "translate_agent": {
                    "summary": "ok",
                    "confidence": 0.8,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "ok"},
        }

    dispatcher._handler_map = {"translate_agent": _handler}

    req = SubAgentRequest(
        agent_id="translate_agent",
        task="translate this",
        request_id="req-ctx-001",
        session_id="session-ctx-001",
        context={"gateway": "GW", "custom": "VALUE"},
    )

    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is True
    assert captured["context"]["gateway"] == "GW"
    assert captured["context"]["custom"] == "VALUE"
    assert "agent_context_envelope" in captured["context"]


def test_dispatcher_repair_agent_success_uses_can_retry_true() -> None:
    dispatcher = SubAgentDispatcher()

    def _repair_handler(payload, context):  # noqa: ANN001
        return {
            "agent_artifacts": {
                "repair_agent": {
                    "summary": "repair done",
                    "confidence": 0.9,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "repair done"},
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
        task="repair requests missing",
        request_id="req-repair-001",
    )
    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is True


def test_dispatcher_repair_agent_success_uses_can_retry_false() -> None:
    dispatcher = SubAgentDispatcher()

    def _repair_handler(payload, context):  # noqa: ANN001
        return {
            "agent_artifacts": {
                "repair_agent": {
                    "summary": "repair failed",
                    "confidence": 0.4,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "repair failed"},
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
        task="repair dependency",
        request_id="req-repair-002",
    )
    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is False


def test_dispatcher_attaches_subgraph_metadata_to_audit_refs() -> None:
    dispatcher = SubAgentDispatcher()

    def _intent_handler(payload, context):  # noqa: ANN001
        return {
            "agent_artifacts": {
                "intent_agent": {
                    "summary": "intent ok",
                    "confidence": 0.73,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "intent ok"},
            "subgraph_trace": [
                {"node_id": "planner_constraints", "status": "OK", "ts_ms": 1, "detail": "ok"},
            ],
            "subgraph_commit_summary": {
                "subgraph_id": "intent_agent_subgraph",
                "node_count": 1,
                "success_count": 1,
                "fail_count": 0,
                "committed_at_ms": 1,
                "graph_trace_flat": [
                    {"node_id": "planner_constraints", "status": "OK", "ts_ms": 1, "detail": "ok"},
                ],
            },
        }

    dispatcher._handler_map = {"intent_agent": _intent_handler}
    req = SubAgentRequest(
        agent_id="intent_agent",
        task="plan the next step",
        request_id="req-subgraph-001",
    )

    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is True
    assert result.audit_refs["subgraph_commit_summary"]["subgraph_id"] == "intent_agent_subgraph"
    assert result.audit_refs["subgraph_trace"][0]["node_id"] == "planner_constraints"


def test_dispatcher_builds_bounded_agent_context_envelope() -> None:
    dispatcher = SubAgentDispatcher()
    captured: dict[str, object] = {}

    def _handler(payload, context):  # noqa: ANN001
        captured["payload"] = dict(payload)
        captured["context"] = dict(context)
        return {
            "agent_artifacts": {
                "translate_agent": {
                    "summary": "ok",
                    "confidence": 0.8,
                    "risk_flags": [],
                }
            },
            "react_scratchpad": {"final_answer": "ok"},
        }

    dispatcher._handler_map = {"translate_agent": _handler}
    long_turns = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"turn-{index} " + ("x" * 900),
        }
        for index in range(12)
    ]
    req = SubAgentRequest(
        agent_id="translate_agent",
        task="translate bounded context",
        request_id="req-envelope-001",
        session_id="sess-envelope-001",
        context={
            "gateway": "GW",
            "custom": "VALUE",
            "api_key": "should-not-leak",
            "retrieval_summary": {"surface_memory_recall_packet": {"facts": ["重复的大块召回不应透传。"]}},
            "recent_turns": long_turns,
            "surface_memory_recall_packet": {
                "facts": ["长期事实：推荐按卡片分段。"],
                "evidence_refs": ["semantic://sem-1"],
            },
            "retrieval_evidence_items": [
                {
                    "doc_id": "doc-1",
                    "source_ref": "life_episode://ep-1",
                    "snippet": "episode evidence " + ("y" * 900),
                    "score": 0.9,
                }
            ],
        },
    )

    result = asyncio.run(dispatcher.dispatch(req))

    assert result.success is True
    payload = captured["payload"]
    context = captured["context"]
    envelope = context["agent_context_envelope"]
    assert payload["agent_context_envelope"] == envelope
    assert "recent_turns" not in payload
    assert "surface_memory_recall_packet" not in payload
    assert "retrieval_summary" not in context
    assert context["custom"] == "VALUE"
    assert context["api_key"] == "[REDACTED]"
    assert len(envelope["conversation_window"]["recent_turns"]) == 4
    assert envelope["conversation_window"]["recent_turns"][0]["content"].startswith("turn-8")
    assert len(envelope["conversation_window"]["recent_turns"][0]["content"]) <= 700
    assert envelope["memory_packet"]["facts"] == ["长期事实：推荐按卡片分段。"]
    assert envelope["semantic_facts"] == ["长期事实：推荐按卡片分段。"]
    assert envelope["evidence_pack"]["items"][0]["source_ref"] == "life_episode://ep-1"
    assert envelope["trace"]["context_profile"] == "translate"
    assert result.audit_refs["agent_context"]["conversation_turns"] == 4
    assert result.audit_refs["agent_context"]["memory_fact_count"] == 1
    assert result.audit_refs["agent_context"]["evidence_item_count"] == 1
