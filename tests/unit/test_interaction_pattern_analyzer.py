from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.interaction_pattern_analyzer import InteractionPatternAnalyzer
from host_brain.persona.personality_traits import PersonalityTraits, TRAIT_NAMES


def test_interaction_pattern_analyzer_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "interaction_pattern_analyzer.analysis_prompt",
        content="自定义分析提示：{days}天 / {count}条\n{memories_text}\nwarmth={warmth:.2f}",
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: dict[str, str] = {}

    def _fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return json.dumps(
            {
                "pattern_summary": "本周很稳定",
                "dominant_theme": "日常陪伴",
                "trait_suggestions": {name: 0.0 for name in TRAIT_NAMES},
                "suggestion_reason": "保持观察",
                "confidence": 0.8,
            },
            ensure_ascii=False,
        )

    analyzer = InteractionPatternAnalyzer(llm_fn=_fake_llm)
    result = analyzer._analyze_with_llm(
        memories_text="1. 最近聊了很多日常",
        current_traits=PersonalityTraits.from_baseline(),
        lookback_days=3,
        memory_count=1,
        ts_ms=1,
        digest="digest",
    )

    assert "自定义分析提示：3天 / 1条" in str(captured.get("prompt") or "")
    assert "1. 最近聊了很多日常" in str(captured.get("prompt") or "")
    assert "warmth=0.85" in str(captured.get("prompt") or "")
    assert result.pattern_summary == "本周很稳定"


def test_interaction_pattern_analyzer_runtime_texts_override_keyword_and_empty(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "interaction_pattern_analyzer.runtime_texts",
        content=json.dumps(
            {
                "keyword_theme_default": "平稳陪伴",
                "keyword_summary_template": "最近整体偏向：{dominant_theme}",
                "keyword_reason_none": "暂时没看到明显特质波动",
                "empty_pattern_summary": "这周还没有可分析的对话",
                "empty_dominant_theme": "空白",
                "empty_reason": "没有近期记忆，先不动。",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    analyzer = InteractionPatternAnalyzer()
    keyword_result = analyzer._analyze_with_keywords(
        memories_text="普通聊天，没有特别关键词",
        memory_count=3,
        ts_ms=2,
        digest="digest",
    )
    empty_result = analyzer._empty_result(3)

    assert keyword_result.pattern_summary == "最近整体偏向：平稳陪伴"
    assert keyword_result.dominant_theme == "平稳陪伴"
    assert keyword_result.suggestion_reason == "暂时没看到明显特质波动"
    assert empty_result.pattern_summary == "这周还没有可分析的对话"
    assert empty_result.dominant_theme == "空白"
    assert empty_result.suggestion_reason == "没有近期记忆，先不动。"


def test_interaction_pattern_analyzer_runtime_texts_override_llm_defaults(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "interaction_pattern_analyzer.runtime_texts",
        content=json.dumps(
            {
                "llm_pattern_summary_default": "默认总结已改写",
                "llm_dominant_theme_default": "默认主题已改写",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    analyzer = InteractionPatternAnalyzer(min_confidence_threshold=0.0)
    result = analyzer._parse_llm_response(
        raw_response=json.dumps(
            {
                "trait_suggestions": {name: 0.0 for name in TRAIT_NAMES},
                "suggestion_reason": "ok",
                "confidence": 0.7,
            },
            ensure_ascii=False,
        ),
        memory_count=6,
        ts_ms=4,
        digest="digest",
    )

    assert result.pattern_summary == "默认总结已改写"
    assert result.dominant_theme == "默认主题已改写"


def test_interaction_pattern_analyzer_strict_llm_disables_keyword_fallback() -> None:
    analyzer = InteractionPatternAnalyzer(
        llm_fn=lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")),
        strict_llm=True,
    )

    result = analyzer._analyze_with_llm(
        memories_text="1. 最近聊了很多日常",
        current_traits=PersonalityTraits.from_baseline(),
        lookback_days=3,
        memory_count=1,
        ts_ms=1,
        digest="digest",
    )

    assert result.analysis_method == "llm_required"
    assert result.confidence == 0.0
    assert result.trait_suggestions == {}
