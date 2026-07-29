from __future__ import annotations

import inspect

from host_brain.tribe.ans_agent import ANSAgent
from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.language_system_agent import LanguageSystemAgent
from host_brain.tribe.motor_cortex_agent import MotorCortexAgent
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
