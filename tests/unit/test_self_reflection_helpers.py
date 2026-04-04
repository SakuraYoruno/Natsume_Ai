from __future__ import annotations

from types import SimpleNamespace

import host_brain.persona.self_reflection as sr_mod
from host_brain.persona.self_reflection import SelfReflection, build_self_reflection_pulse


def test_should_trigger_by_emotion_respects_thresholds_and_dedup() -> None:
    reflection = SelfReflection(min_interval=10, max_interval=10, seed=0)
    reflection._next_trigger_turn = 10

    assert reflection.should_trigger_by_emotion(7, "") is False
    assert reflection.should_trigger_by_emotion(7, "neutral") is False

    reflection._last_event_emotion = "SAD"
    assert reflection.should_trigger_by_emotion(7, "sad") is False

    reflection._last_event_emotion = ""
    assert reflection.should_trigger_by_emotion(7, "sad") is True
    assert reflection.should_trigger_by_emotion(10, "sad") is False


def test_maybe_generate_event_driven_updates_state(monkeypatch) -> None:
    reflection = SelfReflection(min_interval=10, max_interval=10, seed=0)
    reflection._next_trigger_turn = 10
    reflection._rng = SimpleNamespace(choice=lambda seq: seq[0])

    monkeypatch.setattr(
        reflection,
        "_roll_next_trigger",
        lambda: setattr(reflection, "_next_trigger_turn", reflection._last_trigger_turn + 4),
    )
    monkeypatch.setattr(
        reflection._model,
        "build_snapshot",
        lambda **kwargs: {"bond_growth_perception": "稳定", "raw": kwargs},
    )
    monkeypatch.setattr(
        sr_mod,
        "build_self_reflection_pulse",
        lambda snapshot, **kwargs: {"self_awareness": "ok", **kwargs},
    )

    pulse = reflection.maybe_generate(
        turn_count=7,
        current_emotion="sad",
        bond_state=SimpleNamespace(current_stage="CLOSE_FRIEND"),
        weekly_talk_count=5,
        growth_awareness={"trait_felt": "更柔和"},
    )

    assert pulse is not None
    assert pulse["trigger_reason"] == sr_mod._EMOTION_TRIGGER_REASONS["SAD"][0]
    assert reflection.last_trigger_turn == 7
    assert reflection.next_trigger_turn == 11
    assert reflection._last_event_emotion == "SAD"


def test_maybe_generate_returns_none_when_snapshot_build_fails(monkeypatch) -> None:
    reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
    monkeypatch.setattr(
        reflection._model,
        "build_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert reflection.maybe_generate(turn_count=1) is None


def test_time_context_buckets_and_trigger_reason_priority() -> None:
    reflection = SelfReflection(seed=0)
    reflection._rng = SimpleNamespace(choice=lambda seq: seq[-1])
    one_day_ms = 24 * 3600 * 1000

    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=1_000,
        current_ts_ms=1_000 + 8 * one_day_ms,
        weekly_talk_count=0,
    ) == "久别重逢"
    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=1_000,
        current_ts_ms=1_000 + 4 * one_day_ms,
        weekly_talk_count=0,
    ) == "短暂分离"
    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=0,
        current_ts_ms=0,
        weekly_talk_count=6,
    ) == "频繁互动"
    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=0,
        current_ts_ms=0,
        weekly_talk_count=1,
    ) == "稀少互动"

    assert reflection._pick_trigger_reason(
        emotion="SAD",
        event_driven=True,
        time_context="久别重逢",
    ) == sr_mod._EMOTION_TRIGGER_REASONS["SAD"][-1]
    assert reflection._pick_trigger_reason(
        emotion="",
        event_driven=False,
        time_context="久别重逢",
    ) == sr_mod._TIME_AWARE_TRIGGER_REASONS["久别重逢"][-1]
    assert reflection._pick_trigger_reason(
        emotion="",
        event_driven=False,
        time_context="",
    ) == sr_mod._SCHEDULED_TRIGGER_REASONS[-1]


def test_build_self_reflection_pulse_injects_optional_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, time_context="", llm_fn=None, style_evolution=None: "aware",
    )
    monkeypatch.setattr(sr_mod, "build_mood_awareness_text", lambda snapshot, llm_fn=None: "mood")
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, llm_fn=None, style_evolution=None: "grow",
    )
    monkeypatch.setattr(
        sr_mod,
        "detect_contradictions",
        lambda snapshot: [{"type": "push_pull"}, {"type": "fear"}],
    )
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "contradiction")

    pulse = build_self_reflection_pulse(
        {"bond_growth_perception": "稳定"},
        growth_awareness={
            "recent_change": "更坦率了",
            "trait_felt": "",
            "milestone_felt": "被珍惜",
            "desire": None,
        },
        growth_summary={
            "daily_summary": "今天更会把关系接稳。",
            "weekly_summary": "这周更敢表达在意，也更会接住情绪。",
            "active_themes": ["recent_change", "desire"],
            "recent_cycle_count": 3,
            "last_cycle_date": "2026-04-04",
        },
        trigger_reason="因为刚刚很想你",
        time_context="久别重逢",
    )

    assert pulse["contradiction_awareness"] == "contradiction"
    assert pulse["contradiction_types"] == ["push_pull", "fear"]
    assert pulse["trigger_reason"] == "因为刚刚很想你"
    assert pulse["time_context"] == "久别重逢"
    assert pulse["growth_awareness"] == {
        "recent_change": "更坦率了",
        "milestone_felt": "被珍惜",
    }
    assert pulse["growth_summary"] == {
        "daily_summary": "今天更会把关系接稳。",
        "weekly_summary": "这周更敢表达在意，也更会接住情绪。",
        "active_themes": ["recent_change", "desire"],
        "recent_cycle_count": 3,
        "last_cycle_date": "2026-04-04",
    }



def test_build_self_reflection_pulse_adds_narrative_guardrails_when_evidence_is_thin(monkeypatch) -> None:
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, time_context="", llm_fn=None, style_evolution=None: "aware",
    )
    monkeypatch.setattr(sr_mod, "build_mood_awareness_text", lambda snapshot, llm_fn=None: "mood")
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, llm_fn=None, style_evolution=None: "",
    )
    monkeypatch.setattr(sr_mod, "detect_contradictions", lambda snapshot: [])
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "")

    pulse = build_self_reflection_pulse({"bond_growth_perception": "稳定"})

    guardrails = dict(pulse["narrative_guardrails"])
    assert guardrails["risk_level"] == "high"
    assert guardrails["allowed_style"] == "brief"
    assert guardrails["allow_self_narrative_expansion"] is False
    assert "time_context_unverified" in guardrails["risk_tags"]
    assert "unsupported_self_claim" in guardrails["risk_tags"]
    assert "anthropomorphic_overreach" in guardrails["risk_tags"]



def test_align_self_reflection_pulse_reconciles_reconnect_with_brief_policy() -> None:
    aligned = sr_mod.align_self_reflection_pulse(
        {"self_awareness": "原始自省", "mood_awareness": "mood", "growth_note": "grow"},
        snapshot={"bond_growth_perception": "稳定"},
        offline_state={"offline_hours": 80.0},
        bond_summary={"stage": "FRIEND"},
        motive_state={
            "reach_out_drive": 0.42,
            "care_drive": 0.48,
            "continuity_drive": 0.82,
            "withdraw_drive": 0.18,
            "self_protect_drive": 0.12,
        },
        response_policy={"should_be_brief": True},
        runtime_flags={"sys_mode": "normal"},
    )

    assert aligned["self_awareness"] != "原始自省"
    assert "接住" in aligned["self_awareness"]
    assert "说得短一点" in aligned["self_awareness"]
    assert aligned["state_alignment"] == aligned["self_awareness"]
    assert "offline:72h_plus" in aligned["state_alignment_reasons"]
    assert "motive:continuity" in aligned["state_alignment_reasons"]
    assert "policy:brief" in aligned["state_alignment_reasons"]
    guardrails = dict(aligned["narrative_guardrails"])
    assert guardrails["risk_level"] == "high"
    assert "reflection_vs_policy_tension" in guardrails["risk_tags"]



def test_align_self_reflection_pulse_prefers_guarded_tone_when_retreat_dominates() -> None:
    aligned = sr_mod.align_self_reflection_pulse(
        {"self_awareness": "原始自省", "mood_awareness": "mood", "growth_note": "grow"},
        snapshot={"bond_growth_perception": "有些疏远"},
        offline_state={"offline_hours": 2.0},
        bond_summary={"stage": "ACQUAINTANCE"},
        motive_state={
            "reach_out_drive": 0.18,
            "care_drive": 0.22,
            "continuity_drive": 0.16,
            "withdraw_drive": 0.72,
            "self_protect_drive": 0.78,
        },
        response_policy={"should_be_withdrawn": True, "withdrawal_guard": True},
        runtime_flags={"sys_mode": "focus"},
    )

    assert "收短一点" in aligned["self_awareness"] or "放轻一点" in aligned["self_awareness"]
    assert "mode:focus" in aligned["state_alignment_reasons"]
    assert "motive:guarded" in aligned["state_alignment_reasons"]
    assert "policy:withdrawn" in aligned["state_alignment_reasons"]
