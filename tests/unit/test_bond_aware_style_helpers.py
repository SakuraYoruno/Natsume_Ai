from __future__ import annotations

import random

import host_brain.persona.bond_aware_style as bas
from host_brain.persona.bond_aware_style import (
    BondAwareStyleEngine,
    _build_memory_style_instruction,
    _get_rift_adjustment,
    _get_substage_hint,
    _load_style_template_data,
)


def test_get_rift_adjustment_handles_default_and_progress_buckets() -> None:
    templates = _load_style_template_data(render_target_lang="zh")
    assert _get_rift_adjustment(False) == ""
    assert _get_rift_adjustment(True, "unknown", -1.0) == templates["rift_style_map"]["medium"]["fresh"]
    assert _get_rift_adjustment(True, "light", 0.5) == templates["rift_style_map"]["light"]["healing"]
    assert _get_rift_adjustment(True, "severe", 1.5) == templates["rift_style_map"]["severe"]["almost_ok"]


def test_get_substage_hint_handles_unknown_zero_span_and_boundaries(monkeypatch) -> None:
    templates = _load_style_template_data(render_target_lang="zh")
    assert _get_substage_hint("MISSING", 0.4) == ""

    monkeypatch.setitem(
        bas._BOND_SUBSTAGE_CONFIG,
        "BROKEN",
        (0.5, 0.5, 0.5, "entry", "stable"),
    )
    assert _get_substage_hint("BROKEN", 0.5) == "stable"

    friend_config = templates["substage_config"]["FRIEND"]
    assert _get_substage_hint("FRIEND", 0.1) == friend_config["entry_hint"]
    assert _get_substage_hint("FRIEND", 0.59) == friend_config["stable_hint"]


def test_generate_includes_rift_trend_texture_and_shared_memories(monkeypatch) -> None:
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    engine = BondAwareStyleEngine()

    result = engine.generate(
        bond_stage="CLOSE_FRIEND",
        intimacy_score=0.61,
        mood_state="negative",
        natsume_mood="SAD",
        rift_active=True,
        rift_severity="severe",
        rift_progress=0.8,
        bond_trend="declining",
        texture_hint="语气里带一点黏人",
        shared_memory_hints=["一起等过末班车", "", "你说过自己怕黑", "这条应被截断"],
    )

    assert "- 关系细节：" in result.prompt_injection
    assert "- 对方状态：" in result.prompt_injection
    assert "- 你此刻的状态：" in result.prompt_injection
    assert "（程度 severe，修复 80%）" in result.prompt_injection
    assert "- 关系走向：关系有点降温" in result.prompt_injection
    assert "- 细节质感：语气里带一点黏人" in result.prompt_injection
    assert "- 共同记忆：一起等过末班车; 你说过自己怕黑; 这条应被截断" in result.prompt_injection
    assert "- 记忆使用方式：" in result.prompt_injection
    assert result.shared_memory_hint
    assert result.as_dict()["has_shared_memory_hint"] is True


def test_generate_friend_includes_shared_memory_and_supports_rising_trend() -> None:
    engine = BondAwareStyleEngine()

    result = engine.generate(
        bond_stage="FRIEND",
        bond_trend="rising",
        texture_hint="自然熟一点",
        shared_memory_hints=["一起等过末班车"],
    )

    assert "- 关系走向：关系在往上走" in result.prompt_injection
    assert "- 细节质感：自然熟一点" in result.prompt_injection
    assert "共同记忆" in result.prompt_injection
    assert result.shared_memory_hint


def test_build_memory_style_instruction_handles_empty_unknown_and_known_stage(monkeypatch) -> None:
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])
    templates = _load_style_template_data(render_target_lang="zh")

    assert _build_memory_style_instruction("FRIEND", []) == ""

    unknown = _build_memory_style_instruction("UNKNOWN", ["a", "b", "c", "d"])
    assert "共同记忆不少" in unknown

    known = _build_memory_style_instruction("FRIEND", ["a", "b"])
    assert "几段共同经历" in known
    assert templates["memory_style_guides"]["FRIEND"][0] in known


def test_style_templates_override_runtime_sections(monkeypatch) -> None:
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])

    monkeypatch.setattr(
        bas,
        "_load_localized_template_mapping",
        lambda *args, **kwargs: {
            "profiles": {
                "FRIEND": {
                    "style_instruction": "自定义朋友风格",
                    "forbidden_patterns": ["别太官腔"],
                },
            },
            "mood_adjustments": {
                "negative": "自定义接住情绪",
            },
            "rift_style_map": {
                "light": {
                    "fresh": "自定义轻裂痕 fresh",
                },
            },
            "natsume_mood_adjustments": {
                "SAD": "自定义夏目低落语气",
            },
            "substage_config": {
                "FRIEND": {
                    "entry_hint": "自定义刚成朋友",
                    "stable_hint": "自定义稳定朋友",
                },
            },
            "trend_adjustments": {
                "rising": "自定义关系升温提醒",
            },
            "memory_style_guides": {
                "FRIEND": ["自定义朋友记忆指导"],
            },
            "memory_count_hints": {
                "1": "自定义一条记忆提示",
            },
        },
    )

    engine = BondAwareStyleEngine()
    result = engine.generate(
        bond_stage="FRIEND",
        intimacy_score=0.1,
        mood_state="negative",
        natsume_mood="SAD",
        rift_active=True,
        rift_severity="light",
        rift_progress=0.1,
        bond_trend="rising",
        shared_memory_hints=["一起看过海"],
    )

    assert result.style_instruction == "自定义朋友风格"
    assert result.mood_adjustment == "自定义接住情绪"
    assert result.rift_adjustment == "自定义轻裂痕 fresh"
    assert result.substage_hint == "自定义刚成朋友"
    assert result.natsume_mood_hint == "自定义夏目低落语气"
    assert result.shared_memory_hint == "自定义一条记忆提示自定义朋友记忆指导"
    assert result.forbidden_patterns == ["别太官腔"]
    assert "自定义关系升温提醒" in result.prompt_injection
    assert _get_rift_adjustment(True, "light", 0.1) == "自定义轻裂痕 fresh"
    assert _build_memory_style_instruction("FRIEND", ["一起看过海"]) == "自定义一条记忆提示自定义朋友记忆指导"


def test_generate_uses_japanese_bond_style_templates() -> None:
    engine = BondAwareStyleEngine()
    result = engine.generate(
        bond_stage="FRIEND",
        intimacy_score=0.45,
        mood_state="negative",
        natsume_mood="SAD",
        bond_trend="rising",
        render_target_lang="ja",
    )

    assert "【関係" in result.prompt_injection
    assert "関係の距離" in result.prompt_injection
    assert "你" not in result.prompt_injection
    assert "我" not in result.prompt_injection
