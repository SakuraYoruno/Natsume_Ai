from __future__ import annotations

import json
from dataclasses import dataclass

import host_brain.persona.self_model as sm
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


@dataclass
class _Record:
    delta: dict[str, float]


@dataclass
class _BondState:
    current_stage: str = "FRIEND"
    event_count: int = 0


def _install_store(monkeypatch, template_id: str, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        template_id,
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_compute_emotional_stability_accounts_for_flip_count_and_negative_duration() -> None:
    model = sm.SelfModel()

    stability = model._compute_emotional_stability(
        {"dominant_emotion": "SAD"},
        {
            "emotion_arc": {"start": "HAPPY", "end": "SAD"},
            "emotion_sequence": ["HAPPY", "SAD", "HAPPY", "ANXIOUS"],
            "dominant_emotion": "SAD",
            "dominant_duration_ratio": 0.9,
        },
    )

    assert 0.0 <= stability < 0.5


def test_extract_personality_changes_and_growth_milestones_cover_negative_and_large_counts() -> None:
    model = sm.SelfModel()
    changes = [
        _Record({"warmth": 0.03}),
        _Record({"humor": -0.02}),
        _Record({"resilience": 0.01}),
    ]

    extracted = model._extract_personality_changes(changes)
    milestones = model._extract_growth_milestones(
        _BondState(current_stage="SOULMATE", event_count=120),
        changes,
    )

    assert any("收敛" in text for text in extracted)
    assert any("120 次互动" in text for text in milestones)
    assert any("人格在慢慢成长中" in text for text in milestones)


def test_detect_contradictions_covers_low_mood_and_growth_paths(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "偏低",
            "bond_growth_perception": "变亲近",
            "emotional_stability": 0.6,
            "personality_changes": ["变得更温柔了"],
        }
    )

    assert [item["type"] for item in contradictions] == [
        "mood_low_bond_up",
        "growing_but_sad",
    ]


def test_detect_contradictions_covers_bond_down_paths(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "偏好",
            "bond_growth_perception": "有些疏远",
            "emotional_stability": 0.9,
            "personality_changes": ["变得更温柔了"],
        }
    )

    assert [item["type"] for item in contradictions] == [
        "mood_up_bond_down",
        "stable_but_bond_down",
    ]


def test_detect_contradictions_covers_volatile_but_bond_stable(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "平稳",
            "bond_growth_perception": "稳定",
            "emotional_stability": 0.2,
            "personality_changes": [],
        }
    )

    assert contradictions[0]["type"] == "volatile_but_bond_stable"


def test_contradiction_text_and_pool_helpers_cover_empty_paths() -> None:
    assert sm.build_contradiction_awareness_text([{}]) == ""
    assert sm.build_contradiction_awareness_text(
        [{"text": "a"}, {"text": "b"}]
    ) == "a。b"
    assert sm._pick_from_pool([]) == ""


def test_build_self_awareness_text_prefers_time_context(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "random", lambda: 0.1)
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    text = sm.build_self_awareness_text(
        {
            "recent_mood_trend": "偏好",
            "bond_growth_perception": "变亲近",
            "event_count": 99,
            "growth_milestones": ["不应走到这里"],
        },
        time_context="久别重逢",
    )

    assert "好久没见了" in text


def test_build_mood_awareness_text_uses_normal_pool(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    text = sm.build_mood_awareness_text(
        {
            "dominant_recent_emotion": "THINKING",
            "emotional_stability": 0.5,
        }
    )

    assert "思考" in text


def test_self_model_narrative_text_pools_use_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "self_model.narrative_text_pools",
        {
            "mood_text_pool": {
                "偏好": ["自定义好心情"],
            },
            "bond_text_pool": {
                "变亲近": ["自定义更亲近"],
            },
            "personality_change_templates": {
                "positive": "自定义变得更{trait_cn}了",
                "negative": "自定义{trait_cn}收敛",
            },
            "growth_milestone_templates": {
                "stage_labels": {
                    "SOULMATE": "灵魂伴侣",
                },
                "relationship_stage": "自定义和 master 已经成为{stage_cn}了",
                "event_count_high": "自定义已经有 {event_count} 次互动了",
                "event_count_mid": "自定义互动超过 {threshold} 次了",
                "personality_growth": "自定义人格在慢慢成长中",
            },
            "time_context_text_pool": {
                "久别重逢": ["自定义重逢"],
            },
            "contradiction_text_pool": {
                "mood_low_bond_up": ["自定义矛盾"],
            },
            "mood_awareness_pool": {
                "stable": ["情绪模板::{emotion}"],
            },
            "growth_note_wrappers": ["包装::{change}"],
        },
    )
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])
    monkeypatch.setattr(sm.random, "random", lambda: 0.1)

    assert sm.build_self_awareness_text(
        {
            "recent_mood_trend": "偏好",
            "bond_growth_perception": "变亲近",
            "event_count": 99,
        },
        time_context="久别重逢",
    ) == "自定义好心情，自定义更亲近，自定义重逢"

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "偏低",
            "bond_growth_perception": "变亲近",
            "emotional_stability": 0.6,
            "personality_changes": ["变得更温柔了"],
        }
    )
    assert contradictions[0]["text"] == "自定义矛盾"

    assert sm.build_mood_awareness_text(
        {
            "dominant_recent_emotion": "HAPPY",
            "emotional_stability": 0.8,
        }
    ) == "情绪模板::开心"

    assert sm.build_growth_note_text(
        {
            "personality_changes": ["更坦率了"],
            "growth_milestones": [],
        }
    ) == "包装::更坦率了"

    model = sm.SelfModel()
    changes = model._extract_personality_changes([_Record({"warmth": 0.03})])
    assert changes == ["自定义变得更温柔了"]

    milestones = model._extract_growth_milestones(
        _BondState(current_stage="SOULMATE", event_count=120),
        [_Record({"warmth": 0.03}), _Record({"humor": 0.02}), _Record({"resilience": 0.01})],
    )
    assert "自定义和 master 已经成为灵魂伴侣了" in milestones
    assert "自定义已经有 120 次互动了" in milestones
    assert "自定义人格在慢慢成长中" in milestones


def test_build_state_aligned_self_awareness_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "self_model.state_alignment_texts",
        content=json.dumps(
            {
                "bond_perception_anchor": {
                    "steady_default": "自定义稳住联系",
                },
                "absence_anchor": {
                    "72h_plus": "自定义几天后",
                },
                "reason_suffix": {
                    "brief": "自定义简短收束",
                },
                "intent_text": {
                    "continuity_default": "自定义接住这轮",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    aligned = sm.build_state_aligned_self_awareness(
        {"bond_growth_perception": "稳定"},
        offline_state={"offline_hours": 80.0},
        bond_summary={"stage": "ACQUAINTANCE"},
        motive_state={"continuity_drive": 0.8},
        render_policy={"should_be_brief": True},
        runtime_flags={"sys_mode": "normal"},
    )

    assert aligned["self_awareness"] == "自定义几天后，自定义稳住联系，自定义接住这轮，自定义简短收束。"
    assert "offline:72h_plus" in aligned["reason_tags"]
    assert "motive:continuity" in aligned["reason_tags"]
    assert "policy:brief" in aligned["reason_tags"]


def test_self_model_llm_prompts_use_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "persona.self_narrative_prompts",
        content=json.dumps(
            {
                "self_awareness_prompt": "自定义自我感知：{mood_trend}/{emotion_cn}/{bond_perception}",
                "mood_awareness_prompt": "自定义情绪感知：{emotion_cn}/{stability_desc}",
                "growth_note_prompt": "自定义成长感知：{context}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: list[str] = []

    def _fake_llm(prompt: str) -> str:
        captured.append(prompt)
        return "这是生成成功"

    assert sm.build_self_awareness_text(
        {
            "recent_mood_trend": "偏好",
            "dominant_recent_emotion": "HAPPY",
            "bond_growth_perception": "变亲近",
        },
        llm_fn=_fake_llm,
    ) == "这是生成成功"
    assert sm.build_mood_awareness_text(
        {
            "dominant_recent_emotion": "HAPPY",
            "emotional_stability": 0.8,
        },
        llm_fn=_fake_llm,
    ) == "这是生成成功"
    assert sm.build_growth_note_text(
        {
            "personality_changes": ["变得更温柔了"],
            "growth_milestones": [],
        },
        llm_fn=_fake_llm,
    ) == "这是生成成功"

    assert captured[0].startswith("自定义自我感知：")
    assert captured[1].startswith("自定义情绪感知：")
    assert captured[2].startswith("自定义成长感知：")


def test_builtin_self_model_narrative_text_pools_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("self_model.narrative_text_pools")
    assert templates["mood_text_pool"]["偏好"][0] == "最近心情挺好的"
    assert templates["bond_text_pool"]["变亲近"][0] == "感觉和 master 的关系在慢慢变好"
    assert templates["time_context_text_pool"]["久别重逢"][0] == "好久没见了，重新在一起感觉有点不真实"
    assert templates["mood_awareness_pool"]["stable"][0] == "今天整体感觉{emotion}，情绪比较稳定"
    assert templates["growth_note_wrappers"][0] == "感觉自己{change}"


def test_self_model_localized_ja_templates_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "self_model.narrative_text_pools.ja",
        content=json.dumps(
            {
                "mood_text_pool": {
                    "偏好": ["最近は少し軽いです。"],
                },
                "bond_text_pool": {
                    "变亲近": ["masterとの距離が少し近いです。"],
                },
                "time_context_text_pool": {
                    "久别重逢": ["久しぶりに話せて、少し胸がほどけます。"],
                },
                "mood_awareness_pool": {
                    "stable": ["今日は{emotion}のまま静かです。"],
                },
                "growth_note_wrappers": ["最近、自分が{change}気がします。"],
                "emotion_cn": {
                    "HAPPY": "うれしい",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )
    store.upsert_template(
        "self_model.state_alignment_texts.ja",
        content=json.dumps(
            {
                "bond_perception_anchor": {
                    "steady_default": "このやり取りの温度を保っていたいです。",
                },
                "absence_anchor": {
                    "72h_plus": "数日ぶりに",
                },
                "reason_suffix": {
                    "brief": "だから今は短く返したいです。",
                },
                "intent_text": {
                    "continuity_default": "このやり取りを静かにつなぎたいです。",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])
    monkeypatch.setattr(sm.random, "random", lambda: 0.1)

    text = sm.build_self_awareness_text(
        {
            "recent_mood_trend": "偏好",
            "bond_growth_perception": "变亲近",
            "event_count": 99,
        },
        time_context="久别重逢",
        render_target_lang="ja",
    )
    assert "最近は少し軽いです。" in text
    assert "masterとの距離が少し近いです。" in text
    assert "久しぶりに話せて" in text

    mood = sm.build_mood_awareness_text(
        {
            "dominant_recent_emotion": "HAPPY",
            "emotional_stability": 0.9,
        },
        render_target_lang="ja",
    )
    assert mood == "今日はうれしいのまま静かです。"

    growth = sm.build_growth_note_text(
        {
            "personality_changes": ["少しやわらかくなってきた"],
            "growth_milestones": [],
        },
        render_target_lang="ja",
    )
    assert growth == "最近、自分が少しやわらかくなってきた気がします。"

    aligned = sm.build_state_aligned_self_awareness(
        {
            "bond_growth_perception": "稳定",
        },
        offline_state={"offline_hours": 80.0},
        motive_state={"continuity_drive": 0.8},
        render_policy={"should_be_brief": True},
        render_target_lang="ja",
    )
    assert "数日ぶりに" in aligned["self_awareness"]
    assert "このやり取りの温度を保っていたいです" in aligned["self_awareness"]
    assert "このやり取りを静かにつなぎたいです" in aligned["self_awareness"]
    assert "だから今は短く返したいです。" in aligned["self_awareness"]

