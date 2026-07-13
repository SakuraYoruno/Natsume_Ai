from __future__ import annotations

from host_brain.persona.interaction_pattern_analyzer import InteractionPatternAnalyzer
from host_brain.persona.personality_traits import PersonalityTraits, TRAIT_NAMES


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
