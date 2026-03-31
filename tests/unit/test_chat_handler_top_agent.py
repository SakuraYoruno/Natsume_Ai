from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host_brain.app.chat_handler import _unified_chat_sse_generator


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    events: list[str] = []
    async for item in async_iterable:
        events.append(item)
    return events


class TestChatHandlerTopAgentBridge:
    def test_forwards_emotion_context_to_top_agent(self) -> None:
        request_body = SimpleNamespace(
            message="hello",
            session_id="session-001",
            thread_id="thread-001",
            scene_type=None,
            mood_state="engaged",
            emotion_tag="HAPPY",
            emotion_intensity=0.8,
        )
        captured: dict[str, object] = {}

        async def _fake_top_agent_sse_generator(**kwargs):  # noqa: ANN001
            captured["emotion_context"] = dict(kwargs["emotion_context"])
            yield 'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","narrative":"ok","request_id":"req-001"}\n\n'

        metrics = MagicMock()

        with patch(
            "host_brain.app.chat_handler.resolve_chat_emotion_context",
            return_value={
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.8,
            },
        ), patch(
            "host_brain.app.chat_handler.build_chat_governance_snapshot",
            return_value={"emotion_tag": "HAPPY"},
        ), patch(
            "host_brain.app.chat_handler.build_chat_emotion_snapshot",
            return_value={
                "version": "chat_emotion_snapshot.v1",
                "emotion_context": {"emotion_tag": "HAPPY"},
            },
        ), patch(
            "host_brain.app.top_agent.top_agent_sse_generator",
            new=_fake_top_agent_sse_generator,
        ):
            events = _run(
                _collect_events(
                    _unified_chat_sse_generator(
                        request_body=request_body,
                        request_id="req-001",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        metrics=metrics,
                        _pre_classified_scene="CHAT",
                        _pre_approved_governor_result={
                            "decision": "APPROVE",
                            "intent_id": "intent-001",
                            "reason_code": None,
                        },
                    ),
                ),
            )

        assert captured["emotion_context"]["emotion_tag"] == "HAPPY"
        assert [json.loads(event.removeprefix("data: ").strip())["phase"] for event in events] == [
            "PRESENCE",
            "PRESENCE",
            "COMMIT",
        ]
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
