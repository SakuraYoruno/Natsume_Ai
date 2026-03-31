from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.top_agent import top_agent_sse_generator


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    events: list[str] = []
    async for item in async_iterable:
        events.append(item)
    return events


async def _empty_chunks(**_kwargs):  # noqa: ANN001
    if False:
        yield ""


async def _fake_retrieval(**_kwargs):  # noqa: ANN001
    return {"status": "OK"}, []


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
    return "final answer", {"reflection": {"enabled": False}}


async def _noop_async(*_args, **_kwargs):  # noqa: ANN001
    return None


class _FakeRecallPackBuilder:
    def build(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("recall pack should not be built for empty retrieval")


class _FakeBondQueue:
    def enqueue(self, _payload):  # noqa: ANN001
        return {"status": "QUEUED"}


class _FakeBondWriter:
    @staticmethod
    def classify_interaction(*_args, **_kwargs):  # noqa: ANN001, ANN003
        return "NOOP"

    def __init__(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        pass

    def apply_update(self, **_kwargs):  # noqa: ANN003
        return None


class TestTopAgentSseGenerator:
    def test_rejects_blank_message_at_airlock(self) -> None:
        request_body = SimpleNamespace(message="   ", session_id="session-001", thread_id=None)
        metrics = MagicMock()

        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-airlock",
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

        assert len(events) == 1
        body = json.loads(events[0].removeprefix("data: ").strip())
        assert body["decision"] == "REJECT"
        assert body["reason_code"] == "AIRLOCK_EMPTY_MESSAGE"

    def test_rejects_when_governor_blocks(self) -> None:
        request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id=None)
        metrics = MagicMock()

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "REJECT", "reason_code": "RATE_LIMIT"},
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-governor",
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

        assert [json.loads(event.removeprefix("data: ").strip())["phase"] for event in events] == [
            "PRESENCE",
            "COMMIT",
        ]
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "REJECT"
        assert commit["reason_code"] == "RATE_LIMIT"

    def test_success_path_emits_commit_from_top_agent(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

        request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id="thread-001")
        metrics = MagicMock()
        captured: dict[str, object] = {}

        def _fake_top_agent_prompt(**kwargs):  # noqa: ANN001
            captured["prompt_kwargs"] = dict(kwargs)
            return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
        ), patch(
            "host_brain.app.chat_retrieval_helpers._run_retrieval",
            new=_fake_retrieval,
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
            side_effect=_fake_top_agent_prompt,
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
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            new=_noop_async,
        ), patch("host_brain.bond.bond_dynamics.BondDynamicsWriter", _FakeBondWriter), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-success",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.5},
                        emotion_snapshot={"emotion_tag": "NEUTRAL"},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        phases = [json.loads(event.removeprefix("data: ").strip())["phase"] for event in events]
        assert phases[0] == "PRESENCE"
        assert phases[-1] == "COMMIT"
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
        assert commit["narrative"] == "final answer"
        assert commit["audit_refs"]["path"] == "chat_direct"
        assert captured["prompt_kwargs"]["render_target_lang"] == "ja"
