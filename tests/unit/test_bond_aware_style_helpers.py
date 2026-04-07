from __future__ import annotations

import json
import random

import host_brain.persona.bond_aware_style as bas
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.bond_aware_style import (
    BondAwareStyleEngine,
    _build_memory_style_instruction,
    _get_rift_adjustment,
    _get_substage_hint,
)


def test_get_rift_adjustment_handles_default_and_progress_buckets() -> None:
    assert _get_rift_adjustment(False) == ""
    assert _get_rift_adjustment(True, "unknown", -1.0) == bas._RIFT_STYLE_MAP["medium"]["fresh"]
    assert _get_rift_adjustment(True, "light", 0.5) == bas._RIFT_STYLE_MAP["light"]["healing"]
    assert _get_rift_adjustment(True, "severe", 1.5) == bas._RIFT_STYLE_MAP["severe"]["almost_ok"]


def test_get_substage_hint_handles_unknown_zero_span_and_boundaries(monkeypatch) -> None:
    assert _get_substage_hint("MISSING", 0.4) == ""

    monkeypatch.setitem(
        bas._BOND_SUBSTAGE_CONFIG,
        "BROKEN",
        (0.5, 0.5, 0.5, "entry", "stable"),
    )
    assert _get_substage_hint("BROKEN", 0.5) == "stable"

    _, _, _, entry_hint, stable_hint = bas._BOND_SUBSTAGE_CONFIG["FRIEND"]
    assert _get_substage_hint("FRIEND", 0.42) == entry_hint
    assert _get_substage_hint("FRIEND", 0.59) == stable_hint


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

    assert "- substage_context:" in result.prompt_injection
    assert "- mood_adjustment:" in result.prompt_injection
    assert "- natsume_self_mood:" in result.prompt_injection
    assert "severity=severe, progress=80%" in result.prompt_injection
    assert "trend_adjustment: 关系在降温" in result.prompt_injection
    assert "- texture: 语气里带一点黏人" in result.prompt_injection
    assert "- shared_memories: 一起等过末班车; 你说过自己怕黑; 这条应被截断" in result.prompt_injection
    assert "- memory_style_guide:" in result.prompt_injection
    assert result.shared_memory_hint
    assert result.as_dict()["has_shared_memory_hint"] is True


def test_generate_stranger_skips_shared_memory_and_supports_rising_trend() -> None:
    engine = BondAwareStyleEngine()

    result = engine.generate(
        bond_stage="STRANGER",
        bond_trend="rising",
        texture_hint="更克制一点",
        shared_memory_hints=["不该注入"],
    )

    assert "trend_adjustment: 关系在变好" in result.prompt_injection
    assert "- texture: 更克制一点" in result.prompt_injection
    assert "shared_memories" not in result.prompt_injection
    assert result.shared_memory_hint == ""


def test_build_memory_style_instruction_handles_empty_unknown_and_known_stage(monkeypatch) -> None:
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])

    assert _build_memory_style_instruction("FRIEND", []) == ""

    unknown = _build_memory_style_instruction("UNKNOWN", ["a", "b", "c", "d"])
    assert "不少共同记忆" in unknown

    known = _build_memory_style_instruction("ACQUAINTANCE", ["a", "b"])
    assert "几段记忆" in known
    assert bas._MEMORY_STYLE_GUIDES["ACQUAINTANCE"][0] in known


def test_style_templates_override_runtime_sections(monkeypatch) -> None:
    monkeypatch.setattr(random, "choice", lambda seq: seq[0])

    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "bond_aware_style.style_templates",
        content=json.dumps(
            {
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
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    engine = BondAwareStyleEngine()
    result = engine.generate(
        bond_stage="FRIEND",
        intimacy_score=0.41,
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
    assert "trend_adjustment: 自定义关系升温提醒" in result.prompt_injection
    assert _get_rift_adjustment(True, "light", 0.1) == "自定义轻裂痕 fresh"
    assert _build_memory_style_instruction("FRIEND", ["一起看过海"]) == "自定义一条记忆提示自定义朋友记忆指导"
