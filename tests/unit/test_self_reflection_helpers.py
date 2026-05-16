from __future__ import annotations

import json
from types import SimpleNamespace

import host_brain.persona.self_reflection as sr_mod
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
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
    trigger_templates = resolve_prompt_template_json_merged("self_reflection.trigger_reason_templates")

    pulse = reflection.maybe_generate(
        turn_count=7,
        current_emotion="sad",
        bond_state=SimpleNamespace(current_stage="CLOSE_FRIEND"),
        weekly_talk_count=5,
        growth_awareness={"trait_felt": "更柔和"},
    )

    assert pulse is not None
    assert pulse["trigger_reason"] == trigger_templates["emotion"]["SAD"][0]
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
    trigger_templates = resolve_prompt_template_json_merged("self_reflection.trigger_reason_templates")

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
    ) == trigger_templates["emotion"]["SAD"][-1]
    assert reflection._pick_trigger_reason(
        emotion="",
        event_driven=False,
        time_context="久别重逢",
    ) == trigger_templates["time_aware"]["久别重逢"][-1]
    assert reflection._pick_trigger_reason(
        emotion="",
        event_driven=False,
        time_context="",
    ) == trigger_templates["scheduled"][-1]


def test_trigger_reason_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "self_reflection.trigger_reason_templates",
        content=json.dumps(
            {
                "emotion": {
                    "SAD": ["自定义难过触发"],
                },
                "scheduled": ["自定义定时触发"],
                "time_aware": {
                    "久别重逢": ["自定义久别重逢"],
                },
                "force_generate_reason": "自定义强制触发",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    reflection = SelfReflection(seed=0)
    reflection._rng = SimpleNamespace(choice=lambda seq: seq[0])

    assert reflection._pick_trigger_reason(
        emotion="SAD",
        event_driven=True,
        time_context="久别重逢",
    ) == "自定义难过触发"
    assert reflection._pick_trigger_reason(
        emotion="",
        event_driven=False,
        time_context="久别重逢",
    ) == "自定义久别重逢"
    assert reflection._pick_trigger_reason(
        emotion="",
        event_driven=False,
        time_context="",
    ) == "自定义定时触发"

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
    pulse = reflection.force_generate()
    assert pulse["trigger_reason"] == "自定义强制触发"


def test_builtin_self_reflection_templates_are_clean() -> None:
    trigger_templates = resolve_prompt_template_json_merged("self_reflection.trigger_reason_templates")
    guardrail_templates = resolve_prompt_template_json_merged("self_reflection.narrative_guardrail_texts")

    assert trigger_templates["force_generate_reason"] == "强制触发（测试）"
    assert trigger_templates["scheduled"][0] == "到了该停下来想想的时候了"
    assert guardrail_templates["directive_high_risk"].startswith("涉及自我叙事时")
    assert guardrail_templates["directive_reflection_vs_policy_tension"].startswith("当前以主回复任务")


def test_build_self_reflection_pulse_injects_optional_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, time_context="", llm_fn=None, style_evolution=None, strict_llm=False: "aware",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, llm_fn=None, strict_llm=False: "mood",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, llm_fn=None, style_evolution=None, strict_llm=False: "grow",
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
        lambda snapshot, time_context="", llm_fn=None, style_evolution=None, strict_llm=False: "aware",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, llm_fn=None, strict_llm=False: "mood",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, llm_fn=None, style_evolution=None, strict_llm=False: "",
    )
    monkeypatch.setattr(sr_mod, "detect_contradictions", lambda snapshot: [])
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "")

    pulse = build_self_reflection_pulse({"bond_growth_perception": "稳定"})

    guardrails = dict(pulse["narrative_guardrails"])
    assert guardrails["risk_level"] == "high"
    assert guardrails["allowed_style"] == "brief"
    assert guardrails["allow_self_narrative_expansion"] is False
    assert guardrails["max_self_narrative_sentences"] == 1
    assert "time_context_unverified" in guardrails["risk_tags"]
    assert "unsupported_self_claim" in guardrails["risk_tags"]
    assert "anthropomorphic_overreach" in guardrails["risk_tags"]
    assert any("最近/这段时间" in item for item in guardrails["prompt_directives"])
    assert any("人类式经历" in item for item in guardrails["prompt_directives"])
    assert any("不要主动展开自我叙事" in item for item in guardrails["prompt_directives"])



def test_build_self_reflection_pulse_uses_guardrail_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "self_reflection.narrative_guardrail_texts",
        content=json.dumps(
            {
                "instructions": {
                    "time_context_unverified": "自定义时间证据不足说明",
                },
                "directive_high_risk": "自定义高风险收束",
                "directive_time_context_unverified": "自定义最近/这段时间限制",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, time_context="", llm_fn=None, style_evolution=None, strict_llm=False: "aware",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, llm_fn=None, strict_llm=False: "mood",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, llm_fn=None, style_evolution=None, strict_llm=False: "",
    )
    monkeypatch.setattr(sr_mod, "detect_contradictions", lambda snapshot: [])
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "")

    pulse = build_self_reflection_pulse({"bond_growth_perception": "稳定"})
    guardrails = dict(pulse["narrative_guardrails"])

    assert "自定义时间证据不足说明" in guardrails["instruction"]
    assert "自定义高风险收束" in guardrails["prompt_directives"]
    assert "自定义最近/这段时间限制" in guardrails["prompt_directives"]


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
        render_policy={"should_be_brief": True},
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
    assert any("主回复任务和既有边界为先" in item for item in guardrails["prompt_directives"])



def test_align_self_reflection_pulse_prefers_guarded_tone_when_retreat_dominates() -> None:
    aligned = sr_mod.align_self_reflection_pulse(
        {"self_awareness": "原始自省", "mood_awareness": "mood", "growth_note": "grow"},
        snapshot={"bond_growth_perception": "有些疏远"},
        offline_state={"offline_hours": 2.0},
        bond_summary={"stage": "CLOSE_FRIEND"},
        motive_state={
            "reach_out_drive": 0.18,
            "care_drive": 0.22,
            "continuity_drive": 0.16,
            "withdraw_drive": 0.72,
            "self_protect_drive": 0.78,
        },
        render_policy={"should_be_withdrawn": True, "withdrawal_guard": True},
        runtime_flags={"sys_mode": "focus"},
    )

    assert "收短一点" in aligned["self_awareness"] or "放轻一点" in aligned["self_awareness"]
    assert "mode:focus" in aligned["state_alignment_reasons"]
    assert "motive:guarded" in aligned["state_alignment_reasons"]
    assert "policy:withdrawn" in aligned["state_alignment_reasons"]


def test_build_style_trend_note_uses_runtime_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "self_reflection.style_trend_templates",
        content=json.dumps(
            {
                "trait_cn": {
                    "warmth": "柔软气息",
                },
                "text_pool": {
                    "rising": ["自定义上升：{trait}"],
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    monkeypatch.setattr(sr_mod.random, "choice", lambda seq: seq[0])

    text = sr_mod._build_style_trend_note({"warmth": "rising"})

    assert text == "自定义上升：柔软气息"


def test_build_style_trend_note_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "persona.self_narrative_prompts",
        content=json.dumps(
            {
                "style_trend_prompt": "自定义风格趋势：{hint_str}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: dict[str, str] = {}

    def _fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return "风格在变化"

    text = sr_mod._build_style_trend_note(
        {"warmth": "rising", "humor": "falling"},
        llm_fn=_fake_llm,
    )

    assert text == "风格在变化"
    assert captured["prompt"].startswith("自定义风格趋势：")


def test_build_self_reflection_pulse_strict_llm_disables_template_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, **kwargs: "",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, **kwargs: "",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, **kwargs: "",
    )
    monkeypatch.setattr(sr_mod, "detect_contradictions", lambda snapshot: [])
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "")

    pulse = sr_mod.build_self_reflection_pulse(
        {"bond_growth_perception": "稳定"},
        llm_fn=lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")),
        strict_llm=True,
    )

    assert pulse["self_awareness"] == ""
    assert pulse["mood_awareness"] == ""
    assert pulse["growth_note"] == ""


def test_build_style_trend_note_strict_llm_returns_empty_on_failure() -> None:
    text = sr_mod._build_style_trend_note(
        {"warmth": "rising"},
        llm_fn=lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")),
        strict_llm=True,
    )

    assert text == ""


def test_self_reflection_localized_ja_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "self_reflection.trigger_reason_templates.ja",
        content=json.dumps(
            {
                "emotion": {
                    "SAD": ["少し胸に残っているので、静かに考えています。"],
                },
                "scheduled": ["そろそろ自分の気持ちを見ておきたい頃合いです。"],
                "time_aware": {
                    "久别重逢": ["久しぶりに向き合えたので、今の気持ちを受け止めています。"],
                },
                "force_generate_reason": "強制トリガー（テスト）",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )
    store.upsert_template(
        "self_reflection.narrative_guardrail_texts.ja",
        content=json.dumps(
            {
                "instructions": {
                    "time_context_unverified": "時間は最近/このところに留めること。",
                },
                "directive_high_risk": "自己叙事は一言に留めること。",
                "directive_time_context_unverified": "具体的な日付や長さを断定しないこと。",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )
    store.upsert_template(
        "self_reflection.style_trend_templates.ja",
        content=json.dumps(
            {
                "trait_cn": {
                    "warmth": "やわらかさ",
                },
                "text_pool": {
                    "rising": ["最近、自分の{trait}が少し増しています。"],
                    "falling": ["最近は{trait}を少し抑えています。"],
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    monkeypatch.setattr(sr_mod.random, "choice", lambda seq: seq[0])
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, **kwargs: "aware-ja",  # noqa: ARG005
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, **kwargs: "mood-ja",  # noqa: ARG005
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, **kwargs: "grow-ja",  # noqa: ARG005
    )
    monkeypatch.setattr(sr_mod, "detect_contradictions", lambda snapshot: [])  # noqa: ARG005
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "")  # noqa: ARG005

    reflection = SelfReflection(seed=0)
    reason = reflection._pick_trigger_reason(
        emotion="SAD",
        event_driven=True,
        time_context="久别重逢",
        render_target_lang="ja",
    )
    assert reason == "少し胸に残っているので、静かに考えています。"

    pulse = sr_mod.build_self_reflection_pulse(
        {"bond_growth_perception": "稳定"},
        style_evolution={"trend": {"warmth": "rising"}, "history_count": 25},
        render_target_lang="ja",
    )
    assert pulse["self_awareness"] == "aware-ja"
    assert pulse["mood_awareness"] == "mood-ja"
    assert pulse["growth_note"] == "grow-ja"
    assert pulse["style_trend_note"] == "最近、自分のやわらかさが少し増しています。"
    assert "時間は最近/このところに留めること。" in pulse["narrative_guardrails"]["instruction"]
    assert any(
        item in pulse["narrative_guardrails"]["prompt_directives"]
        for item in (
            "自己叙事は一言に留めること。",
            "具体的な日付や長さを断定しないこと。",
        )
    )
