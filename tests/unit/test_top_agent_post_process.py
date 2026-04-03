from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from host_brain.app.top_agent import _async_post_process


def _run(coro):
    return asyncio.run(coro)


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


class _FakeLedger:
    def __init__(self) -> None:
        self.voice_events: list[dict[str, object]] = []

    def log_governor(self, _row):  # noqa: ANN001
        return None

    def log_llm_node_call(self, _row):  # noqa: ANN001
        return None

    def log_graph_run_trace(self, _row):  # noqa: ANN001
        return None

    def log_voice_event(self, row):  # noqa: ANN001
        self.voice_events.append(dict(row))


class _FakeInnerWorld:
    def __init__(self, *, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.calls: list[dict[str, object]] = []

    def on_session_end(self, user_id, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"user_id": user_id, **kwargs})
        if self.should_raise:
            raise RuntimeError("inner world boom")


class _FakeBodySenseModel:
    def __init__(self, *, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.calls = 0

    def on_conversation_end(self, *, duration_minutes=None) -> None:
        _ = duration_minutes
        self.calls += 1
        if self.should_raise:
            raise RuntimeError("body sense boom")


class TestTopAgentPostProcess:
    def test_async_post_process_writes_episodic_memory(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
        captured: dict[str, object] = {}
        ledger = _FakeLedger()

        def _fake_persist_chat_episodic_memory(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            return {"status": "OK", "memory_id": "mem::001"}

        with patch(
            "host_brain.airlock.quarantine.classify_ingress",
            return_value="FACT",
        ), patch(
            "host_brain.app.observability_persist.persist_chat_episodic_memory",
            side_effect=_fake_persist_chat_episodic_memory,
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_ledger",
            return_value=ledger,
        ), patch(
            "host_brain.app.dependencies.get_bond_stage_engine",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ), patch(
            "host_brain.bond.bond_dynamics.BondDynamicsWriter",
            _FakeBondWriter,
        ), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            return_value=None,
        ):
            _run(
                _async_post_process(
                    request_id="req-001",
                    person_id="person-001",
                    session_id="session-001",
                    user_message="remember me",
                    narrative="hello from natsume",
                    emotion_context={"emotion_tag": "HAPPY", "emotion_intensity": 0.7},
                    bond_summary={"stage": "FRIEND"},
                    ts_ms=1000,
                    render_target_lang="ja",
                ),
            )

        assert captured["request_id"] == "req-001"
        assert captured["assistant_reply"] == "hello from natsume"
        assert captured["user_message"] == "remember me"
        # INPUT_COMPLETED 已移至主流程同步写入（pre_commit 阶段），
        # _async_post_process 不再写入 INPUT_COMPLETED。
        # 验证 graph_run_trace 等审计日志仍正常写入。
        assert not any(
            ve.get("event_type") == "INPUT_COMPLETED" for ve in ledger.voice_events
        ), "INPUT_COMPLETED should no longer be written in _async_post_process"

    def test_async_post_process_swallow_persistence_failure(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
        ledger = _FakeLedger()

        with patch(
            "host_brain.airlock.quarantine.classify_ingress",
            return_value="FACT",
        ), patch(
            "host_brain.app.observability_persist.persist_chat_episodic_memory",
            side_effect=RuntimeError("episodic boom"),
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_ledger",
            return_value=ledger,
        ), patch(
            "host_brain.app.dependencies.get_bond_stage_engine",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ), patch(
            "host_brain.bond.bond_dynamics.BondDynamicsWriter",
            _FakeBondWriter,
        ), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            return_value=None,
        ):
            _run(
                _async_post_process(
                    request_id="req-002",
                    person_id="person-002",
                    session_id="session-002",
                    user_message="remember me",
                    narrative="hello from natsume",
                    emotion_context={"emotion_tag": "HAPPY", "emotion_intensity": 0.7},
                    bond_summary={"stage": "FRIEND"},
                    ts_ms=1000,
                    render_target_lang="ja",
                ),
            )

    def test_async_post_process_runs_persona_session_end_hooks(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
        ledger = _FakeLedger()
        inner_world = _FakeInnerWorld()
        body_sense = _FakeBodySenseModel()

        with patch(
            "host_brain.airlock.quarantine.classify_ingress",
            return_value="FACT",
        ), patch(
            "host_brain.app.observability_persist.persist_chat_episodic_memory",
            return_value={"status": "OK", "memory_id": "mem::003"},
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_ledger",
            return_value=ledger,
        ), patch(
            "host_brain.app.dependencies.get_bond_stage_engine",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ), patch(
            "host_brain.bond.bond_dynamics.BondDynamicsWriter",
            _FakeBondWriter,
        ), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ), patch(
            "host_brain.bond.user_identity.get_canonical_bond_user_id",
            return_value="persona-user-001",
        ), patch(
            "host_brain.persona.inner_world.get_inner_world",
            return_value=inner_world,
        ), patch(
            "host_brain.persona.body_sense.get_body_sense_model",
            return_value=body_sense,
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            return_value=None,
        ):
            _run(
                _async_post_process(
                    request_id="req-003",
                    person_id="person-003",
                    session_id="session-003",
                    user_message="我们刚才聊的那个数字生命方案还要继续。",
                    narrative="我会继续把它整理好。",
                    emotion_context={
                        "emotion_tag": "HAPPY",
                        "emotion_intensity": 0.7,
                        "valence": 0.42,
                        "arousal": 0.55,
                    },
                    bond_summary={"stage": "FRIEND"},
                    ts_ms=1000,
                    render_target_lang="zh",
                ),
            )

        assert body_sense.calls == 1
        assert len(inner_world.calls) == 1
        assert inner_world.calls[0]["user_id"] == "persona-user-001"
        assert inner_world.calls[0]["valence"] == 0.42
        assert inner_world.calls[0]["arousal"] == 0.55
        assert inner_world.calls[0]["pending_topics"] == ["我们刚才聊的那个数字生命方案还要继续。"]

    def test_async_post_process_persona_session_end_failure_does_not_block(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
        ledger = _FakeLedger()
        inner_world = _FakeInnerWorld(should_raise=True)
        body_sense = _FakeBodySenseModel(should_raise=True)

        with patch(
            "host_brain.airlock.quarantine.classify_ingress",
            return_value="FACT",
        ), patch(
            "host_brain.app.observability_persist.persist_chat_episodic_memory",
            return_value={"status": "OK", "memory_id": "mem::004"},
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_ledger",
            return_value=ledger,
        ), patch(
            "host_brain.app.dependencies.get_bond_stage_engine",
            return_value=SimpleNamespace(),
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ), patch(
            "host_brain.bond.bond_dynamics.BondDynamicsWriter",
            _FakeBondWriter,
        ), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ), patch(
            "host_brain.bond.user_identity.get_canonical_bond_user_id",
            return_value="persona-user-002",
        ), patch(
            "host_brain.persona.inner_world.get_inner_world",
            return_value=inner_world,
        ), patch(
            "host_brain.persona.body_sense.get_body_sense_model",
            return_value=body_sense,
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            return_value=None,
        ):
            _run(
                _async_post_process(
                    request_id="req-004",
                    person_id="person-004",
                    session_id="session-004",
                    user_message="继续",
                    narrative="好，我继续。",
                    emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.3},
                    bond_summary={"stage": "FRIEND"},
                    ts_ms=1000,
                    render_target_lang="zh",
                ),
            )

        assert len(inner_world.calls) == 1
        assert body_sense.calls == 1
