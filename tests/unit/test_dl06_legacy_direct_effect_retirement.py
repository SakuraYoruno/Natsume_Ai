from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.ans_agent import ANSAgent
from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.language_system_agent import LanguageSystemAgent
from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
from host_brain.tribe.social_brain_agent import SocialBrainAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_legacy_motor_speech_rejects_without_state_or_edge_effect() -> None:
    registry = TribeStateRegistry()
    motor = MotorCortexAgent(state_registry=registry)

    result = motor.dispatch_speak(
        text="不应从旧 Tribe 路径发声。",
        emotion_tag="SAD",
        emotion_intensity=0.8,
        utterance_id="utterance-dl06-retired",
        request_id="request-dl06-retired",
        session_id="session-dl06-retired",
    )

    assert result == {
        "ok": False,
        "utterance_id": "utterance-dl06-retired",
        "reason_code": "LEGACY_TRIBE_DIRECT_SPEAK_RETIRED",
        "authority": "CANONICAL_LIFE_DELIVERY_PROTOCOL",
    }
    assert registry.get_state("motor_cortex_agent") is None

    source = inspect.getsource(MotorCortexAgent.dispatch_speak)
    assert "submit_voice_command" not in source
    assert "MOTOR_SPEAK_DISPATCHED" not in source
    assert "drive_emotion_metabolism" not in source


def test_legacy_event_bus_bridges_no_longer_turn_text_into_voice() -> None:
    cortex_source = inspect.getsource(CortexAgent)
    language_source = inspect.getsource(LanguageSystemAgent.resolve_dual_system)
    ans_source = inspect.getsource(ANSAgent.register_subscriptions)

    assert "PROACTIVE_VOICE_TRIGGERED" not in cortex_source
    assert "get_motor_cortex_agent" not in cortex_source
    assert "dispatch_speak" not in language_source
    assert "get_motor_cortex_agent" not in language_source
    assert "MOTOR_SPEAK_DISPATCHED" not in ans_source
    assert not hasattr(CortexAgent, "_on_proactive_voice_triggered")
    assert not hasattr(ANSAgent, "_on_motor_speak_dispatched")


def test_canonical_life_retires_social_brain_event_writers_and_reads_bounded_projection(
    monkeypatch,
) -> None:
    class _Reader:
        def get_snapshot(self, person_id: str) -> dict[str, object]:
            assert person_id == "person-dl06"
            return {
                "version": 17,
                "state_digest": "life-digest-17",
                "state": {
                    "relationship_v2": {
                        "self_id": "natsume_ai",
                        "workspace_id": "workspace-dl06",
                        "relationship_subject_id": "person-dl06",
                        "revision": 4,
                        "trust": 0.72,
                        "safety": 0.68,
                        "attunement": 0.61,
                        "rupture_load": 0.42,
                        "repair_required": True,
                        "proactive_permission": "DENIED",
                    }
                },
            }

    class _Bus:
        def __init__(self) -> None:
            self.subscriptions: list[tuple[str, object]] = []

        def subscribe(self, event_type: str, handler: object) -> None:
            self.subscriptions.append((event_type, handler))

    class _ForbiddenStageEngine:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(
                f"canonical projection must not read BondStageEngine:{name}"
            )

    reader = _Reader()
    registry = TribeStateRegistry()
    bus = _Bus()
    agent = SocialBrainAgent(
        application_ports=TribeApplicationPorts(
            life_snapshot_reader=lambda: reader,
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
        state_registry=registry,
    )
    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: bus,
    )
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "person-dl06",
    )

    agent.register_subscriptions()
    event = SimpleNamespace(payload={"severity": 0.9, "user_id": "ignored"}, ts_ms=99)
    retired_results = [
        asyncio.run(getattr(agent, handler_name)(event))
        for handler_name in (
            "_on_interaction_recorded",
            "_on_bond_progress_snapshot",
            "_on_bond_stage_changed",
            "_on_bond_regress_snapshot",
            "_on_task_success_bond",
            "_on_loneliness_signal_bond",
            "_on_immune_block_bond",
            "_on_bond_rupture_signal",
            "_on_bond_repair_action",
            "_on_humor_landed_bond",
        )
    ]
    context = agent.get_bond_context(session_id="session-dl06", user_id="ignored")
    narrative = agent.resolve_bond_narrative(user_id="ignored")
    projection = agent.resolve_bond_projection(
        person_id="ignored",
        stage_engine=_ForbiddenStageEngine(),
    )

    assert bus.subscriptions == []
    assert {
        result["reason_code"]
        for result in retired_results
    } == {"LEGACY_SOCIAL_BRAIN_RELATIONSHIP_WRITE_RETIRED"}
    assert registry.get_state("social_brain_agent") is None
    assert context["authority"] == "CANONICAL_LIFE_JOURNAL"
    assert context["bond_stage"] == "friend"
    assert context["bond_vector"]["trust"] == 0.72
    assert context["bond_vector"]["dependency"] == 0.0
    assert context["bond_summary"]["proactive_permission"] == "DENIED"
    assert narrative["status"] == "RETIRED_CANONICAL_LIFE"
    assert projection["authority"]["legal_writer"] == "CANONICAL_LIFE_JOURNAL"
    assert projection["compatibility"]["current_stage"] == "FRIEND"
    assert projection["relationship"]["vector"]["dependency"] == 0.0
