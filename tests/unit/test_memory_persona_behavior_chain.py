from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

import host_brain.app.chat_context_builder as ccb
from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_TOPIC_FOLLOWUP
from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy


def _ts_at_hour(hour: int, minute: int = 0) -> int:
    return int(datetime.datetime(2026, 4, 4, hour, minute, 0).timestamp() * 1000)


def test_response_policy_memory_signal_restrains_followup_and_proactive() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.93, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            motive_state={
                "care_drive": 0.36,
                "continuity_drive": 0.32,
                "withdraw_drive": 0.54,
                "self_protect_drive": 0.76,
            },
            memory_persona_signal={
                "suppressed_memory_count": 1,
                "distorted_memory_count": 1,
                "avg_distortion_level": 0.56,
                "dominant_memory_emotion": "HURT",
                "recall_bias": "guarded",
            },
        )
    ).as_dict()

    assert policy["allow_followup"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["should_be_withdrawn"] is True
    assert policy["withdrawal_guard"] is True
    assert "memory:suppressed=1" in policy["policy_reason_tags"]
    assert any(tag.startswith("memory:fragile=") for tag in policy["policy_reason_tags"])
    assert "memory:protective_guard" in policy["policy_reason_tags"]


def test_response_policy_warm_memory_promotes_warmer_continuity() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.95, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            motive_state={
                "care_drive": 0.64,
                "continuity_drive": 0.71,
                "withdraw_drive": 0.12,
                "self_protect_drive": 0.08,
            },
            memory_persona_signal={
                "suppressed_memory_count": 0,
                "distorted_memory_count": 0,
                "avg_distortion_level": 0.12,
                "dominant_memory_emotion": "WARM",
                "recall_bias": "warm",
            },
        )
    ).as_dict()

    assert policy["allow_followup"] is True
    assert policy["min_sentences"] >= 2
    assert policy["warmth_floor"] >= 0.22
    assert "memory:warm=WARM" in policy["policy_reason_tags"]


def test_chat_context_builder_passes_memory_signal_into_response_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ccb,
        "build_chat_generation_prompt_payload",
        lambda **kwargs: {
            "prompt_context": {
                "authoritative": [],
                "retrieval": [],
                "style_pack": {},
                "constraints": {"retrieval_status": "OK"},
                "memory_emotion_hint": {
                    "mood_state": "steady",
                    "emotion_tag": "SAD",
                    "humanized_memory_count": 2,
                    "suppressed_memory_count": 1,
                    "distorted_memory_count": 1,
                    "avg_distortion_level": 0.48,
                    "dominant_memory_emotion": "HURT",
                    "recall_bias": "guarded",
                },
                "memory_recall_summary": "记忆需要轻触试探。",
            },
            "prompt_context_validation": {"valid": True, "reason_code": None, "violation_count": 0},
            "prompt_context_observability": {"source_stats": {}},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "SAD",
                "emotion_intensity": 0.75,
                "emotion_range_hint": "needs support",
            },
            "user_message": kwargs["user_message"],
        },
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.build_prompt_intelligence",
        lambda **_kwargs: {},
    )

    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):  # noqa: ANN001
            hint = SimpleNamespace(
                style_hint="respond naturally",
                tone="gentle",
                echo_weight_modifier=0.0,
                pace_hint="steady",
            )
            setattr(hint, "追问倾向", 0.0)
            return hint

    class _FakePersonaEngine:
        @staticmethod
        def get_instance():
            return SimpleNamespace(get_traits=lambda _user_id: SimpleNamespace())

    class _FakeMotiveState:
        def as_dict(self):
            return {
                "care_drive": 0.42,
                "reach_out_drive": 0.2,
                "continuity_drive": 0.3,
                "withdraw_drive": 0.18,
                "self_protect_drive": 0.22,
                "dominant_motive": "care",
            }

    captured: dict[str, object] = {}

    class _FakePolicy:
        def as_dict(self):
            return {"allow_followup": False, "max_sentences": 2}

    def _fake_decide_response_policy(inp):  # noqa: ANN001
        captured["memory_persona_signal"] = dict(inp.memory_persona_signal or {})
        return _FakePolicy()

    monkeypatch.setattr("host_brain.persona.expression_adapter.PersonaExpressionAdapter", _FakeAdapter)
    monkeypatch.setattr("host_brain.persona.persona_engine.PersonaEngine", _FakePersonaEngine)
    monkeypatch.setattr("host_brain.persona.motive_engine.compute_motive_state", lambda _inp: _FakeMotiveState())
    monkeypatch.setattr("host_brain.persona.response_policy.decide_response_policy", _fake_decide_response_policy)

    runtime_input = build_chat_runtime_input(
        request_id="req-memory-policy-pass",
        person_id="person-memory-policy-pass",
        session_id="session-memory-policy-pass",
        thread_id=None,
        user_message="我又想起那件事了",
        render_target_lang="zh",
        retrieval_summary={"status": "OK"},
        retrieval_evidence_items=[],
        emotion_state={"mood_state": "steady", "emotion_tag": "SAD", "emotion_intensity": 0.75},
        recall_slot_text={},
        bond_summary={},
        scene_type="CHAT",
    )

    ccb.build_chat_prompt_runtime_payload(
        runtime_input=runtime_input,
        style_bindings={},
    )

    assert dict(captured.get("memory_persona_signal") or {})["suppressed_memory_count"] == 1
    assert dict(captured.get("memory_persona_signal") or {})["dominant_memory_emotion"] == "HURT"


def test_proactive_voice_suppressed_memory_blocks_topic_followup() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-guarded-block",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "面试", "topic_keywords": ["面试"]}],
            "motive_state": {
                "reach_out_drive": 0.54,
                "care_drive": 0.48,
                "continuity_drive": 0.74,
                "withdraw_drive": 0.32,
                "self_protect_drive": 0.46,
                "dominant_motive": "continuity",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 1,
                "avg_distortion_level": 0.22,
                "dominant_memory_emotion": "HURT",
                "recall_bias": "guarded",
            },
        },
    )

    assert result is None


def test_proactive_voice_fragile_memory_softens_topic_followup_content() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-fragile-soften",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "手术", "topic_keywords": ["手术"]}],
            "motive_state": {
                "reach_out_drive": 0.62,
                "care_drive": 0.84,
                "continuity_drive": 0.78,
                "withdraw_drive": 0.12,
                "self_protect_drive": 0.16,
                "dominant_motive": "care",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 0,
                "avg_distortion_level": 0.24,
                "dominant_memory_emotion": "ANXIOUS",
                "recall_bias": "guarded",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "手术" in result.content
    assert "如果你现在愿意聊的话" in result.content
    assert "memory:guarded_soften_followup" in result.motive_influence


def test_proactive_voice_warm_memory_makes_followup_more_natural() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at_hour(15, 0)

    result = voice.evaluate_scene(
        user_id="memory-warm-followup",
        context={
            "current_ts_ms": ts,
            "user_online": False,
            "last_interaction_ts_ms": ts - 1000,
            "pending_followup_topics": [{"topic_label": "搬家", "topic_keywords": ["搬家"]}],
            "motive_state": {
                "reach_out_drive": 0.58,
                "care_drive": 0.66,
                "continuity_drive": 0.82,
                "withdraw_drive": 0.08,
                "self_protect_drive": 0.08,
                "dominant_motive": "continuity",
            },
            "memory_persona_signal": {
                "suppressed_memory_count": 0,
                "avg_distortion_level": 0.12,
                "dominant_memory_emotion": "WARM",
                "recall_bias": "warm",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "搬家" in result.content
    assert "后来有新进展吗" in result.content
    assert "memory:warm_followup=WARM" in result.motive_influence
