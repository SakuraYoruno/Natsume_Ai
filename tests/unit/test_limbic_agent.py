from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.persona.reply_plan import build_reply_plan_from_inputs
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.limbic_agent import LimbicAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


def test_emit_tribe_event_publishes_to_internal_bus(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Bus:
        def publish(self, event) -> None:
            captured["event"] = event

    monkeypatch.setattr(
        "host_brain.app.internal_tribe_routes.get_tribe_event_bus",
        lambda: _Bus(),
    )

    row = emit_tribe_event(
        event_type="body_sense_updated",
        source_agent="ans_agent",
        target_agent="limbic_agent",
        payload={"loneliness": 0.4},
    )

    assert row is not None
    assert row["event_type"] == "body_sense_updated"
    assert row["source_agent"] == "ans_agent"
    assert captured["event"].event_type == "body_sense_updated"


def test_emit_tribe_event_returns_none_on_publish_error(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("bus unavailable")

    monkeypatch.setattr(
        "host_brain.app.internal_tribe_routes.get_tribe_event_bus",
        _boom,
    )

    row = emit_tribe_event(event_type="x", source_agent="y")
    assert row is None


def test_limbic_agent_get_emotion_context_updates_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.emotion_context.resolve_chat_emotion_context",
        lambda request_body: {"emotion_tag": "HAPPY", "mood_state": "warm"},
    )
    agent = LimbicAgent()

    context = agent.get_emotion_context(request_body={"message": "hi"})

    assert context["emotion_tag"] == "HAPPY"
    state = agent.state_registry.get_state("limbic") or {}
    last = dict((state.get("state") or {}).get("last_emotion_context") or {})
    assert last["emotion_tag"] == "HAPPY"
    assert last["mood_state"] == "warm"


def test_limbic_agent_build_snapshot_updates_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.emotion_context.build_chat_emotion_snapshot",
        lambda emotion_context, source, ts_ms: {
            "source": source,
            "emotion_context": {"emotion_tag": "SAD"},
            "ts_ms": ts_ms,
        },
    )
    agent = LimbicAgent()

    snapshot = agent.build_emotion_snapshot(
        emotion_context={"emotion_tag": "CALM"},
        source="chat",
        ts_ms=123456,
    )

    assert snapshot["emotion_context"]["emotion_tag"] == "SAD"
    state = agent.state_registry.get_state("limbic") or {}
    last = dict((state.get("state") or {}).get("last_emotion_snapshot") or {})
    assert last["source"] == "chat"
    assert last["emotion_tag"] == "SAD"
    assert last["ts_ms"] == 123456


def test_limbic_agent_keeps_asr_text_emotion_hint_untrusted(monkeypatch) -> None:
    class _HardState:
        emotion_tag = "NEUTRAL"
        emotion_intensity = 0.0
        mood_state = "steady"

    class _Timeline:
        def __init__(self) -> None:
            self.rows: list[dict[str, object]] = []

        def append(self, **kwargs):  # noqa: ANN003
            self.rows.append(dict(kwargs))

    hard_state = _HardState()
    timeline = _Timeline()
    monkeypatch.setattr("host_brain.app.dependencies.get_hard_state", lambda: hard_state)
    monkeypatch.setattr("host_brain.app.dependencies.get_emotion_timeline", lambda: timeline)
    monkeypatch.setattr("host_brain.tribe.edge_command_pusher.get_edge_command_pusher", lambda: None)
    registry = TribeStateRegistry(persist_throttle=999999)
    agent = LimbicAgent(state_registry=registry)

    event = SimpleNamespace(
        payload={
            "emotion_tag": "SAD",
            "emotion_intensity": 0.9,
            "mood_state": "sad",
            "emotion_source": "asr_text_bootstrap",
            "emotion_modality": "asr_text",
            "emotion_confidence": 0.55,
            "emotion_inference_degraded": False,
            "acoustic_emotion_available": False,
            "session_id": "sess-asr",
        },
        source_agent="somatic_agent",
        ts_ms=123,
    )

    asyncio.run(agent._on_emotion_inference_updated(event))

    assert hard_state.emotion_tag == "NEUTRAL"
    assert hard_state.emotion_intensity == 0.0
    assert timeline.rows == []
    limbic_state = dict((registry.get_state("limbic") or {}).get("state") or {})
    hint = dict(limbic_state["last_voice_emotion_inference"])
    assert hint["emotion_tag"] == "SAD"
    assert hint["trusted_runtime_emotion"] is False
    vitals = registry.aggregate_vital_signs()
    assert vitals["voice_emotion_tag"] is None
    assert vitals["voice_emotion_trusted"] is False
    assert vitals["voice_emotion_source"] == "asr_text_bootstrap"


def test_limbic_agent_accepts_explicit_or_acoustic_runtime_emotion(monkeypatch) -> None:
    class _HardState:
        emotion_tag = "NEUTRAL"
        emotion_intensity = 0.0
        mood_state = "steady"

    class _Timeline:
        def __init__(self) -> None:
            self.rows: list[dict[str, object]] = []

        def append(self, **kwargs):  # noqa: ANN003
            self.rows.append(dict(kwargs))

    hard_state = _HardState()
    timeline = _Timeline()
    monkeypatch.setattr("host_brain.app.dependencies.get_hard_state", lambda: hard_state)
    monkeypatch.setattr("host_brain.app.dependencies.get_emotion_timeline", lambda: timeline)
    monkeypatch.setattr("host_brain.tribe.edge_command_pusher.get_edge_command_pusher", lambda: None)
    registry = TribeStateRegistry(persist_throttle=999999)
    agent = LimbicAgent(state_registry=registry)

    event = SimpleNamespace(
        payload={
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.82,
            "mood_state": "warm",
            "emotion_source": "edge_explicit",
            "emotion_modality": "explicit",
            "emotion_confidence": 1.0,
            "emotion_inference_degraded": False,
            "acoustic_emotion_available": False,
            "session_id": "sess-explicit",
        },
        source_agent="somatic_agent",
        ts_ms=456,
    )

    asyncio.run(agent._on_emotion_inference_updated(event))

    assert hard_state.emotion_tag == "HAPPY"
    assert hard_state.emotion_intensity == 0.82
    assert hard_state.mood_state == "warm"
    assert timeline.rows[-1]["emotion_tag"] == "HAPPY"
    limbic_state = dict((registry.get_state("limbic") or {}).get("state") or {})
    hint = dict(limbic_state["last_voice_emotion_inference"])
    assert hint["trusted_runtime_emotion"] is True
    vitals = registry.aggregate_vital_signs()
    assert vitals["voice_emotion_tag"] == "HAPPY"
    assert vitals["voice_emotion_intensity"] == 0.82
    assert vitals["voice_emotion_trusted"] is True


def test_limbic_agent_handle_signal_records_state_and_event(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _emit(**kwargs):
        captured["kwargs"] = kwargs
        return {"event_id": "evt_1"}

    monkeypatch.setattr("host_brain.tribe.limbic_agent.emit_tribe_event", _emit)
    agent = LimbicAgent()

    result = agent.handle_signal(
        event_type="body_sense_updated",
        payload={"loneliness": 0.3},
        source_agent="ans_agent",
        target_agent="limbic_agent",
    )

    assert result["accepted"] is True
    assert result["event_type"] == "BODY_SENSE_UPDATED"
    state = agent.state_registry.get_state("limbic") or {}
    payload_state = dict(state.get("state") or {})
    assert payload_state["signal_count"] == 1
    assert payload_state["last_signal_event_type"] == "BODY_SENSE_UPDATED"
    assert payload_state["recent_signals"][-1]["payload"]["loneliness"] == 0.3
    assert captured["kwargs"]["event_type"] == "BODY_SENSE_UPDATED"
    assert captured["kwargs"]["source_agent"] == "ans_agent"


def test_limbic_agent_on_conversation_started_normalizes_payload(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def _emit(**kwargs):
        calls.append(kwargs)
        return {"event_id": "evt_2"}

    monkeypatch.setattr("host_brain.tribe.limbic_agent.emit_tribe_event", _emit)
    agent = LimbicAgent()

    result = agent.on_conversation_started(
        request_id=" req_1 ",
        session_id="sess_1",
        person_id=" user_1 ",
        emotion_tag="happy",
        mood_state="Calm",
    )

    assert result["accepted"] is True
    assert result["event_type"] == "CONVERSATION_STARTED"
    payload = dict(calls[-1]["payload"] or {})
    assert payload["request_id"] == "req_1"
    assert payload["person_id"] == "user_1"
    assert payload["emotion_tag"] == "HAPPY"
    assert payload["mood_state"] == "calm"


def test_limbic_agent_resolve_emotion_timeline_snapshot(monkeypatch) -> None:
    class _Timeline:
        def rebuild_summary(self):
            return {"dominant_emotion": "calm", "valence": 0.2}

        def build_daily_diary(self):
            return {"events": 3}

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_emotion_timeline",
        lambda: _Timeline(),
    )
    agent = LimbicAgent()

    snapshot = agent.resolve_emotion_timeline_snapshot()

    assert snapshot["dominant_emotion"] == "CALM"
    assert snapshot["summary"]["valence"] == 0.2
    assert snapshot["diary"]["events"] == 3
    state = agent.state_registry.get_state("limbic") or {}
    last = dict((state.get("state") or {}).get("last_emotion_timeline") or {})
    assert last["dominant_emotion"] == "CALM"
    assert last["summary_size"] == 2


def test_limbic_agent_resolve_emotion_timeline_snapshot_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_emotion_timeline",
        lambda: None,
    )
    agent = LimbicAgent()

    snapshot = agent.resolve_emotion_timeline_snapshot()

    assert snapshot == {
        "summary": {},
        "diary": {},
        "dominant_emotion": "NEUTRAL",
    }


def test_limbic_agent_resolve_emotion_timeline_snapshot_with_windows(monkeypatch) -> None:
    captured: dict[str, int] = {}

    class _Timeline:
        def rebuild_summary(self, *, window_ms: int):
            captured["summary_window_ms"] = int(window_ms)
            return {"entry_count": 2}

        def build_daily_diary(self, *, window_ms: int):
            captured["diary_window_ms"] = int(window_ms)
            return {"dominant_emotion": "sad"}

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_emotion_timeline",
        lambda: _Timeline(),
    )
    agent = LimbicAgent()

    snapshot = agent.resolve_emotion_timeline_snapshot(
        summary_window_ms=12345,
        diary_window_ms=67890,
    )

    assert captured["summary_window_ms"] == 12345
    assert captured["diary_window_ms"] == 67890
    assert snapshot["dominant_emotion"] == "SAD"


def test_limbic_agent_resolve_persistent_emotion_state(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Restored:
        from_baseline = False
        valence = 0.8
        arousal = 0.6
        dominance = 0.2
        reunion_type = "reunion"

    class _EmotionPersistence:
        def restore(self, *, user_id: str):
            captured["user_id"] = user_id
            return _Restored()

        def persist(self, *, user_id: str, valence: float, arousal: float, dominance: float) -> None:
            captured["persist"] = {
                "user_id": user_id,
                "valence": valence,
                "arousal": arousal,
                "dominance": dominance,
            }

    monkeypatch.setattr(
        "host_brain.persona.emotion_persistence.EmotionPersistence",
        _EmotionPersistence,
    )
    # 隔离 PainRewardAgent 的持久化历史状态，确保该用例只验证 EmotionPersistence 混合逻辑
    class _PainAgent:
        def get_emotion_modulation(self):
            return {
                "valence_bias": 0.0,
                "arousal_boost": 0.0,
                "suggested_emotion_tag": None,
            }

    monkeypatch.setattr(
        "host_brain.tribe.pain_reward_agent.get_pain_reward_agent",
        lambda: _PainAgent(),
    )
    agent = LimbicAgent()

    payload = agent.resolve_persistent_emotion_state(
        person_id="user-ep",
        valence=0.2,
        arousal=0.4,
        dominance=0.1,
    )

    assert captured["user_id"] == "user-ep"
    assert payload["reunion_type"] == "reunion"
    assert round(payload["valence"], 4) == 0.38
    assert round(payload["arousal"], 4) == 0.46
    assert round(payload["dominance"], 4) == 0.13
    persisted = dict(captured["persist"] or {})
    assert round(float(persisted["valence"]), 4) == 0.38
    registry = agent.state_registry.get_state("limbic") or {}
    last = dict((registry.get("state") or {}).get("last_emotion_persistence") or {})
    assert last["user_id"] == "user-ep"
    assert last["reunion_type"] == "reunion"


def test_limbic_agent_resolve_persistent_emotion_state_with_pain_modulation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Restored:
        from_baseline = False
        valence = 0.8
        arousal = 0.6
        dominance = 0.2
        reunion_type = "reunion"

    class _EmotionPersistence:
        def restore(self, *, user_id: str):
            captured["user_id"] = user_id
            return _Restored()

        def persist(self, *, user_id: str, valence: float, arousal: float, dominance: float) -> None:
            captured["persist"] = {
                "user_id": user_id,
                "valence": valence,
                "arousal": arousal,
                "dominance": dominance,
            }

    class _PainAgent:
        def get_emotion_modulation(self):
            return {
                "valence_bias": -0.4,
                "arousal_boost": 0.8,
                "suggested_emotion_tag": "SAD",
            }

    monkeypatch.setattr(
        "host_brain.persona.emotion_persistence.EmotionPersistence",
        _EmotionPersistence,
    )
    monkeypatch.setattr(
        "host_brain.tribe.pain_reward_agent.get_pain_reward_agent",
        lambda: _PainAgent(),
    )
    agent = LimbicAgent()

    payload = agent.resolve_persistent_emotion_state(
        person_id="user-ep",
        valence=0.2,
        arousal=0.4,
        dominance=0.1,
    )

    assert captured["user_id"] == "user-ep"
    assert round(payload["valence"], 4) == 0.24
    assert round(payload["arousal"], 4) == 0.61
    assert round(payload["dominance"], 4) == 0.13
    assert payload["pain_valence_bias"] == -0.14
    assert payload["pain_arousal_boost"] == 0.15
    assert payload["pain_suggested_emotion_tag"] == "SAD"
    persisted = dict(captured["persist"] or {})
    assert round(float(persisted["valence"]), 4) == 0.24
    assert round(float(persisted["arousal"]), 4) == 0.61


def test_limbic_agent_resolve_offline_reconnect_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.offline_continuity.resolve_absence_band",
        lambda offline_state: "24h_plus" if offline_state.get("offline_hours", 0) >= 24 else "recent",
    )
    monkeypatch.setattr(
        "host_brain.persona.offline_continuity.reconnect_style_for_absence_band",
        lambda absence_band: "reunion" if absence_band == "24h_plus" else "soft_resume",
    )
    agent = LimbicAgent()

    payload = agent.resolve_offline_reconnect_profile(
        offline_state={"offline_hours": 36},
    )

    assert payload == {
        "absence_band": "24h_plus",
        "reconnect_style": "reunion",
    }
    registry = agent.state_registry.get_state("limbic") or {}
    last = dict((registry.get("state") or {}).get("last_offline_reconnect_profile") or {})
    assert last == payload


def test_limbic_agent_resolve_emotion_reactivity_profile(monkeypatch) -> None:
    class _Modifiers:
        def as_dict(self):
            return {"sensitivity_bias": 0.2}

    class _EmotionReactivityProfile:
        @staticmethod
        def compute_modifiers(traits, emotion_tag: str, stage: str):  # noqa: ANN001
            assert emotion_tag == "SAD"
            assert stage == "FRIEND"
            assert traits == {"sensitivity": 0.8}
            return _Modifiers()

        @staticmethod
        def apply_to_echo_weight(echo_weight: float, modifiers):  # noqa: ANN001
            assert echo_weight == 0.4
            assert modifiers.__class__.__name__ == "_Modifiers"
            return 0.55

    monkeypatch.setattr(
        "host_brain.persona.emotion_reactivity.EmotionReactivityProfile",
        _EmotionReactivityProfile,
    )
    agent = LimbicAgent()

    payload = agent.resolve_emotion_reactivity_profile(
        personality_traits={"sensitivity": 0.8},
        user_emotion_tag="SAD",
        bond_stage="FRIEND",
        echo_weight=0.4,
    )

    assert payload["effective_echo_weight"] == 0.55
    assert payload["persona_modifiers"]["sensitivity_bias"] == 0.2
    registry = agent.state_registry.get_state("limbic") or {}
    last = dict((registry.get("state") or {}).get("last_emotion_reactivity_profile") or {})
    assert last["bond_stage"] == "FRIEND"
    assert last["user_emotion_tag"] == "SAD"


def test_limbic_agent_resolve_silence_state_and_apply_penalty(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _SilenceState:
        def as_dict(self):
            return {"willingness_level": "LOW", "response_willingness": 0.2}

    class _SilenceEngine:
        def get_silence_state(self, *, current_topic: str):
            captured["current_topic"] = current_topic
            return _SilenceState()

        def apply_body_sense_penalty(self, penalty: float):
            captured["penalty"] = penalty

    monkeypatch.setattr(
        "host_brain.persona.emotional_silence.get_silence_engine",
        lambda: _SilenceEngine(),
    )
    agent = LimbicAgent()

    state = agent.resolve_silence_state(current_topic="topic-a")
    agent.apply_body_sense_penalty(penalty=0.35)

    assert state["willingness_level"] == "LOW"
    assert captured["current_topic"] == "topic-a"
    assert captured["penalty"] == 0.35
    registry = agent.state_registry.get_state("limbic") or {}
    last = dict((registry.get("state") or {}).get("last_silence_state") or {})
    assert last["willingness_level"] == "LOW"


def test_limbic_agent_resolve_body_sense_state(monkeypatch) -> None:
    class _BodyState:
        def as_dict(self):
            return {
                "fatigue_level": "mild",
                "drowsiness_level": "none",
                "loneliness_level": "low",
            }

    class _BodyModel:
        def get_state(self):
            return _BodyState()

    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: _BodyModel(),
    )
    agent = LimbicAgent()

    state = agent.resolve_body_sense_state()

    assert state["fatigue_level"] == "mild"
    registry = agent.state_registry.get_state("limbic") or {}
    last = dict((registry.get("state") or {}).get("last_body_sense_state") or {})
    assert last["fatigue_level"] == "mild"


def test_limbic_agent_capture_conversation_body_sense(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _BodyState:
        def as_dict(self):
            return {
                "fatigue": 0.2,
                "drowsiness": 0.1,
                "loneliness": 0.5,
                "willingness_penalty": 0.35,
                "is_active": True,
                "fatigue_level": "TIRED",
                "drowsiness_level": "SLEEPY",
                "loneliness_level": "LONGING",
            }

    class _BodyModel:
        def update_loneliness(self, *, loneliness_value: float):
            captured["loneliness_value"] = loneliness_value

        def on_conversation_start(self):
            captured["conversation_started"] = True

        def get_state(self):
            return _BodyState()

    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: _BodyModel(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.LimbicAgent.on_conversation_started",
        lambda self, **kwargs: captured.setdefault("conversation_payload", kwargs),
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.LimbicAgent.handle_signal",
        lambda self, **kwargs: captured.setdefault("signal_payload", kwargs),
    )
    agent = LimbicAgent()

    result = agent.capture_conversation_body_sense(
        request_id=" req-1 ",
        session_id="sess-1",
        person_id=" user-1 ",
        emotion_tag="happy",
        mood_state="calm",
        offline_loneliness=0.5,
    )

    assert captured["loneliness_value"] == 0.5
    assert captured["conversation_started"] is True
    assert captured["conversation_payload"]["request_id"] == " req-1 "
    assert captured["signal_payload"]["event_type"] == "BODY_SENSE_UPDATED"
    assert result["is_active"] is True
    assert result["willingness_penalty"] == 0.35
    assert result["body_sense"]["fatigue_level"] == "TIRED"


def test_limbic_agent_on_conversation_ended(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _BodyState:
        def as_dict(self):
            return {
                "willingness_penalty": 0.18,
                "fatigue_level": "FRESH",
                "drowsiness_level": "ALERT",
                "loneliness_level": "FINE",
            }

    class _BodyModel:
        def on_conversation_end(self, *, duration_minutes=None):
            captured["duration_minutes"] = duration_minutes

        def get_state(self):
            return _BodyState()

    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: _BodyModel(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.LimbicAgent.handle_signal",
        lambda self, **kwargs: captured.setdefault("signal_payload", kwargs),
    )
    agent = LimbicAgent()

    result = agent.on_conversation_ended(
        request_id="req-2",
        session_id="sess-2",
        person_id="user-2",
        duration_minutes=12,
    )

    assert captured["duration_minutes"] == 12.0
    assert captured["signal_payload"]["event_type"] == "CONVERSATION_ENDED"
    assert result["status"] == "OK"
    assert result["body_sense"]["fatigue_level"] == "FRESH"


def test_limbic_agent_resolve_inner_world_and_offline_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.inner_world.get_inner_world",
        lambda: type(
            "_Inner",
            (),
            {
                "get_state": lambda self, user_id: {
                    "user_id": user_id,
                    "total_offline_hours": 2.5,
                    "anticipation_topics": ["topic-a"],
                }
            },
        )(),
    )
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda _state, now_ms=None: {
            "offline_hours": 2.5,
            "absence_band": "recent",
            "now_ms": now_ms,
        },
    )
    agent = LimbicAgent()

    inner_state = agent.resolve_inner_world_state(person_id="user-3")
    offline_state = agent.resolve_offline_state(person_id="user-3", now_ms=123)

    assert inner_state["user_id"] == "user-3"
    assert inner_state["total_offline_hours"] == 2.5
    assert offline_state["offline_hours"] == 2.5
    assert offline_state["absence_band"] == "recent"
    registry = agent.state_registry.get_state("limbic") or {}
    state_payload = dict(registry.get("state") or {})
    assert dict(state_payload.get("last_offline_state") or {})["offline_hours"] == 2.5


def test_limbic_agent_on_user_awaken_and_session_end(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Snapshot:
        def as_dict(self):
            return {
                "offline_hours": 6.5,
                "absence_band": "6h_plus",
                "awakening_hint": "hint",
            }

    class _InnerWorld:
        def on_user_awaken(self, *, user_id: str):
            captured["awaken_user"] = user_id
            return _Snapshot()

        def on_session_end(self, user_id, **kwargs):  # noqa: ANN001, ANN003
            captured["session_end"] = {"user_id": user_id, **kwargs}

        def get_state(self, user_id: str):
            return {"user_id": user_id, "total_offline_hours": 1.0}

    monkeypatch.setattr(
        "host_brain.persona.inner_world.get_inner_world",
        lambda: _InnerWorld(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.limbic_agent.LimbicAgent.handle_signal",
        lambda self, **kwargs: captured.setdefault("signal_payload", kwargs),
    )
    agent = LimbicAgent()

    awaken_payload = agent.on_user_awaken(person_id="user-awaken")
    session_payload = agent.on_session_end(
        person_id="user-awaken",
        valence=0.2,
        arousal=0.4,
        pending_topics=["topic-1"],
    )

    assert captured["awaken_user"] == "user-awaken"
    assert awaken_payload["absence_band"] == "6h_plus"
    assert captured["session_end"]["user_id"] == "user-awaken"
    assert captured["signal_payload"]["event_type"] == "SESSION_ENDED"
    assert session_payload["user_id"] == "user-awaken"


def test_limbic_agent_compute_runtime_motive_state(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **kwargs: {
            "dominant_motive": "reach_out",
            "reach_out_drive": 0.72,
            "continuity_drive": 0.58,
            "received_persona_user_id": kwargs.get("persona_user_id"),
        },
    )
    agent = LimbicAgent()

    result = agent.compute_runtime_motive_state(
        persona_user_id="user-motive",
        current_topic="topic-motive",
        runtime_flags={"source": "test"},
    )

    assert result["dominant_motive"] == "reach_out"
    assert result["received_persona_user_id"] == "user-motive"
    registry = agent.state_registry.get_state("limbic") or {}
    last = dict((registry.get("state") or {}).get("last_runtime_motive_state") or {})
    assert last["dominant_motive"] == "reach_out"


def test_limbic_agent_compute_motive_and_decide_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.motive_engine.compute_motive_state",
        lambda _inp: type(
            "_Motive",
            (),
            {"as_dict": lambda self: {"dominant_motive": "continuity", "reach_out_drive": 0.4}},
        )(),
    )
    agent = LimbicAgent()

    motive_state = agent.compute_motive_state(
        silence_state={"willingness_level": "NORMAL"},
        body_sense={"fatigue_level": "FRESH"},
        offline_state={"offline_hours": 2.0},
        user_message="hello",
        runtime_flags={"source": "test"},
    )
    plan = build_reply_plan_from_inputs(
        silence_state={"willingness_level": "NORMAL"},
        body_sense={"fatigue_level": "FRESH"},
        offline_state={"offline_hours": 2.0},
        motive_state=motive_state,
        conversation_interpretation={
            "literal_topic": "identity",
            "interaction_move": "first_ask",
            "freshness": "new",
            "user_stance": "probing",
        },
    ).as_dict()

    assert motive_state["dominant_motive"] == "continuity"
    assert plan["primary_topic"] == "identity"
    assert plan["response_goal"] == "identify"
    assert plan["tactic_family"] == "brief_name_only"
    registry = agent.state_registry.get_state("limbic") or {}
    state_payload = dict(registry.get("state") or {})
    assert dict(state_payload.get("last_motive_state") or {})["dominant_motive"] == "continuity"
    assert "last_render_policy" not in state_payload
