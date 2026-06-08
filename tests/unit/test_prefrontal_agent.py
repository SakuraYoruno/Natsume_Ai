from __future__ import annotations

from types import SimpleNamespace

from host_brain.tribe.prefrontal_agent import PrefrontalAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _FakeLogger:
    def __init__(self) -> None:
        self.info_calls: list[tuple[str, tuple[object, ...]]] = []
        self.warning_calls: list[tuple[str, tuple[object, ...]]] = []

    def info(self, message: str, *args) -> None:
        self.info_calls.append((message, args))

    def warning(self, message: str, *args) -> None:
        self.warning_calls.append((message, args))


def test_prefrontal_run_evolution_cycle_success(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeTimeline:
        def rebuild_summary(self, *, window_ms: int):
            return {"window_ms": window_ms, "status": "ok"}

        def build_daily_diary(self, *, window_ms: int):
            return {"diary_window_ms": window_ms}

    class _FakeOrchestrator:
        def run_evolution_cycle(self, **kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                cycle_id="evo_123",
                traits_changed=True,
                growth_awareness={"focus": "stability"},
                errors=[],
            )

    class _FakeBSE:
        def get_bond_state(self, *, session_id: str):
            assert session_id == "default"
            return SimpleNamespace(stage="trusted", trend="up", capability_hint="high")

    monkeypatch.setattr("host_brain.app.emotion_context._global_emotion_timeline", _FakeTimeline())
    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: _FakeBSE())
    monkeypatch.setattr("host_brain.persona.evolution_orchestrator.EvolutionOrchestrator", _FakeOrchestrator)
    logger = _FakeLogger()

    result = PrefrontalAgent().run_evolution_cycle(
        system_intent={"args": {"user_id": "user_x", "date": "2026-04-08"}},
        logger=logger,
    )

    assert result["status"] == "OK"
    assert result["cycle_id"] == "evo_123"
    kwargs = dict(captured["kwargs"] or {})
    assert kwargs["user_id"] == "user_x"
    assert kwargs["date"] == "2026-04-08"
    assert kwargs["timeline_summary"]["status"] == "ok"
    assert kwargs["bond_summary"]["stage"] == "trusted"
    assert len(logger.info_calls) == 1


def test_prefrontal_run_evolution_cycle_degraded_on_error(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("orchestrator unavailable")

    monkeypatch.setattr("host_brain.persona.evolution_orchestrator.EvolutionOrchestrator", _boom)
    logger = _FakeLogger()

    result = PrefrontalAgent().run_evolution_cycle(
        system_intent={"args": {"user_id": "user_y"}},
        logger=logger,
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "EVOLUTION_CYCLE_FAILED"
    assert "orchestrator unavailable" in str(result["detail"])
    assert len(logger.warning_calls) == 1


def test_prefrontal_resolve_self_reflection_context(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")

    class _Snapshot:
        def as_dict(self) -> dict:
            return {"recent_mood_trend": "steady", "dominant_recent_emotion": "CALM"}

    class _Engine:
        def get_self_model_snapshot(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return _Snapshot()

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: _Engine()),
    )
    monkeypatch.setattr(
        "host_brain.persona.self_reflection.build_self_reflection_pulse",
        lambda snapshot, trigger_reason="runtime_snapshot", **kwargs: {
            "self_awareness": f"trend={snapshot.get('recent_mood_trend')}",
            "trigger_reason": trigger_reason,
        },
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.build_pool_prompt_llm_fn",
        lambda **kwargs: (lambda prompt: "ok"),
    )

    result = PrefrontalAgent().resolve_self_reflection_context(
        persona_user_id="user-sr",
        timeline_summary={"dominant_emotion": "CALM"},
        timeline_diary={"entry_count": 3},
        bond_state_obj=SimpleNamespace(last_event_ts_ms=123),
        bond_summary={"stage": "FRIEND"},
        now_ms=999,
        trigger_reason="runtime_snapshot",
    )

    assert result["self_model_snapshot"]["recent_mood_trend"] == "steady"
    assert result["self_reflection_pulse"]["self_awareness"] == "trend=steady"
    assert result["self_reflection_pulse"]["trigger_reason"] == "runtime_snapshot"


def test_prefrontal_resolve_self_reflection_context_skips_llm_when_background_disabled(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "false")

    class _Snapshot:
        def as_dict(self) -> dict:
            return {"recent_mood_trend": "steady", "dominant_recent_emotion": "CALM"}

    class _Engine:
        def get_self_model_snapshot(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return _Snapshot()

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: _Engine()),
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.build_pool_prompt_llm_fn",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("self reflection llm should not resolve")),
    )

    result = PrefrontalAgent().resolve_self_reflection_context(
        persona_user_id="user-sr",
        timeline_summary={"dominant_emotion": "CALM"},
        timeline_diary={"entry_count": 3},
        bond_state_obj=SimpleNamespace(last_event_ts_ms=123),
        bond_summary={"stage": "FRIEND"},
        now_ms=999,
        trigger_reason="runtime_snapshot",
    )

    assert result["self_model_snapshot"]["recent_mood_trend"] == "steady"
    assert result["self_reflection_pulse"] == {}
    assert result["reason_code"] == "SELF_REFLECTION_BACKGROUND_TASKS_DISABLED"


def test_prefrontal_resolve_self_reflection_context_can_be_read_only(monkeypatch) -> None:
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")

    class _Snapshot:
        def as_dict(self) -> dict:
            return {"recent_mood_trend": "steady", "dominant_recent_emotion": "CALM"}

    class _Engine:
        def get_self_model_snapshot(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return _Snapshot()

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: _Engine()),
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.build_pool_prompt_llm_fn",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("read-only snapshot must not build llm")),
    )
    monkeypatch.setattr(
        "host_brain.persona.self_reflection.build_self_reflection_pulse",
        lambda snapshot, trigger_reason="runtime_snapshot", llm_fn=None, strict_llm=False, **kwargs: {
            "self_awareness": f"strict={strict_llm};llm={llm_fn is not None}",
            "trigger_reason": trigger_reason,
        },
    )

    result = PrefrontalAgent().resolve_self_reflection_context(
        persona_user_id="user-sr",
        timeline_summary={"dominant_emotion": "CALM"},
        timeline_diary={"entry_count": 3},
        bond_state_obj=SimpleNamespace(last_event_ts_ms=123),
        bond_summary={"stage": "FRIEND"},
        now_ms=999,
        trigger_reason="runtime_snapshot",
        use_llm=False,
    )

    assert result["self_reflection_pulse"]["self_awareness"] == "strict=False;llm=False"


def test_prefrontal_resolve_style_evolution_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.drift_detector.get_drift_detector",
        lambda: SimpleNamespace(
            get_style_evolution_summary=lambda: {"warmth": "rising", "humor": "stable"},
        ),
    )

    result = PrefrontalAgent().resolve_style_evolution_summary()

    assert result == {"warmth": "rising", "humor": "stable"}


def test_prefrontal_run_persona_drift_cycle_sets_hard_state_hint(monkeypatch) -> None:
    class _FakeHardState:
        def __init__(self) -> None:
            self.last_key: str | None = None
            self.last_value: str | None = None

        def set(self, key: str, value: str) -> None:
            self.last_key = key
            self.last_value = value

    class _FakeDetector:
        def on_turn(self, *, llm_output_text: str, user_input_text: str):
            assert llm_output_text == "reply"
            assert user_input_text == "input"
            return SimpleNamespace(
                alert_level="moderate",
                drift_dims=["warmth_LOW"],
                correction_hint="be warmer",
            )

    hard_state = _FakeHardState()
    monkeypatch.setattr("host_brain.app.dependencies.get_hard_state", lambda: hard_state)
    monkeypatch.setattr("host_brain.persona.drift_detector.PersonaDriftDetector", _FakeDetector)

    result = PrefrontalAgent().run_persona_drift_cycle(
        llm_output_text="reply",
        user_input_text="input",
    )

    assert result["alert_level"] == "MODERATE"
    assert result["drift_dims"] == ["warmth_LOW"]
    assert result["correction_hint"] == "be warmer"
    assert hard_state.last_key == "drift_correction_hint"
    assert hard_state.last_value == "be warmer"


def test_prefrontal_run_growth_feedback_cycle(monkeypatch) -> None:
    captured: dict[str, object] = {"feedback_calls": []}

    class _FakeCollector:
        @staticmethod
        def extract_engagement_signals(_text: str):
            return ["signal_1", "signal_2"]

        def add_feedback(self, user_id: str, feedback: str) -> None:
            captured["feedback_calls"].append((user_id, feedback))

    class _FakeTracker:
        def on_interaction_cycle(self, **kwargs):
            captured["tracker_kwargs"] = kwargs
            return SimpleNamespace(applied=True, triggered_rules=["rule_A"])

    monkeypatch.setattr("host_brain.bond.user_identity.resolve_bond_user_id", lambda session_id: f"user::{session_id}")
    monkeypatch.setattr("host_brain.persona.growth_feedback_collector.GrowthFeedbackCollector", _FakeCollector)
    monkeypatch.setattr("host_brain.persona.persona_growth.PersonaGrowthTracker", _FakeTracker)

    result = PrefrontalAgent().run_growth_feedback_cycle(
        session_id="s-001",
        user_message="continue",
        assistant_reply="ok",
    )

    assert result == {
        "user_id": "user::s-001",
        "feedback_count": 2,
        "applied": True,
        "triggered_rules": ["rule_A"],
    }
    assert captured["feedback_calls"] == [
        ("user::s-001", "signal_1"),
        ("user::s-001", "signal_2"),
    ]


def test_prefrontal_apply_stage_and_milestone_growth(monkeypatch) -> None:
    class _FakeTracker:
        def on_stage_promoted(self, **kwargs):  # noqa: ANN003
            assert kwargs["to_stage"] == "CONFIDANT"
            return SimpleNamespace(applied=True, reason="stage-ok")

        def on_milestone(self, **kwargs):  # noqa: ANN003
            assert kwargs["milestone_key"] == "stage_promoted"
            return SimpleNamespace(applied=True, reason="milestone-ok")

    monkeypatch.setattr("host_brain.persona.persona_growth.PersonaGrowthTracker", _FakeTracker)
    agent = PrefrontalAgent()

    promoted = agent.apply_stage_promotion_growth(
        user_id="u-growth",
        from_stage="FRIEND",
        to_stage="CONFIDANT",
        ts_ms=100,
    )
    milestone = agent.apply_milestone_growth(
        user_id="u-growth",
        milestone_key="stage_promoted",
        ts_ms=101,
    )

    assert promoted == {"applied": True, "reason": "stage-ok"}
    assert milestone == {"applied": True, "reason": "milestone-ok"}


def test_prefrontal_evolution_event_deferred_by_resource_gate(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setenv("HOST_PREFRONTAL_EVENT_EVOLUTION_STARTUP_GRACE_S", "0")

    class _FakeResourceManager:
        def assess_background_task(self, **kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {
                "allowed": False,
                "pressure_status": "HIGH",
                "reason_code": "RESOURCE_PRESSURE_HIGH",
                "retry_after_s": 600,
            }

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_resource_manager",
        lambda create_if_missing=False: _FakeResourceManager(),
    )

    event_key = PrefrontalAgent()._reserve_evolution_event(  # noqa: SLF001
        user_id="u1",
        reason="loneliness_signal",
    )

    assert event_key is None
    assert captured["task_id"] == "evolution_cycle_event"
    assert captured["task_type"] == "memory_heavy"
    assert captured["uses_llm"] is True


def test_prefrontal_evolution_event_cooldown_after_release(monkeypatch) -> None:
    monkeypatch.setenv("HOST_PREFRONTAL_EVENT_EVOLUTION_STARTUP_GRACE_S", "0")
    monkeypatch.setenv("HOST_PREFRONTAL_LONELINESS_EVOLUTION_COOLDOWN_S", "120")
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_resource_manager",
        lambda create_if_missing=False: None,
    )
    agent = PrefrontalAgent()

    first_key = agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal")  # noqa: SLF001
    assert first_key == "u1:loneliness_signal"
    agent._release_evolution_event(first_key)  # noqa: SLF001

    assert agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal") is None  # noqa: SLF001


def test_prefrontal_evolution_user_cooldown_merges_different_reasons(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setenv("HOST_PREFRONTAL_EVENT_EVOLUTION_STARTUP_GRACE_S", "0")
    monkeypatch.setenv("HOST_PREFRONTAL_EVENT_EVOLUTION_USER_COOLDOWN_S", "300")
    monkeypatch.setenv("HOST_PREFRONTAL_INNER_WORLD_EVOLUTION_COOLDOWN_S", "60")
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_resource_manager",
        lambda create_if_missing=False: None,
    )
    agent = PrefrontalAgent()

    first_key = agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal")  # noqa: SLF001
    assert first_key == "u1:loneliness_signal"
    assert agent._reserve_evolution_event(user_id="u1", reason="inner_world_spontaneous_activity") is None  # noqa: SLF001
    agent._release_evolution_event(first_key)  # noqa: SLF001

    assert agent._reserve_evolution_event(user_id="u1", reason="inner_world_spontaneous_activity") is None  # noqa: SLF001

    clock["now"] += 301.0
    next_key = agent._reserve_evolution_event(user_id="u1", reason="inner_world_spontaneous_activity")  # noqa: SLF001
    assert next_key == "u1:inner_world_spontaneous_activity"


def test_prefrontal_evolution_startup_grace_skips_background_events(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setenv("HOST_PREFRONTAL_EVENT_EVOLUTION_STARTUP_GRACE_S", "300")
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_resource_manager",
        lambda create_if_missing=False: None,
    )
    agent = PrefrontalAgent()
    agent._created_monotonic = clock["now"]  # noqa: SLF001

    assert agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal") is None  # noqa: SLF001

    clock["now"] += 301.0
    assert agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal") == "u1:loneliness_signal"  # noqa: SLF001


def test_prefrontal_loneliness_logs_and_milestones_are_debounced(monkeypatch) -> None:
    monkeypatch.setenv("HOST_PREFRONTAL_LONELINESS_SIGNAL_LOG_COOLDOWN_S", "300")
    monkeypatch.setenv("HOST_PREFRONTAL_LONELINESS_MILESTONE_COOLDOWN_S", "600")
    agent = PrefrontalAgent()

    assert agent._loneliness_signal_log_allowed(user_id="u1") is True  # noqa: SLF001
    assert agent._loneliness_signal_log_allowed(user_id="u1") is False  # noqa: SLF001
    assert agent._loneliness_milestone_allowed(user_id="u1") is True  # noqa: SLF001
    assert agent._loneliness_milestone_allowed(user_id="u1") is False  # noqa: SLF001


def test_prefrontal_run_emergent_growth_weekly_and_status(monkeypatch) -> None:
    class _PatternResult:
        analysis_method = "llm"

        def as_dict(self):
            return {"relationship_signal_density": 0.8}

    class _GrowthResult:
        applied = True

        def as_dict(self):
            return {"applied": True, "reason": "weekly"}

    class _Analyzer:
        def __init__(self, llm_fn=None, strict_llm=False):  # noqa: ANN001
            assert callable(llm_fn)
            assert strict_llm is True

        def analyze(self, **kwargs):  # noqa: ANN003
            assert kwargs["user_id"] == "user-eg"
            assert kwargs["lookback_days"] == 7
            return _PatternResult()

    class _Engine:
        def get_traits(self, user_id: str):
            assert user_id == "user-eg"
            return {"warmth": 0.5}

    class _EmergentEngine:
        def apply(self, **kwargs):  # noqa: ANN003
            assert kwargs["user_id"] == "user-eg"
            return _GrowthResult()

        def get_growth_log(self, user_id: str):
            assert user_id == "user-eg"
            return [SimpleNamespace(as_dict=lambda: {"cycle": 1})]

        def get_growth_summary(self, user_id: str):
            assert user_id == "user-eg"
            return {"weekly_cycles": 1}

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: _Engine()),
    )
    monkeypatch.setattr(
        "host_brain.persona.interaction_pattern_analyzer.InteractionPatternAnalyzer",
        _Analyzer,
    )

    agent = PrefrontalAgent()
    agent._emergent_growth_engine = _EmergentEngine()

    weekly = agent.run_emergent_growth_weekly(
        user_id="user-eg",
        lookback_days=7,
        episodic_store=object(),
        llm_fn=lambda prompt: "{}",
    )
    status = agent.resolve_emergent_growth_status(user_id="user-eg", recent_limit=5)

    assert weekly["status"] == "OK"
    assert weekly["pattern_result"]["relationship_signal_density"] == 0.8
    assert weekly["growth_result"]["applied"] is True
    assert status["growth_summary"]["weekly_cycles"] == 1
    assert status["recent_growth_log"][0]["cycle"] == 1


def test_prefrontal_run_emergent_growth_weekly_skips_without_llm() -> None:
    result = PrefrontalAgent().run_emergent_growth_weekly(
        user_id="user-eg",
        lookback_days=7,
        episodic_store=object(),
        llm_fn=None,
    )

    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "EMERGENT_GROWTH_LLM_REQUIRED"


def test_prefrontal_resolve_persona_change_and_growth_payloads(monkeypatch) -> None:
    class _Engine:
        def get_change_log(self, user_id: str, limit: int = 5):
            assert user_id == "user-1"
            assert limit == 3
            return [{"field": "warmth", "delta": 0.01}]

    class _FakeEvolutionOrchestrator:
        def get_last_growth_awareness(self):
            return {"focus": "stability"}

        def get_growth_summary(self, user_id: str, *, now_ts_ms: int = 0):
            assert user_id == "user-1"
            assert now_ts_ms == 1234
            return {"recent_cycle_count": 2}

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: _Engine()),
    )
    monkeypatch.setattr(
        "host_brain.persona.evolution_orchestrator.EvolutionOrchestrator",
        _FakeEvolutionOrchestrator,
    )

    agent = PrefrontalAgent()
    changes = agent.resolve_persona_change_log(persona_user_id="user-1", limit=3)
    growth_awareness = agent.resolve_growth_awareness()
    growth_summary = agent.resolve_growth_summary(persona_user_id="user-1", now_ts_ms=1234)

    assert changes == [{"field": "warmth", "delta": 0.01}]
    assert growth_awareness == {"focus": "stability"}
    assert growth_summary == {"recent_cycle_count": 2}


def test_prefrontal_resolve_persona_traits_fallback(monkeypatch) -> None:
    class _Engine:
        def get_traits(self, _user_id: str):
            raise RuntimeError("boom")

    class _BaselineTraits:
        @staticmethod
        def from_baseline():
            return {"warmth": 0.5}

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: _Engine()),
    )
    monkeypatch.setattr("host_brain.persona.personality_traits.PersonalityTraits", _BaselineTraits)

    result = PrefrontalAgent().resolve_persona_traits(persona_user_id="user-2")

    assert result == {"warmth": 0.5}


def test_prefrontal_resolve_persona_expression_hint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAdapter:
        def adapt(self, traits, emotion_vector, bond_stage, *, user_emotion_tag: str, user_message: str):  # noqa: ANN001
            captured["traits"] = traits
            captured["bond_stage"] = bond_stage
            captured["user_emotion_tag"] = user_emotion_tag
            captured["user_message"] = user_message
            captured["emotion_vector"] = emotion_vector
            return SimpleNamespace(
                style_hint="自然回应",
                tone="gentle",
                echo_weight_modifier=0.15,
                follow_up_tendency=0.4,
                pace_hint="steady",
            )

    monkeypatch.setattr(
        "host_brain.persona.expression_adapter.PersonaExpressionAdapter",
        _FakeAdapter,
    )
    monkeypatch.setattr(
        PrefrontalAgent,
        "resolve_persona_traits",
        lambda self, *, persona_user_id: {"uid": persona_user_id},  # noqa: ARG005
    )

    result = PrefrontalAgent().resolve_persona_expression_hint(
        persona_user_id="user-3",
        bond_stage="FRIEND",
        user_emotion_tag="HAPPY",
        user_message="继续",
        valence=0.4,
        arousal=0.5,
        dominance=0.3,
        ts_ms=123,
    )

    assert captured["traits"] == {"uid": "user-3"}
    assert captured["bond_stage"] == "FRIEND"
    assert captured["user_emotion_tag"] == "HAPPY"
    assert captured["user_message"] == "继续"
    assert result == {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.15,
        "follow_up_tendency": 0.4,
        "pace_hint": "steady",
    }


def test_prefrontal_resolve_bond_style_instruction(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeBondAwareStyleEngine:
        def generate(self, **kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(prompt_injection="保持温柔但有边界")

    monkeypatch.setattr(
        "host_brain.persona.bond_aware_style.BondAwareStyleEngine",
        _FakeBondAwareStyleEngine,
    )

    result = PrefrontalAgent().resolve_bond_style_instruction(
        bond_stage="FRIEND",
        intimacy_score=0.62,
        mood_state="positive",
        natsume_mood="HAPPY",
        rift_active=False,
        rift_severity="medium",
        rift_progress=0.1,
        bond_trend="rising",
        shared_memory_hints=["一起调试过前端"],
    )

    assert result == "保持温柔但有边界"
    kwargs = dict(captured["kwargs"] or {})
    assert kwargs["bond_stage"] == "FRIEND"
    assert kwargs["intimacy_score"] == 0.62
    assert kwargs["shared_memory_hints"] == ["一起调试过前端"]


def test_prefrontal_autobiographical_gate_and_commit(monkeypatch) -> None:
    registry = TribeStateRegistry()
    registry.set_state(
        "endocrine_agent",
        {"gate_state": "open", "stress_index": 0.21, "energy_reserve": 0.83, "homeostasis_ok": True},
        writer="unit",
    )
    registry.set_state("immune_agent", {"risk_level": "none", "block_rate": 0.0, "counters": {"block": 0}}, writer="unit")

    class _FakeSocial:
        def get_bond_context(self, *, session_id: str, user_id: str):  # noqa: ANN001
            assert session_id == "sess-autobio"
            assert user_id == "u-autobio"
            return {"bond_vector": {"trust": 0.74, "repair_confidence": 0.68}}

    class _FakeHippocampus:
        def write_autobiographical_memory(self, **kwargs):  # noqa: ANN003
            assert kwargs["person_id"] == "u-autobio"
            assert kwargs["gate_result"]["decision"] == "ALLOW"
            return {"layer": "autobiographical", "payload": {"narrative": kwargs["narrative"]}}

    monkeypatch.setattr("host_brain.tribe.tribe_state.get_global_registry", lambda: registry)
    monkeypatch.setattr("host_brain.tribe.social_brain_agent.get_social_brain_agent", lambda: _FakeSocial())
    monkeypatch.setattr("host_brain.tribe.hippocampus_agent.get_hippocampus_agent", lambda: _FakeHippocampus())

    agent = PrefrontalAgent(state_registry=registry)
    gate = agent.evaluate_autobiographical_memory_gate(
        persona_user_id="u-autobio",
        narrative="这是我们一起推进系统的一次重要记忆。",
        source_agent="default_mode_agent",
        context={"session_id": "sess-autobio"},
    )
    committed = agent.commit_autobiographical_memory(
        persona_user_id="u-autobio",
        narrative="这是我们一起推进系统的一次重要记忆。",
        source_agent="default_mode_agent",
        context={"session_id": "sess-autobio"},
        request_id="req-autobio",
    )

    assert gate["decision"] == "ALLOW"
    assert gate["reason_code"] == "PASS"
    assert committed["status"] == "OK"
    assert committed["write_result"]["layer"] == "autobiographical"
