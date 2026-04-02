from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host_brain.app.top_agent import _async_post_process, top_agent_sse_generator


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    events: list[str] = []
    async for item in async_iterable:
        events.append(item)
    return events


async def _failing_retrieval(**_kwargs):  # noqa: ANN001
    raise RuntimeError("chromadb package unavailable")


async def _fake_state_arbiter(**_kwargs):  # noqa: ANN001
    return {}, None


async def _fake_runtime(**_kwargs):  # noqa: ANN001
    return {
        "available": True,
        "provider_name": "mock",
        "model_id": "mock-model",
        "timeout_ms": 1000,
        "llm_api_key": "",
        "base_url_override": "",
    }


def _fake_apply_response_intelligence(**_kwargs):  # noqa: ANN001
    return "final answer", {}


async def _empty_chunks(**_kwargs):  # noqa: ANN001
    if False:
        yield ""


async def _noop_async(**_kwargs):  # noqa: ANN001
    return None


class _FakeRecallPackBuilder:
    def build(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("recall pack should not be built when retrieval fails early")


class _FakeBondQueue:
    def enqueue(self, _payload):  # noqa: ANN001
        return {"status": "QUEUED"}


class TestChatRetrievalInitFailureDegrade:
    def test_retrieval_init_failure_degrades_without_breaking_chat(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

        request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id=None)
        metrics = MagicMock()
        captured: dict[str, object] = {}

        def _fake_prompt(**kwargs):  # noqa: ANN001
            captured["retrieval_summary"] = dict(kwargs["retrieval_summary"])
            return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
        ), patch(
            "host_brain.app.chat_retrieval_helpers._run_retrieval",
            new=_failing_retrieval,
        ), patch(
            "host_brain.app.prompt_builder.infer_render_target_lang",
            return_value="ja",
        ), patch(
            "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
            new=_fake_state_arbiter,
        ), patch(
            "host_brain.app.dependencies.get_recall_pack_builder",
            return_value=_FakeRecallPackBuilder(),
        ), patch(
            "host_brain.app.top_agent_prompt.build_top_agent_prompt",
            side_effect=_fake_prompt,
        ), patch(
            "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
            new=_fake_runtime,
        ), patch(
            "host_brain.integrations.llm_helpers.astream_llm_chunks",
            new=_empty_chunks,
        ), patch(
            "host_brain.app.intelligent_runtime.apply_response_intelligence",
            new=_fake_apply_response_intelligence,
        ), patch(
            "host_brain.app.top_agent._async_post_process",
            new=_noop_async,
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=None,
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-retrieval",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={},
                        emotion_snapshot={},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        assert captured["retrieval_summary"]["status"] == "DEGRADED"
        assert "chromadb" in str(captured["retrieval_summary"]["error"])
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
        assert commit["narrative"] == "final answer"

    def test_async_post_process_persists_recall_failure_details_into_trace(self) -> None:
        ledger = MagicMock()

        with patch(
            "host_brain.app.dependencies.get_ledger",
            return_value=ledger,
        ), patch(
            "host_brain.airlock.quarantine.classify_ingress",
            return_value="safe",
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=None,
        ):
            _run(
                _async_post_process(
                    request_id="req-trace-recall-failed",
                    session_id="session-001",
                    user_message="贪吃蛇",
                    narrative="先给你普通回复",
                    person_id="user_global_test",
                    emotion_context={},
                    bond_summary=None,
                    ts_ms=1000,
                    render_target_lang="zh",
                    total_latency_ms=100,
                    llm_latency_ms=50,
                    provider_name="gemini",
                    model_id="gemini-2.5-flash",
                    retrieval_summary={
                        "status": "DEGRADED",
                        "candidate_count": 0,
                        "reason_code": "RETRIEVAL_FAILED",
                        "error": "Connection refused",
                        "route_count": 0,
                        "rerank_topk": 0,
                        "degraded": True,
                        "target_stores": ["episodic_vector"],
                        "query_plan": {"intent": "chat", "skip_retrieval": False},
                    },
                    gov_decision={"decision": "APPROVE", "latency_ms": 0},
                )
            )

        graph_row = ledger.log_graph_run_trace.call_args.args[0]
        recall_node = next(node for node in graph_row["nodes_json"] if node["node_id"] == "recall_fusion")
        assert recall_node["output_data"]["reason_code"] == "RETRIEVAL_FAILED"
        assert recall_node["output_data"]["error"] == "Connection refused"
        assert recall_node["output_data"]["target_stores"] == ["episodic_vector"]
