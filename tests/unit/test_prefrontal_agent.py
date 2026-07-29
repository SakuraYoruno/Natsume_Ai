from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.tribe.prefrontal_agent import PrefrontalAgent
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.tribe_state import TribeStateRegistry
from host_brain.persona.personality_traits import DEFAULT_BASELINE


def _build_prefrontal_agent(**kwargs) -> PrefrontalAgent:
    kwargs.setdefault("state_registry", TribeStateRegistry())
    return PrefrontalAgent(**kwargs)


class _FakeEvolutionService:
    def __init__(self) -> None:
        self.staged: list[dict[str, object]] = []
        self.expression_preferences = None

    def resolve_persona_traits(self, **_kwargs):
        return dict(DEFAULT_BASELINE)

    def resolve_expression_preferences(self, **_kwargs):
        return self.expression_preferences

    def stage_persona_feedback(self, **kwargs):
        self.staged.append(dict(kwargs))
        return {
            "status": "STAGED",
            "proposal": {"proposal_id": "feedback-proposal://unit"},
        }


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

    class _FakeSocial:
        def resolve_bond_snapshot(self, *, person_id: str):
            state = _FakeBSE().get_bond_state(session_id="default")
            return {
                "bond_state_obj": state,
                "bond_summary": {
                    "stage": state.stage,
                    "trend": state.trend,
                    "capability_hint": state.capability_hint,
                },
            }

    monkeypatch.setattr("host_brain.persona.evolution_orchestrator.EvolutionOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.get_social_brain_agent", lambda: _FakeSocial())
    logger = _FakeLogger()

    result = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(emotion_timeline=lambda: _FakeTimeline()),
    ).run_evolution_cycle(
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


def test_prefrontal_canonical_life_defers_unscoped_evolution(
    monkeypatch,
) -> None:
    class _ForbiddenTimeline:
        def rebuild_summary(self, **_kwargs):
            raise AssertionError("canonical Life must not read legacy timeline")

        def build_daily_diary(self, **_kwargs):
            raise AssertionError("canonical Life must not read legacy timeline")

    monkeypatch.setattr(
        "host_brain.tribe.prefrontal_agent.get_social_brain_agent",
        lambda: (_ for _ in ()).throw(
            AssertionError("canonical Life must not read legacy Bond")
        ),
    )
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
            emotion_timeline=lambda: _ForbiddenTimeline(),
        ),
    )

    result = agent.run_evolution_cycle(
        system_intent={"args": {"user_id": "user_x", "date": "2026-04-09"}},
        logger=_FakeLogger(),
    )

    assert result == {
        "status": "DEFERRED",
        "reason_code": "CANONICAL_RELATIONSHIP_SCOPE_REQUIRED",
        "proposal_only": True,
        "proposals": [],
        "growth_awareness": {},
        "errors": [],
        "relationship_input_source": "CANONICAL_RELATIONSHIP_SCOPE_REQUIRED",
    }


def test_prefrontal_canonical_life_retires_bond_stage_growth_paths(
    monkeypatch,
) -> None:
    subscriptions: list[str] = []

    class _FakeBus:
        def subscribe(self, event_type: str, _handler) -> None:  # noqa: ANN001
            subscriptions.append(event_type)

    monkeypatch.setattr(
        "host_brain.tribe.event_bus.get_tribe_event_bus",
        lambda: _FakeBus(),
    )
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    agent.register_subscriptions()
    direct_result = agent.apply_stage_promotion_growth(
        user_id="user_x",
        from_stage="FRIEND",
        to_stage="SOULMATE",
        ts_ms=1,
    )
    milestone_result = agent.apply_milestone_growth(
        user_id="user_x",
        milestone_key="deep_loneliness_experience",
        ts_ms=1,
    )
    asyncio.run(
        agent._on_bond_stage_changed_growth(  # noqa: SLF001
            SimpleNamespace(
                payload={
                    "user_id": "user_x",
                    "from_stage": "FRIEND",
                    "to_stage": "SOULMATE",
                },
                ts_ms=1,
            )
        )
    )
    asyncio.run(
        agent._on_evolution_cycle_triggered(  # noqa: SLF001
            SimpleNamespace(payload={"user_id": "user_x", "reason": "legacy"})
        )
    )
    asyncio.run(
        agent._on_loneliness_signal(  # noqa: SLF001
            SimpleNamespace(payload={"user_id": "user_x", "loneliness": 0.95}, ts_ms=1)
        )
    )
    asyncio.run(
        agent._on_immune_block_growth(  # noqa: SLF001
            SimpleNamespace(payload={"user_id": "user_x", "risk_level": "critical"}, ts_ms=1)
        )
    )

    assert subscriptions == ["INTERACTION_RECORDED"]
    assert "BOND_STAGE_CHANGED" not in subscriptions
    assert direct_result == {
        "applied": False,
        "reason": "LEGACY_BOND_STAGE_GROWTH_RETIRED",
        "proposal": {
            "status": "NO_CANDIDATE",
            "reason_code": "LEGACY_BOND_STAGE_GROWTH_RETIRED",
        },
    }
    assert milestone_result == {
        "applied": False,
        "reason": "LEGACY_UNSCOPED_MILESTONE_GROWTH_RETIRED",
        "proposal": {
            "status": "NO_CANDIDATE",
            "reason_code": "LEGACY_UNSCOPED_MILESTONE_GROWTH_RETIRED",
        },
    }


def test_prefrontal_canonical_life_keeps_drift_guard_but_blocks_legacy_side_effects(
    monkeypatch,
) -> None:
    legacy_calls: list[str] = []
    emitted: list[str] = []
    governor_updates: list[dict[str, object]] = []

    class _Governor:
        def update_prefrontal_state(self, **kwargs) -> None:  # noqa: ANN003
            governor_updates.append(dict(kwargs))

    monkeypatch.setattr(
        PrefrontalAgent,
        "run_growth_feedback_cycle",
        lambda _self, **_kwargs: {
            "drift_alert_level": "SEVERE",
            "drift_dims": ["identity"],
        },
    )
    monkeypatch.setattr(
        "host_brain.integrations.governor_rules_cache.get_governor_rules_cache",
        lambda: _Governor(),
    )
    for target in (
        "get_limbic_agent",
        "get_endocrine_agent",
        "get_pain_reward_agent",
        "get_interoception_agent",
    ):
        monkeypatch.setattr(
            f"host_brain.tribe.prefrontal_agent.{target}",
            lambda _target=target: legacy_calls.append(_target),
        )
    monkeypatch.setattr(
        "host_brain.tribe.prefrontal_agent.emit_tribe_event",
        lambda **kwargs: emitted.append(str(kwargs.get("event_type") or "")),
    )
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    asyncio.run(
        agent._on_interaction_recorded(  # noqa: SLF001
            SimpleNamespace(
                payload={
                    "user_id": "user_x",
                    "session_id": "session_x",
                    "user_message": "可以帮我分析这个吗？",
                    "assistant_reply": "好的，我来看看。哈哈。",
                }
            )
        )
    )

    assert governor_updates == [
        {"drift_detected": True, "scope_override": "drift_restricted:severe"},
    ]
    assert legacy_calls == []
    assert emitted == []


def test_prefrontal_run_evolution_cycle_degraded_on_error(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("orchestrator unavailable")

    monkeypatch.setattr("host_brain.persona.evolution_orchestrator.EvolutionOrchestrator", _boom)
    logger = _FakeLogger()

    result = _build_prefrontal_agent().run_evolution_cycle(
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

    result = _build_prefrontal_agent().resolve_self_reflection_context(
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

    result = _build_prefrontal_agent().resolve_self_reflection_context(
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

    result = _build_prefrontal_agent().resolve_self_reflection_context(
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

    result = _build_prefrontal_agent().resolve_style_evolution_summary()

    assert result == {"warmth": "rising", "humor": "stable"}


def test_prefrontal_run_persona_drift_cycle_does_not_write_hard_state(monkeypatch) -> None:
    class _FakeDetector:
        def on_turn(self, *, llm_output_text: str, user_input_text: str):
            assert llm_output_text == "reply"
            assert user_input_text == "input"
            return SimpleNamespace(
                alert_level="moderate",
                drift_dims=["warmth_LOW"],
                correction_hint="be warmer",
            )

    monkeypatch.setattr("host_brain.persona.drift_detector.PersonaDriftDetector", _FakeDetector)

    result = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            hard_state=lambda: (_ for _ in ()).throw(
                AssertionError("Prefrontal must not write Host HardState")
            )
        ),
    ).run_persona_drift_cycle(
        llm_output_text="reply",
        user_input_text="input",
    )

    assert result["alert_level"] == "MODERATE"
    assert result["drift_dims"] == ["warmth_LOW"]
    assert result["correction_hint"] == "be warmer"


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
            return SimpleNamespace(
                applied=False,
                triggered_rules=["rule_A"],
                candidate_delta={"warmth": 0.01},
            )

    monkeypatch.setattr("host_brain.bond.user_identity.resolve_bond_user_id", lambda session_id: f"user::{session_id}")
    monkeypatch.setattr("host_brain.persona.growth_feedback_collector.GrowthFeedbackCollector", _FakeCollector)
    monkeypatch.setattr("host_brain.persona.persona_growth.PersonaGrowthTracker", _FakeTracker)

    evolution = _FakeEvolutionService()
    result = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            evolution_activation_service=lambda: evolution,
        ),
    ).run_growth_feedback_cycle(
        session_id="s-001",
        user_message="continue",
        assistant_reply="ok",
    )

    assert result == {
        "user_id": "user::s-001",
        "feedback_count": 2,
        "applied": False,
        "proposal": {
            "status": "STAGED",
            "proposal": {
                "proposal_id": "feedback-proposal://unit"
            },
        },
        "triggered_rules": ["rule_A"],
    }
    assert evolution.staged[0]["source_kind"] == "response_feedback"
    assert captured["feedback_calls"] == [
        ("user::s-001", "signal_1"),
        ("user::s-001", "signal_2"),
    ]


def test_prefrontal_canonical_life_retires_legacy_session_feedback_growth(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "host_brain.bond.user_identity.resolve_bond_user_id",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("canonical Life must not resolve a legacy Bond user")
        ),
    )
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    result = agent.run_growth_feedback_cycle(
        session_id="s-001",
        user_message="你真的帮到我了",
        assistant_reply="我在呢",
    )

    assert result == {
        "status": "RETIRED",
        "reason_code": "LEGACY_SESSION_FEEDBACK_GROWTH_RETIRED",
        "feedback_source": "CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED",
        "user_id": "",
        "feedback_count": 0,
        "applied": False,
        "proposal": {
            "status": "NO_CANDIDATE",
            "reason_code": "LEGACY_SESSION_FEEDBACK_GROWTH_RETIRED",
        },
        "triggered_rules": [],
    }


def test_prefrontal_apply_stage_and_milestone_growth(monkeypatch) -> None:
    class _FakeTracker:
        def on_stage_promoted(self, **kwargs):  # noqa: ANN003
            assert kwargs["to_stage"] == "CONFIDANT"
            return SimpleNamespace(
                applied=False,
                reason="stage-ok",
                candidate_delta={"warmth": 0.01},
            )

        def on_milestone(self, **kwargs):  # noqa: ANN003
            assert kwargs["milestone_key"] == "stage_promoted"
            return SimpleNamespace(
                applied=False,
                reason="milestone-ok",
                candidate_delta={"curiosity": 0.01},
            )

    monkeypatch.setattr("host_brain.persona.persona_growth.PersonaGrowthTracker", _FakeTracker)
    evolution = _FakeEvolutionService()
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            evolution_activation_service=lambda: evolution,
        ),
    )

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

    assert promoted["applied"] is False
    assert promoted["reason"] == "stage-ok"
    assert promoted["proposal"]["status"] == "STAGED"
    assert milestone["applied"] is False
    assert milestone["reason"] == "milestone-ok"
    assert milestone["proposal"]["status"] == "STAGED"
    assert [item["source_kind"] for item in evolution.staged] == [
        "bond_stage_promotion",
        "relationship_milestone",
    ]


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
        "host_brain.composition.process_services.get_resource_manager",
        lambda create_if_missing=False: _FakeResourceManager(),
    )

    event_key = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            resource_manager=lambda **_: _FakeResourceManager(),
        ),
    )._reserve_evolution_event(  # noqa: SLF001
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
        "host_brain.composition.process_services.get_resource_manager",
        lambda create_if_missing=False: None,
    )
    agent = _build_prefrontal_agent()

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
        "host_brain.composition.process_services.get_resource_manager",
        lambda create_if_missing=False: None,
    )
    agent = _build_prefrontal_agent()

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
        "host_brain.composition.process_services.get_resource_manager",
        lambda create_if_missing=False: None,
    )
    agent = _build_prefrontal_agent()
    agent._created_monotonic = clock["now"]  # noqa: SLF001

    assert agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal") is None  # noqa: SLF001

    clock["now"] += 301.0
    assert agent._reserve_evolution_event(user_id="u1", reason="loneliness_signal") == "u1:loneliness_signal"  # noqa: SLF001


def test_prefrontal_loneliness_logs_and_milestones_are_debounced(monkeypatch) -> None:
    monkeypatch.setenv("HOST_PREFRONTAL_LONELINESS_SIGNAL_LOG_COOLDOWN_S", "300")
    monkeypatch.setenv("HOST_PREFRONTAL_LONELINESS_MILESTONE_COOLDOWN_S", "600")
    agent = _build_prefrontal_agent()

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
        applied = False
        candidate_delta = {"curiosity": 0.01}
        guard_result = {"allowed": True}

        def as_dict(self):
            return {
                "applied": False,
                "candidate_delta": dict(self.candidate_delta),
                "reason": "weekly",
            }

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

    evolution = _FakeEvolutionService()
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            evolution_activation_service=lambda: evolution,
        ),
    )
    agent._emergent_growth_engine = _EmergentEngine()

    weekly = agent.run_emergent_growth_weekly(
        user_id="user-eg",
        lookback_days=7,
        episodic_store=object(),
        llm_fn=lambda prompt: "{}",
    )
    status = agent.resolve_emergent_growth_status(user_id="user-eg", recent_limit=5)

    assert weekly["status"] == "PROPOSED"
    assert weekly["pattern_result"]["relationship_signal_density"] == 0.8
    assert weekly["growth_result"]["applied"] is False
    assert weekly["proposal"]["status"] == "STAGED"
    assert status["growth_summary"]["weekly_cycles"] == 1
    assert status["recent_growth_log"][0]["cycle"] == 1


def test_prefrontal_run_emergent_growth_weekly_skips_without_llm() -> None:
    result = _build_prefrontal_agent().run_emergent_growth_weekly(
        user_id="user-eg",
        lookback_days=7,
        episodic_store=object(),
        llm_fn=None,
    )

    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "EMERGENT_GROWTH_LLM_REQUIRED"


def test_prefrontal_canonical_life_retires_raw_emergent_growth_before_analyzer(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "host_brain.persona.interaction_pattern_analyzer.InteractionPatternAnalyzer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("canonical Life must not construct legacy analyzer")
        ),
    )
    agent = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    )

    weekly = agent.run_emergent_growth_weekly(
        user_id="user-eg",
        lookback_days=7,
        episodic_store=object(),
        llm_fn=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("canonical Life must not call legacy LLM")
        ),
    )
    status = agent.resolve_emergent_growth_status(user_id="user-eg", recent_limit=5)

    assert agent.uses_canonical_affective_life() is True
    assert weekly == {
        "status": "DEFERRED",
        "reason_code": "CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED",
        "user_id": "user-eg",
        "lookback_days": 7,
        "pattern_result": {},
        "growth_result": {},
        "proposal": {
            "status": "NO_CANDIDATE",
            "reason_code": "LEGACY_EMERGENT_GROWTH_RETIRED",
        },
    }
    assert status == {
        "status": "RETIRED",
        "reason_code": "LEGACY_EMERGENT_GROWTH_RETIRED",
        "user_id": "user-eg",
        "recent_limit": 5,
        "growth_summary": {},
        "recent_growth_log": [],
    }


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

    agent = _build_prefrontal_agent()
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

    result = _build_prefrontal_agent().resolve_persona_traits(
        persona_user_id="user-2"
    )

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

    result = _build_prefrontal_agent().resolve_persona_expression_hint(
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
        "preference_policy_active": False,
    }


def test_prefrontal_consumes_only_activated_expression_policy(
    monkeypatch,
) -> None:
    class _FakeAdapter:
        def adapt(self, *_args, **_kwargs):
            return SimpleNamespace(
                style_hint="自然回应",
                tone="gentle",
                echo_weight_modifier=0.0,
                follow_up_tendency=0.0,
                pace_hint="steady",
            )

    evolution = _FakeEvolutionService()
    evolution.expression_preferences = {
        "schema": "natsume.expression-preference-policy.v1",
        "positive_feedback_count": 0,
        "negative_feedback_count": 2,
        "tag_scores": {
            "template_like": -2,
            "ai_tone": -1,
        },
    }
    monkeypatch.setattr(
        "host_brain.persona.expression_adapter.PersonaExpressionAdapter",
        _FakeAdapter,
    )
    monkeypatch.setattr(
        PrefrontalAgent,
        "resolve_persona_traits",
        lambda self, *, persona_user_id: {"uid": persona_user_id},
    )
    result = _build_prefrontal_agent(
        application_ports=TribeApplicationPorts(
            evolution_activation_service=lambda: evolution,
        ),
    ).resolve_persona_expression_hint(
        persona_user_id="user-3",
        bond_stage="FRIEND",
        user_emotion_tag="HAPPY",
        user_message="继续",
        valence=0.4,
        arousal=0.5,
        dominance=0.3,
        ts_ms=123,
    )

    assert result["preference_policy_active"] is True
    assert "避免模板化分段" in result["style_hint"]
    assert "避免机械 AI 腔" in result["style_hint"]


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

    result = _build_prefrontal_agent().resolve_bond_style_instruction(
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
            assert user_id == "default"
            return {"bond_vector": {"trust": 0.74, "repair_confidence": 0.68}}

    class _FakeHippocampus:
        def write_autobiographical_memory(self, **kwargs):  # noqa: ANN003
            assert kwargs["person_id"] == "u-autobio"
            assert kwargs["gate_result"]["decision"] == "ALLOW"
            return {"layer": "autobiographical", "payload": {"narrative": kwargs["narrative"]}}

    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.get_social_brain_agent", lambda: _FakeSocial())
    monkeypatch.setattr("host_brain.tribe.prefrontal_agent.get_hippocampus_agent", lambda: _FakeHippocampus())

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
