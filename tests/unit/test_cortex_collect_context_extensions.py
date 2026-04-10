from __future__ import annotations

from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeLimbic:
    def __init__(self, registry: TribeStateRegistry) -> None:
        self.state_registry = registry

    def get_emotion_context(self, request_body=None):  # noqa: ANN001
        return {"emotion_tag": "CALM", "emotion_intensity": 0.4}


class _FakeSocial:
    def get_bond_context(self, *, session_id: str, user_id: str):  # noqa: ANN001
        return {
            "bond_stage": "friend",
            "bond_vector": {"trust": 0.7, "affinity": 0.68, "safety": 0.72, "repair_confidence": 0.63},
        }


class _FakeEndocrine:
    def resolve_vital_signs(self) -> dict:
        return {"gate_state": "open", "stress_index": 0.2, "energy_reserve": 0.78}


class _FakePain:
    def get_hedonic_state(self) -> dict:
        return {"hedonic_tone": "positive", "pain_level": 0.1, "reward_level": 0.7, "net_valence": 0.4}

    def get_loneliness_level(self) -> float:
        return 0.3


class _FakeImmune:
    def export_runtime_state(self) -> dict:
        return {"risk_level": "none", "block_rate": 0.0}


class _FakeSomatic:
    def export_runtime_state(self) -> dict:
        return {"fatigue_level": 0.2}


class _FakePeripheral:
    def export_runtime_state(self) -> dict:
        return {"connected_platforms": ["discord"]}


class _FakeCircadian:
    def export_runtime_state(self) -> dict:
        return {"phase": "day", "proactive_multiplier": 1.0}


class _FakeProprio:
    def export_runtime_state(self) -> dict:
        return {"cognitive_load_index": 0.22, "readiness_hint": 0.75, "continuity_hint": 0.66}


class _FakeInitiative:
    def get_last_decision(self) -> dict:
        return {"decision": "ALLOW", "reason_code": "PASS_ALL_GATES"}


class _FakeDefaultMode:
    def export_runtime_state(self) -> dict:
        return {"last_focus": "reflection", "continuity_score": 0.7}


class _FakeMirror:
    def export_runtime_state(self) -> dict:
        return {"last_emotion_tag": "SAD", "last_resonance_strength": 0.4}


class _FakeHippo:
    def resolve_memory_layer_snapshot(self) -> dict:
        return {"episodic": {"content": "x"}, "semantic": {"fact": "y"}, "autobiographical": {}, "last_write_type": "semantic"}


class _FakeLang:
    def __init__(self, registry: TribeStateRegistry) -> None:
        self.state_registry = registry

    def resolve_dual_system(self, **kwargs):  # noqa: ANN003
        return {"wernicke": {"intent_type": "question"}, "broca": {"response_mode": "direct_answer"}, "style": {"tone": "balanced"}}


class _FakeInteroception:
    def export_runtime_state(self) -> dict:
        return {"interoceptive_index": 0.31, "body_budget": 0.72}


class _FakeMotorCortex:
    def export_runtime_state(self) -> dict:
        return {"last_plan_id": "motor_plan_1", "last_dispatch_ok": True}


class _FakeCerebellum:
    def export_runtime_state(self) -> dict:
        return {"last_strategy": "NO_ADJUST", "last_urgency": 0.2}


class _FakeGlymphatic:
    def export_runtime_state(self) -> dict:
        return {"executed": True, "consolidation_score": 0.66}


class _FakeVestibular:
    def export_runtime_state(self) -> dict:
        return {"stability_state": "stable", "jitter_index": 0.18}


def test_cortex_collect_tribe_context_includes_extended_fields(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "open", "stress_index": 0.25, "energy_reserve": 0.81, "homeostasis_ok": True},
        writer="unit",
    )
    registry.set_state("limbic_agent", {"emotion_tag": "NEUTRAL", "mood_state": "steady", "silence_willingness": 0.5}, writer="unit")

    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: _FakeLimbic(registry))
    monkeypatch.setattr("host_brain.tribe.social_brain_agent.get_social_brain_agent", lambda: _FakeSocial())
    monkeypatch.setattr("host_brain.tribe.endocrine_agent.get_endocrine_agent", lambda: _FakeEndocrine())
    monkeypatch.setattr("host_brain.tribe.pain_reward_agent.get_pain_reward_agent", lambda: _FakePain())
    monkeypatch.setattr("host_brain.tribe.immune_agent.get_immune_agent", lambda: _FakeImmune())
    monkeypatch.setattr("host_brain.tribe.somatic_agent.get_somatic_agent", lambda: _FakeSomatic())
    monkeypatch.setattr("host_brain.tribe.peripheral_agent.get_peripheral_agent", lambda: _FakePeripheral())
    monkeypatch.setattr("host_brain.tribe.circadian_agent.get_circadian_agent", lambda: _FakeCircadian())
    monkeypatch.setattr("host_brain.tribe.proprioception_agent.get_proprioception_agent", lambda: _FakeProprio())
    monkeypatch.setattr("host_brain.tribe.initiative_governor_agent.get_initiative_governor_agent", lambda: _FakeInitiative())
    monkeypatch.setattr("host_brain.tribe.default_mode_agent.get_default_mode_agent", lambda: _FakeDefaultMode())
    monkeypatch.setattr("host_brain.tribe.mirror_neuron_agent.get_mirror_neuron_agent", lambda: _FakeMirror())
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_hippocampus_agent", lambda: _FakeHippo())
    monkeypatch.setattr("host_brain.tribe.language_system_agent.get_language_system_agent", lambda: _FakeLang(registry))
    monkeypatch.setattr("host_brain.tribe.interoception_agent.get_interoception_agent", lambda: _FakeInteroception())
    monkeypatch.setattr("host_brain.tribe.motor_cortex_agent.get_motor_cortex_agent", lambda: _FakeMotorCortex())
    monkeypatch.setattr("host_brain.tribe.cerebellum_agent.get_cerebellum_agent", lambda: _FakeCerebellum())
    monkeypatch.setattr("host_brain.tribe.glymphatic_agent.get_glymphatic_agent", lambda: _FakeGlymphatic())
    monkeypatch.setattr("host_brain.tribe.vestibular_agent.get_vestibular_agent", lambda: _FakeVestibular())

    context = CortexAgent(state_registry=registry).collect_tribe_context(
        request_body={"user_message": "为什么我有点焦虑"},
        session_id="sess-cortex",
        user_id="u-cortex",
    )

    assert context["default_mode_state"]["last_focus"] == "reflection"
    assert context["mirror_neuron_state"]["last_emotion_tag"] == "SAD"
    assert context["memory_layers"]["last_write_type"] == "semantic"
    assert "wernicke" in context["language_dual_system"]
    assert "homeostasis_report" in context
    assert context["interoception_state"]["interoceptive_index"] == 0.31
    assert context["motor_cortex_state"]["last_plan_id"] == "motor_plan_1"
    assert context["cerebellum_state"]["last_strategy"] == "NO_ADJUST"
    assert context["glymphatic_state"]["executed"] is True
    assert context["vestibular_state"]["stability_state"] == "stable"
