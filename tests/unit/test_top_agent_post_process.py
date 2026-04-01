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
        assert ledger.voice_events
        assert ledger.voice_events[0]["event_type"] == "INPUT_COMPLETED"
        assert ledger.voice_events[0]["payload"]["person_id"] == "person-001"

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
