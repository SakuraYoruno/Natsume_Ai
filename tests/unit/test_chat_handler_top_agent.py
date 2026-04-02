from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host_brain.app.chat_handler import _unified_chat_sse_generator, handle_chat


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    events: list[str] = []
    async for item in async_iterable:
        events.append(item)
    return events


class TestChatHandlerTopAgentBridge:
    def test_handle_chat_builds_intent_draft_from_pre_classifier(self) -> None:
        request_body = SimpleNamespace(
            message="please write code",
            session_id=None,
            thread_id=None,
            person_id=None,
            reply_channel="wechat",
            reply_target="conv-001",
            intent_draft=None,
        )
        request = SimpleNamespace(state=SimpleNamespace(request_id="req-handle-build"))
        metrics = MagicMock()
        captured: dict[str, object] = {}

        async def _fake_unified_chat_sse_generator(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            yield 'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","request_id":"req-handle-build"}\n\n'

        with patch(
            "host_brain.app.chat_handler.resolve_person_id",
            return_value="person-handle-build",
        ), patch(
            "host_brain.app.chat_handler.pre_intent_classifier",
            return_value="WORK_CODE",
        ), patch(
            "host_brain.app.chat_handler.allocate_intent_seq",
            return_value=7,
        ), patch(
            "host_brain.app.chat_handler._unified_chat_sse_generator",
            new=_fake_unified_chat_sse_generator,
        ):
            response = _run(handle_chat(request_body, request, metrics))
            events = _run(_collect_events(response.body_iterator))

        assert request_body.person_id == "person-handle-build"
        assert captured["session_id"] == "session::req-handle-build"
        assert captured["_pre_classified_scene"] == "WORK_CODE"
        assert captured["intent_draft"]["action"] == "work_code"
        assert captured["intent_draft"]["seq"] == 7
        assert captured["intent_draft"]["person_id"] == "person-handle-build"
        assert events[-1].startswith("data: ")

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

    def test_unified_chat_runs_governor_and_rejects_non_approve(self) -> None:
        request_body = SimpleNamespace(
            message="hello",
            session_id="session-reject",
            thread_id="thread-reject",
            scene_type=None,
            mood_state="steady",
            emotion_tag="NEUTRAL",
            emotion_intensity=0.2,
        )
        metrics = MagicMock()
        engine = MagicMock()
        engine.evaluate_intent.return_value = {
            "decision": "REJECT",
            "reason_code": "RATE_LIMIT",
            "intent_id": "intent-reject",
        }

        with patch(
            "host_brain.app.chat_handler.resolve_chat_emotion_context",
            return_value={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.2},
        ), patch(
            "host_brain.app.chat_handler.build_chat_governance_snapshot",
            return_value={"emotion_tag": "NEUTRAL"},
        ), patch(
            "host_brain.app.chat_handler.build_chat_emotion_snapshot",
            return_value={"version": "chat_emotion_snapshot.v1"},
        ), patch(
            "host_brain.app.chat_handler.get_intent_engine",
            return_value=engine,
        ):
            events = _run(
                _collect_events(
                    _unified_chat_sse_generator(
                        request_body=request_body,
                        request_id="req-reject",
                        session_id="session-reject",
                        ts_ms=1003,
                        intent_draft={"id": "intent-reject"},
                        metrics=metrics,
                        _pre_classified_scene=None,
                        _pre_approved_governor_result=None,
                    ),
                ),
            )

        payloads = [json.loads(event.removeprefix("data: ").strip()) for event in events]
        assert payloads[0]["event"] == "THINKING"
        assert payloads[-1]["decision"] == "REJECT"
        assert payloads[-1]["reason_code"] == "RATE_LIMIT"
        metrics.inc_rejection.assert_called_once_with("RATE_LIMIT")

    def test_unified_chat_accepts_valid_explicit_scene(self) -> None:
        request_body = SimpleNamespace(
            message="write code",
            session_id="session-explicit",
            thread_id="thread-explicit",
            scene_type="SCENE_WORK_CODE",
            mood_state="engaged",
            emotion_tag="FOCUSED",
            emotion_intensity=0.9,
        )

        async def _fake_top_agent_sse_generator(**_kwargs):  # noqa: ANN001
            yield 'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","request_id":"req-explicit"}\n\n'

        metrics = MagicMock()

        with patch(
            "host_brain.app.chat_handler.resolve_chat_emotion_context",
            return_value={"mood_state": "engaged", "emotion_tag": "FOCUSED", "emotion_intensity": 0.9},
        ), patch(
            "host_brain.app.chat_handler.build_chat_governance_snapshot",
            return_value={"emotion_tag": "FOCUSED"},
        ), patch(
            "host_brain.app.chat_handler.build_chat_emotion_snapshot",
            return_value={"version": "chat_emotion_snapshot.v1"},
        ), patch(
            "host_brain.app.chat_handler._logger.info",
        ) as info_mock, patch(
            "host_brain.app.top_agent.top_agent_sse_generator",
            new=_fake_top_agent_sse_generator,
        ):
            events = _run(
                _collect_events(
                    _unified_chat_sse_generator(
                        request_body=request_body,
                        request_id="req-explicit",
                        session_id="session-explicit",
                        ts_ms=1004,
                        intent_draft={"id": "intent-explicit"},
                        metrics=metrics,
                        _pre_classified_scene="CHAT",
                        _pre_approved_governor_result={
                            "decision": "APPROVE",
                            "intent_id": "intent-explicit",
                            "reason_code": None,
                        },
                    ),
                ),
            )

        assert info_mock.call_args.kwargs["extra"]["scene_type"] == "WORK_CODE"
        assert info_mock.call_args.kwargs["extra"]["scene_type_source"] == "explicit"
        assert json.loads(events[-1].removeprefix("data: ").strip())["decision"] == "APPROVE"

    def test_unified_chat_classifies_when_pre_scene_missing(self) -> None:
        request_body = SimpleNamespace(
            message="normal chat",
            session_id="session-no-pre",
            thread_id="thread-no-pre",
            scene_type=None,
            mood_state="steady",
            emotion_tag="NEUTRAL",
            emotion_intensity=0.3,
        )

        async def _fake_top_agent_sse_generator(**_kwargs):  # noqa: ANN001
            yield 'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","request_id":"req-no-pre"}\n\n'

        metrics = MagicMock()

        with patch(
            "host_brain.app.chat_handler.resolve_chat_emotion_context",
            return_value={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.3},
        ), patch(
            "host_brain.app.chat_handler.build_chat_governance_snapshot",
            return_value={"emotion_tag": "NEUTRAL"},
        ), patch(
            "host_brain.app.chat_handler.build_chat_emotion_snapshot",
            return_value={"version": "chat_emotion_snapshot.v1"},
        ), patch(
            "host_brain.app.chat_handler.pre_intent_classifier",
            return_value="CHAT",
        ) as classifier_mock, patch(
            "host_brain.app.top_agent.top_agent_sse_generator",
            new=_fake_top_agent_sse_generator,
        ):
            events = _run(
                _collect_events(
                    _unified_chat_sse_generator(
                        request_body=request_body,
                        request_id="req-no-pre",
                        session_id="session-no-pre",
                        ts_ms=1005,
                        intent_draft={"id": "intent-no-pre"},
                        metrics=metrics,
                        _pre_classified_scene=None,
                        _pre_approved_governor_result={
                            "decision": "APPROVE",
                            "intent_id": "intent-no-pre",
                            "reason_code": None,
                        },
                    ),
                ),
            )

        classifier_mock.assert_called_once_with("normal chat")
        assert json.loads(events[-1].removeprefix("data: ").strip())["decision"] == "APPROVE"

    def test_handle_chat_locked_mode_returns_reject_stream(self) -> None:
        request_body = SimpleNamespace(
            message="hello",
            session_id="session-locked",
            thread_id=None,
            person_id=None,
            reply_channel=None,
            reply_target=None,
            intent_draft=None,
        )
        request = SimpleNamespace(state=SimpleNamespace(request_id="req-locked"))
        metrics = MagicMock()

        with patch.dict("os.environ", {"HOST_SYS_MODE": "locked"}, clear=False), patch(
            "host_brain.app.chat_handler.resolve_person_id",
            return_value="person-locked",
        ):
            response = _run(handle_chat(request_body, request, metrics))
            events = _run(_collect_events(response.body_iterator))

        payload = json.loads(events[0].removeprefix("data: ").strip())
        assert payload["decision"] == "REJECT"
        assert payload["reason_code"] == "SYS_MODE_LOCKED"
        metrics.inc_chat.assert_called_once()

    def test_handle_chat_maintenance_mode_returns_reject_stream(self) -> None:
        request_body = SimpleNamespace(
            message="hello",
            session_id="session-maintenance",
            thread_id=None,
            person_id=None,
            reply_channel=None,
            reply_target=None,
            intent_draft=None,
        )
        request = SimpleNamespace(state=SimpleNamespace(request_id="req-maintenance"))
        metrics = MagicMock()

        with patch.dict("os.environ", {"HOST_SYS_MODE": "maintenance"}, clear=False), patch(
            "host_brain.app.chat_handler.resolve_person_id",
            return_value="person-maintenance",
        ):
            response = _run(handle_chat(request_body, request, metrics))
            events = _run(_collect_events(response.body_iterator))

        payload = json.loads(events[0].removeprefix("data: ").strip())
        assert payload["decision"] == "REJECT"
        assert payload["reason_code"] == "SYS_MODE_MAINTENANCE"
        metrics.inc_chat.assert_called_once()

    def test_unified_chat_invalid_explicit_scene_falls_back_to_chat(self) -> None:
        request_body = SimpleNamespace(
            message="hello",
            session_id="session-invalid-scene",
            thread_id="thread-invalid-scene",
            scene_type="SCENE_NOT_ALLOWED",
            mood_state="steady",
            emotion_tag="NEUTRAL",
            emotion_intensity=0.4,
        )

        async def _fake_top_agent_sse_generator(**_kwargs):  # noqa: ANN001
            yield 'data: {"phase":"COMMIT","done":true,"decision":"APPROVE","narrative":"ok","request_id":"req-invalid-scene"}\n\n'

        metrics = MagicMock()

        with patch(
            "host_brain.app.chat_handler.resolve_chat_emotion_context",
            return_value={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.4},
        ), patch(
            "host_brain.app.chat_handler.build_chat_governance_snapshot",
            return_value={"emotion_tag": "NEUTRAL"},
        ), patch(
            "host_brain.app.chat_handler.build_chat_emotion_snapshot",
            return_value={
                "version": "chat_emotion_snapshot.v1",
                "emotion_context": {"emotion_tag": "NEUTRAL"},
            },
        ), patch(
            "host_brain.app.chat_handler._logger.warning",
        ) as warning_mock, patch(
            "host_brain.app.top_agent.top_agent_sse_generator",
            new=_fake_top_agent_sse_generator,
        ):
            events = _run(
                _collect_events(
                    _unified_chat_sse_generator(
                        request_body=request_body,
                        request_id="req-invalid-scene",
                        session_id="session-invalid-scene",
                        ts_ms=1001,
                        intent_draft={"id": "intent-invalid-scene"},
                        metrics=metrics,
                        _pre_classified_scene="WORK_CODE",
                        _pre_approved_governor_result={
                            "decision": "APPROVE",
                            "intent_id": "intent-invalid-scene",
                            "reason_code": None,
                        },
                    ),
                ),
            )

        assert warning_mock.called
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"

    def test_unified_chat_top_agent_exception_returns_internal_error_commit(self) -> None:
        request_body = SimpleNamespace(
            message="hello",
            session_id="session-error",
            thread_id="thread-error",
            scene_type=None,
            mood_state="steady",
            emotion_tag="NEUTRAL",
            emotion_intensity=0.2,
        )

        async def _failing_top_agent_sse_generator(**_kwargs):  # noqa: ANN001
            if False:
                yield ""
            raise RuntimeError("boom")

        metrics = MagicMock()

        with patch(
            "host_brain.app.chat_handler.resolve_chat_emotion_context",
            return_value={"mood_state": "steady", "emotion_tag": "NEUTRAL", "emotion_intensity": 0.2},
        ), patch(
            "host_brain.app.chat_handler.build_chat_governance_snapshot",
            return_value={"emotion_tag": "NEUTRAL"},
        ), patch(
            "host_brain.app.chat_handler.build_chat_emotion_snapshot",
            return_value={
                "version": "chat_emotion_snapshot.v1",
                "emotion_context": {"emotion_tag": "NEUTRAL"},
            },
        ), patch(
            "host_brain.app.top_agent.top_agent_sse_generator",
            new=_failing_top_agent_sse_generator,
        ):
            events = _run(
                _collect_events(
                    _unified_chat_sse_generator(
                        request_body=request_body,
                        request_id="req-error",
                        session_id="session-error",
                        ts_ms=1002,
                        intent_draft={"id": "intent-error"},
                        metrics=metrics,
                        _pre_classified_scene="CHAT",
                        _pre_approved_governor_result={
                            "decision": "APPROVE",
                            "intent_id": "intent-error",
                            "reason_code": None,
                        },
                    ),
                ),
            )

        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "REJECT"
        assert commit["reason_code"] == "INTERNAL_SERVER_ERROR"
        metrics.inc_error.assert_called_once()
