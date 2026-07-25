from __future__ import annotations

import asyncio

from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.tribe_state import TribeStateRegistry


def _run(coro):
    return asyncio.run(coro)


def test_cortex_post_process_bond_update_queued(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Queue:
        def enqueue(self, payload):
            captured["payload"] = dict(payload)
            return {"status": "QUEUED"}

    class _Writer:
        @staticmethod
        def classify_interaction(*, text: str, emotion_intensity: float):
            assert text == "hello"
            assert emotion_intensity == 0.6
            return "INTERACTION_ENGAGED"

    monkeypatch.setattr("host_brain.bond.bond_dynamics.BondDynamicsWriter", _Writer)
    monkeypatch.setattr("host_brain.bond.user_identity.resolve_bond_user_id", lambda session_id: f"user-{session_id}")

    result = _run(
        CortexAgent(
            application_ports=TribeApplicationPorts(bond_update_queue=lambda: _Queue()),
            state_registry=TribeStateRegistry(),
        ).post_process_bond_update(
            request_id="req_1",
            session_id="sess_1",
            user_message="hello",
            emotion_context={"emotion_tag": "happy", "emotion_intensity": 0.6},
            ts_ms=123,
        )
    )

    assert result == {"status": "QUEUED", "update_kind": "INTERACTION_ENGAGED"}
    assert captured["payload"]["user_id"] == "user-sess_1"
    assert captured["payload"]["emotion_tag"] == "HAPPY"


def test_cortex_post_process_bond_update_fallback_writer(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Queue:
        def enqueue(self, payload):
            captured["queued_payload"] = dict(payload)
            return {"status": "FAILED"}

    class _Writer:
        @staticmethod
        def classify_interaction(*, text: str, emotion_intensity: float):
            return "INTERACTION_MINIMAL"

        def __init__(self, *, ledger, stage_engine):  # noqa: ANN001
            captured["ledger"] = ledger
            captured["stage_engine"] = stage_engine

        def apply_update(self, **kwargs):
            captured["written_payload"] = dict(kwargs)
            return None

    class _SocialBrain:
        def resolve_stage_engine(self):
            return "stage_engine_sentinel"

    monkeypatch.setattr("host_brain.bond.bond_dynamics.BondDynamicsWriter", _Writer)
    monkeypatch.setattr("host_brain.bond.user_identity.resolve_bond_user_id", lambda session_id: "user_2")
    monkeypatch.setattr("host_brain.tribe.cortex_agent.get_social_brain_agent", lambda: _SocialBrain())

    result = _run(
        CortexAgent(
            application_ports=TribeApplicationPorts(
                bond_update_queue=lambda: _Queue(),
                ledger=lambda: "ledger_sentinel",
            ),
            state_registry=TribeStateRegistry(),
        ).post_process_bond_update(
            request_id="req_2",
            session_id="sess_2",
            user_message="hi",
            emotion_context={"emotion_tag": "neutral", "emotion_intensity": 0.2},
            ts_ms=456,
        )
    )

    assert result == {"status": "WRITTEN", "update_kind": "INTERACTION_MINIMAL"}
    assert captured["ledger"] == "ledger_sentinel"
    assert captured["stage_engine"] == "stage_engine_sentinel"
    assert captured["written_payload"]["session_id"] == "sess_2"
