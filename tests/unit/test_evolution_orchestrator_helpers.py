from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona import self_model as self_model_mod
from host_brain.persona.evolution_orchestrator import (
    EvolutionCycleResult,
    EvolutionOrchestrator,
)
from host_brain.persona.personality_traits import PersonalityTraits


class _DummyGrowthResult:
    def __init__(self, *, applied: bool, reason: str = "ok", delta_applied: dict[str, float] | None = None) -> None:
        self.applied = applied
        self.reason = reason
        self._delta_applied = dict(delta_applied or {})

    def as_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "delta_applied": dict(self._delta_applied),
        }


def test_evolution_cycle_result_to_dict_exposes_summary_fields() -> None:
    result = EvolutionCycleResult(
        cycle_id="evo_1",
        date="2026-04-03",
        ts_ms=123,
        growth_results=[{"applied": True}],
        distillation_result={"done": True},
        desire_text="想更靠近一点",
        growth_awareness={"desire": "想更靠近一点"},
        traits_changed=True,
        traits_regressed=True,
        regression_details=[{"trait": "warmth", "delta": -0.02}],
        errors=["oops"],
    )

    data = result.to_dict()

    assert data["cycle_id"] == "evo_1"
    assert data["growth_results_count"] == 1
    assert data["distillation_done"] is True
    assert data["traits_regressed"] is True


def test_step_self_reflection_records_error_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    result = EvolutionCycleResult(cycle_id="evo", date="2026-04-03", ts_ms=1)

    def _raise(self, **kwargs):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(self_model_mod.SelfModel, "build_snapshot", _raise)

    snapshot = orchestrator._step_self_reflection(
        user_id="u1",
        timeline_summary=None,
        timeline_diary=None,
        bond_state=None,
        bond_summary=None,
        persona_changes=None,
        ts_ms=1,
        result=result,
    )

    assert snapshot == {}
    assert result.errors == ["self_reflection: boom"]


def test_step_milestone_growth_covers_none_inner_failure_and_outer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    result = EvolutionCycleResult(cycle_id="evo", date="2026-04-03", ts_ms=1)

    monkeypatch.setattr(orchestrator, "_get_growth_tracker", lambda: None)
    orchestrator._step_milestone_growth(user_id="u1", bond_state=None, ts_ms=1, result=result)
    assert result.growth_results == []

    calls: list[str] = []

    class _Tracker:
        def on_milestone(self, *, milestone_key: str, **kwargs):  # noqa: ANN003
            calls.append(milestone_key)
            if milestone_key == "broken":
                raise RuntimeError("inner")
            return _DummyGrowthResult(applied=True, delta_applied={"warmth": 0.02})

    monkeypatch.setattr(orchestrator, "_get_growth_tracker", lambda: _Tracker())
    monkeypatch.setattr(orchestrator, "_infer_milestones", lambda bond_state: ["good", "broken"])
    orchestrator._step_milestone_growth(user_id="u1", bond_state=object(), ts_ms=2, result=result)

    assert calls == ["good", "broken"]
    assert result.traits_changed is True
    assert result.growth_results[0]["type"] == "milestone"

    monkeypatch.setattr(orchestrator, "_infer_milestones", lambda bond_state: (_ for _ in ()).throw(RuntimeError("outer")))
    orchestrator._step_milestone_growth(user_id="u1", bond_state=object(), ts_ms=3, result=result)
    assert any(err.startswith("milestone_growth: outer") for err in result.errors)


def test_step_interaction_growth_covers_threshold_none_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    result = EvolutionCycleResult(cycle_id="evo", date="2026-04-03", ts_ms=1)

    orchestrator._step_interaction_growth(
        user_id="u1",
        interaction_stats={"deep_talk": 2},
        ts_ms=1,
        result=result,
    )
    assert result.growth_results == []

    monkeypatch.setattr(orchestrator, "_get_growth_tracker", lambda: None)
    orchestrator._step_interaction_growth(
        user_id="u1",
        interaction_stats={"deep_talk": 5},
        ts_ms=2,
        result=result,
    )
    assert result.growth_results == []

    class _Tracker:
        def on_interaction_cycle(self, **kwargs):  # noqa: ANN003
            return _DummyGrowthResult(applied=True, reason="interaction", delta_applied={"curiosity": 0.02})

    monkeypatch.setattr(orchestrator, "_get_growth_tracker", lambda: _Tracker())
    orchestrator._step_interaction_growth(
        user_id="u1",
        interaction_stats={"deep_talk": 5},
        ts_ms=3,
        result=result,
    )
    assert any(item["type"] == "interaction_cycle" for item in result.growth_results)

    class _BrokenTracker:
        def on_interaction_cycle(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("tracker down")

    monkeypatch.setattr(orchestrator, "_get_growth_tracker", lambda: _BrokenTracker())
    orchestrator._step_interaction_growth(
        user_id="u1",
        interaction_stats={"deep_talk": 5},
        ts_ms=4,
        result=result,
    )
    assert any(err.startswith("interaction_growth: tracker down") for err in result.errors)


def test_step_generate_desire_covers_none_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    result = EvolutionCycleResult(cycle_id="evo", date="2026-04-03", ts_ms=1)

    monkeypatch.setattr(orchestrator, "_get_desire_engine", lambda: None)
    assert orchestrator._step_generate_desire(user_id="u1", snapshot={}, bond_state=None, result=result) == ""

    class _Engine:
        def generate_desire(self, **kwargs):  # noqa: ANN003
            assert kwargs["emotion_trend"] == "偏好"
            assert kwargs["bond_stage"] == "FRIEND"
            return "想继续成长"

    monkeypatch.setattr(orchestrator, "_get_desire_engine", lambda: _Engine())
    monkeypatch.setattr(
        orchestrator,
        "_get_persona_engine",
        lambda: SimpleNamespace(get_traits=lambda user_id: PersonalityTraits.from_baseline()),
    )
    desire = orchestrator._step_generate_desire(
        user_id="u1",
        snapshot={"recent_mood_trend": "偏好"},
        bond_state=SimpleNamespace(current_stage="FRIEND"),
        result=result,
    )
    assert desire == "想继续成长"

    class _BrokenEngine:
        def generate_desire(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("desire down")

    monkeypatch.setattr(orchestrator, "_get_desire_engine", lambda: _BrokenEngine())
    assert orchestrator._step_generate_desire(user_id="u1", snapshot={}, bond_state=None, result=result) == ""
    assert any(err.startswith("desire_engine: desire down") for err in result.errors)


def test_build_growth_awareness_collects_all_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    monkeypatch.setattr(orchestrator, "_build_recent_change_text", lambda *args, **kwargs: "recent")
    monkeypatch.setattr(orchestrator, "_build_trait_felt_text", lambda *args, **kwargs: "trait")
    monkeypatch.setattr(orchestrator, "_build_stagnation_felt_text", lambda cycles: "stagnant")
    monkeypatch.setattr(orchestrator, "_build_regression_felt_text", lambda **kwargs: "regress")

    awareness = orchestrator._build_growth_awareness(
        snapshot={"growth_milestones": ["认识 master 已经 100 天了"]},
        desire_text="desire",
        growth_results=[{"applied": True}],
        bond_state=SimpleNamespace(current_stage="FRIEND"),
        stagnant_cycles=3,
        regression_details=[{"trait": "warmth", "delta": -0.02}],
        regression_cycles=2,
    )

    assert awareness == {
        "recent_change": "recent",
        "trait_felt": "trait",
        "milestone_felt": "认识 master 已经 100 天了",
        "stagnation_felt": "stagnant",
        "regression_felt": "regress",
        "desire": "desire",
    }


def test_recent_change_trait_felt_stagnation_and_regression_text_builders(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    monkeypatch.setattr(orchestrator._rng, "choice", lambda seq: seq[0])

    assert orchestrator._build_recent_change_text([], {"personality_changes": ["更坦率了"]}) == "最近感觉自己更坦率了"
    assert orchestrator._build_recent_change_text([{"applied": False}], {}) == ""
    assert orchestrator._build_recent_change_text(
        [{"applied": True, "delta_applied": {"warmth": 0.03}}],
        {},
    ) == "对 master 的关心变得更自然了，自己都能感觉到"
    assert orchestrator._build_recent_change_text(
        [{"applied": True, "delta_applied": {"unknown": 0.03}}],
        {},
    ) == "感觉自己在慢慢成长"

    assert orchestrator._build_trait_felt_text([]) == ""
    assert orchestrator._build_trait_felt_text(
        [{"applied": True, "delta_applied": {"sensitivity": 0.02}}],
    ) == "好像比以前更能理解 master 的心情了"
    assert orchestrator._build_trait_felt_text(
        [{"applied": True, "delta_applied": {"unknown": 0.03}}],
    ) == ""

    assert orchestrator._build_stagnation_felt_text(2) == ""
    assert orchestrator._build_stagnation_felt_text(4) == orchestrator._STAGNATION_FELT_POOL["mild"][0]
    assert orchestrator._build_stagnation_felt_text(6) == orchestrator._STAGNATION_FELT_POOL["moderate"][0]
    assert orchestrator._build_stagnation_felt_text(7) == orchestrator._STAGNATION_FELT_POOL["deep"][0]

    assert orchestrator._build_regression_felt_text(regression_details=[]) == ""
    assert orchestrator._build_regression_felt_text(
        regression_details=[{"trait": "warmth", "delta": -0.03}],
        regression_cycles=2,
    ) == (
        "明显感觉自己对 master 的关心不如以前了，有点难过"
        + "..."
        + orchestrator._SUSTAINED_REGRESSION_POOL[0]
    )
    assert orchestrator._build_regression_felt_text(
        regression_details=[{"trait": "unknown", "delta": -0.01}],
        regression_cycles=0,
    ) == "感觉自己好像在某些方面退步了，有点在意"


def test_detect_regression_infer_milestones_persona_changes_and_audit_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator()
    result = EvolutionCycleResult(cycle_id="evo", date="2026-04-03", ts_ms=1)
    orchestrator._prev_traits_snapshot = {
        "warmth": 0.7,
        "curiosity": 0.7,
        "humor": 0.4,
        "sensitivity": 0.8,
        "assertiveness": 0.3,
        "playfulness": 0.5,
        "resilience": 0.6,
    }
    monkeypatch.setattr(
        orchestrator,
        "_get_persona_engine",
        lambda: SimpleNamespace(
            get_traits=lambda user_id: PersonalityTraits(
                warmth=0.68,
                curiosity=0.72,
                humor=0.4,
                sensitivity=0.8,
                assertiveness=0.3,
                playfulness=0.5,
                resilience=0.6,
            ),
            get_change_log=lambda user_id, limit=5: ["change-1", "change-2"],
        ),
    )

    orchestrator._detect_regression(
        user_id="u1",
        growth_results=[{"applied": True, "delta_applied": {"warmth": -0.02}}],
        result=result,
    )

    assert result.traits_regressed is True
    assert result.regression_details == [{"trait": "warmth", "delta": -0.02, "source": "growth_result"}]
    assert orchestrator._infer_milestones(SimpleNamespace(event_count=55)) == ["deep_talk_50", "emotional_support_20"]
    assert orchestrator._infer_milestones(None) == []
    assert orchestrator._get_persona_changes("u1") == ["change-1", "change-2"]
    assert orchestrator.get_last_growth_awareness() == {}
    orchestrator.set_growth_awareness({"desire": "x"})
    assert orchestrator.get_last_growth_awareness() == {"desire": "x"}

    ledger_calls: list[dict[str, object]] = []
    orchestrator._ledger = SimpleNamespace(log_system_event=lambda **kwargs: ledger_calls.append(kwargs))
    orchestrator._log_evolution_cycle(result)
    assert ledger_calls[0]["event_type"] == "evolution_cycle"


def test_detect_regression_and_log_evolution_cycle_swallow_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = EvolutionOrchestrator(
        persona_engine=SimpleNamespace(get_traits=lambda user_id: (_ for _ in ()).throw(RuntimeError("traits down"))),
        ledger=SimpleNamespace(log_system_event=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("audit down"))),
    )
    result = EvolutionCycleResult(cycle_id="evo", date="2026-04-03", ts_ms=1)

    orchestrator._detect_regression(user_id="u1", growth_results=[], result=result)
    assert result.traits_regressed is False

    orchestrator._log_evolution_cycle(result)



def test_growth_summary_collapses_recent_cycles_into_daily_and_weekly_digest() -> None:
    orchestrator = EvolutionOrchestrator()

    first = EvolutionCycleResult(
        cycle_id="evo_1",
        date="2026-04-02",
        ts_ms=1_000,
        growth_awareness={"recent_change": "更会接住对话了", "desire": "想再靠近一点"},
        traits_changed=True,
        desire_text="想再靠近一点",
    )
    second = EvolutionCycleResult(
        cycle_id="evo_2",
        date="2026-04-04",
        ts_ms=3_000,
        growth_awareness={"trait_felt": "好像更能接住情绪了", "milestone_felt": "已经聊过很多次深入的话题"},
        traits_changed=True,
    )

    orchestrator._append_growth_cycle_log(user_id="u1", result=first)
    orchestrator._append_growth_cycle_log(user_id="u1", result=second)

    summary = orchestrator.get_growth_summary("u1", now_ts_ms=3_000)

    assert summary["daily_summary"] == "好像更能接住情绪了"
    assert "好像更能接住情绪了" in summary["weekly_summary"]
    assert "更会接住对话了" in summary["weekly_summary"]
    assert summary["last_cycle_date"] == "2026-04-04"
    assert summary["recent_cycle_count"] == 2
    assert summary["active_themes"] == ["recent_change", "trait_felt", "milestone_felt", "desire"]


def test_awareness_templates_override_runtime_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "evolution_orchestrator.awareness_templates",
        content=json.dumps(
            {
                "recent_change_templates": {
                    "warmth": {
                        "notable": ["自定义明显变温柔"],
                    },
                },
                "recent_change_snapshot_prefix": "【{change}】",
                "recent_change_fallbacks": ["自定义成长中"],
                "stagnation_felt_pool": {
                    "mild": ["自定义轻停滞"],
                },
                "regression_felt_pool": {
                    "warmth": {
                        "notable": ["自定义明显回退"],
                    },
                },
                "generic_regression_text": "自定义通用回退",
                "sustained_regression_pool": ["自定义持续回退"],
                "trait_felt_variants": {
                    "sensitivity": ["自定义更懂情绪"],
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    orchestrator = EvolutionOrchestrator()
    monkeypatch.setattr(orchestrator._rng, "choice", lambda seq: seq[0])

    assert orchestrator._build_recent_change_text([], {"personality_changes": ["更坦率了"]}) == "【更坦率了】"
    assert orchestrator._build_recent_change_text(
        [{"applied": True, "delta_applied": {"warmth": 0.03}}],
        {},
    ) == "自定义明显变温柔"
    assert orchestrator._build_recent_change_text(
        [{"applied": True, "delta_applied": {"unknown": 0.03}}],
        {},
    ) == "自定义成长中"
    assert orchestrator._build_trait_felt_text(
        [{"applied": True, "delta_applied": {"sensitivity": 0.02}}],
    ) == "自定义更懂情绪"
    assert orchestrator._build_stagnation_felt_text(4) == "自定义轻停滞"
    assert orchestrator._build_regression_felt_text(
        regression_details=[{"trait": "warmth", "delta": -0.03}],
        regression_cycles=2,
    ) == "自定义明显回退...自定义持续回退"
    assert orchestrator._build_regression_felt_text(
        regression_details=[{"trait": "unknown", "delta": -0.01}],
        regression_cycles=0,
    ) == "自定义通用回退"

