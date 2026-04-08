from __future__ import annotations

from host_brain.persona import inner_activity_agent as agent


def test_extract_llm_json_payload_handles_wrapped_text() -> None:
    payload = agent.extract_llm_json_payload(
        'prefix {"note":"我先把情绪收束一下","focus":"state"} suffix'
    )
    assert payload["note"] == "我先把情绪收束一下"
    assert payload["focus"] == "state"


def test_generate_inner_activity_note_success(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_INNER_ACTIVITY_LLM_ENABLED", True, raising=False)
    monkeypatch.setattr(
        agent,
        "_resolve_inner_activity_llm_runtime",
        lambda **_kwargs: {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "llm_api_key": "mock-key",
            "timeout_ms": 1200,
            "agent_gen_params": {"temperature": 0.5},
        },
    )
    monkeypatch.setattr(
        agent,
        "_build_inner_activity_context",
        lambda **_kwargs: {
            "offline_state": {"absence_band": "recent", "offline_hours": 0.3},
            "silence_state": {},
            "body_sense": {},
            "bond_summary": {},
            "emotion_timeline": {},
            "motive_state": {},
            "response_policy": {},
            "self_model_snapshot": {},
            "self_reflection_pulse": {},
            "recent_inner_events": [],
        },
    )
    monkeypatch.setattr(
        agent,
        "_run_inner_activity_llm",
        lambda **_kwargs: '{"note":"我先把节奏放慢一点","focus":"plan"}',
    )

    note, focus, reason, meta = agent.generate_inner_activity_note(
        persona_user_id="user_1",
        absence_band="recent",
        offline_hours=0.3,
        inner_state={},
        now_ms=1776000000000,
        topic_phrase="",
    )
    assert note == "我先把节奏放慢一点。"
    assert focus == "plan"
    assert reason == ""
    assert "context_scope" in meta


def test_generate_inner_activity_note_rejects_duplicate(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_INNER_ACTIVITY_LLM_ENABLED", True, raising=False)
    monkeypatch.setattr(
        agent,
        "_resolve_inner_activity_llm_runtime",
        lambda **_kwargs: {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "llm_api_key": "mock-key",
            "timeout_ms": 1200,
        },
    )
    monkeypatch.setattr(
        agent,
        "_build_inner_activity_context",
        lambda **_kwargs: {
            "offline_state": {"absence_band": "recent", "offline_hours": 0.3},
            "silence_state": {},
            "body_sense": {},
            "bond_summary": {},
            "emotion_timeline": {},
            "motive_state": {},
            "response_policy": {},
            "self_model_snapshot": {},
            "self_reflection_pulse": {},
            "recent_inner_events": [{"note": "我先把节奏放慢一点。"}],
        },
    )
    monkeypatch.setattr(
        agent,
        "_run_inner_activity_llm",
        lambda **_kwargs: '{"note":"我先把节奏放慢一点","focus":"plan"}',
    )

    note, focus, reason, _meta = agent.generate_inner_activity_note(
        persona_user_id="user_1",
        absence_band="recent",
        offline_hours=0.3,
        inner_state={},
        now_ms=1776000000000,
        topic_phrase="",
    )
    assert note == ""
    assert focus == ""
    assert reason == "INNER_ACTIVITY_LLM_DUPLICATE"


def test_build_inner_activity_context_contains_required_soul_layers(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "build_offline_state_from_inner_state",
        lambda _inner_state, now_ms=None: {
            "absence_band": "24h_plus",
            "offline_hours": 24.0,
            "loneliness_level": 0.45,
            "anticipation_level": 0.55,
            "anticipation_topics": ["上次没聊完的话题"],
            "continuity_score": 0.6,
            "last_spontaneous_source": "llm",
        },
    )
    monkeypatch.setattr(
        agent,
        "_collect_silence_state",
        lambda **_kwargs: {"response_willingness": 0.8, "willingness_level": "NORMAL"},
    )
    monkeypatch.setattr(
        agent,
        "_collect_body_sense",
        lambda: {"fatigue_level": 0.2, "energy_level": 0.7},
    )
    monkeypatch.setattr(
        agent,
        "_collect_bond_context",
        lambda **_kwargs: ({"stage": "CLOSE_FRIEND", "score": 0.76}, object()),
    )
    monkeypatch.setattr(
        agent,
        "_collect_emotion_timeline",
        lambda: ({"dominant_emotion": "CALM"}, {"avg_valence": 0.2}, "CALM"),
    )
    monkeypatch.setattr(
        agent,
        "compute_runtime_motive_state",
        lambda **_kwargs: {"dominant_motive": "continuity", "continuity_drive": 0.8},
    )
    monkeypatch.setattr(
        agent,
        "_decide_response_policy",
        lambda **_kwargs: {"max_chars": 140, "allow_followup": True},
    )
    monkeypatch.setattr(
        agent,
        "_collect_persona_changes",
        lambda **_kwargs: [{"kind": "style"}],
    )
    monkeypatch.setattr(
        agent,
        "_collect_self_model_snapshot",
        lambda **_kwargs: {"recent_mood_trend": "stable", "bond_growth_perception": "closer"},
    )
    monkeypatch.setattr(
        agent,
        "_collect_self_reflection_pulse",
        lambda **_kwargs: {"self_awareness": "我在逐步稳定自己的表达"},
    )

    context = agent._build_inner_activity_context(
        persona_user_id="user_1",
        absence_band="24h_plus",
        offline_hours=24.0,
        inner_state={"inner_stream": [{"note": "旧内心流", "focus": "self"}]},
        now_ms=1776000000000,
        topic_phrase="上次没聊完的话题",
    )
    assert set(context.keys()) == {
        "offline_state",
        "silence_state",
        "body_sense",
        "bond_summary",
        "emotion_timeline",
        "motive_state",
        "response_policy",
        "self_model_snapshot",
        "self_reflection_pulse",
        "recent_inner_events",
    }
    assert context["offline_state"]["absence_band"] == "24h_plus"
    assert context["motive_state"]["dominant_motive"] == "continuity"
    assert context["self_reflection_pulse"]["self_awareness"] == "我在逐步稳定自己的表达"
