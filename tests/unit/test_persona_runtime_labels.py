from __future__ import annotations

import host_brain.persona.self_model as self_model_mod
import host_brain.persona.self_reflection as self_reflection_mod
from host_brain.persona.runtime_labels import (
    BOND_GROWTH_CLOSER,
    BOND_GROWTH_DISTANT,
    BOND_GROWTH_LABELS,
    BOND_GROWTH_STABLE,
    MOOD_TREND_LABELS,
    MOOD_TREND_NEGATIVE,
    MOOD_TREND_POSITIVE,
    MOOD_TREND_STABLE,
    TIME_CONTEXT_FREQUENT_INTERACTION,
    TIME_CONTEXT_LABELS,
    TIME_CONTEXT_REUNION,
    TIME_CONTEXT_SHORT_ABSENCE,
    TIME_CONTEXT_SPARSE_INTERACTION,
)


def test_persona_runtime_labels_are_clean() -> None:
    assert MOOD_TREND_LABELS["positive"] == "偏好"
    assert MOOD_TREND_LABELS["negative"] == "偏低"
    assert MOOD_TREND_LABELS["stable"] == "平稳"
    assert BOND_GROWTH_LABELS["closer"] == "变亲近"
    assert BOND_GROWTH_LABELS["distant"] == "有些疏远"
    assert TIME_CONTEXT_LABELS["reunion"] == "久别重逢"
    assert TIME_CONTEXT_LABELS["sparse_interaction"] == "稀少互动"


def test_self_model_and_self_reflection_use_runtime_labels() -> None:
    model = self_model_mod.SelfModel()
    reflection = self_reflection_mod.SelfReflection(seed=0)
    one_day_ms = 24 * 3600 * 1000

    assert model._analyze_mood_trend({"trend": "improving"}, {"avg_valence": 0.0}) == MOOD_TREND_POSITIVE
    assert model._analyze_mood_trend({"trend": "declining"}, {"avg_valence": 0.0}) == MOOD_TREND_NEGATIVE
    assert model._analyze_mood_trend({"trend": "stable"}, {"avg_valence": 0.0}) == MOOD_TREND_STABLE

    assert model._analyze_bond_growth(None, {"trend": "上升"}) == BOND_GROWTH_CLOSER
    assert model._analyze_bond_growth(None, {"trend": "下降"}) == BOND_GROWTH_DISTANT
    assert model._analyze_bond_growth(None, {"trend": "稳定"}) == BOND_GROWTH_STABLE

    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=1_000,
        current_ts_ms=1_000 + 8 * one_day_ms,
        weekly_talk_count=0,
    ) == TIME_CONTEXT_REUNION
    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=1_000,
        current_ts_ms=1_000 + 4 * one_day_ms,
        weekly_talk_count=0,
    ) == TIME_CONTEXT_SHORT_ABSENCE
    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=0,
        current_ts_ms=0,
        weekly_talk_count=6,
    ) == TIME_CONTEXT_FREQUENT_INTERACTION
    assert reflection._analyze_time_context(
        last_deep_talk_ts_ms=0,
        current_ts_ms=0,
        weekly_talk_count=1,
    ) == TIME_CONTEXT_SPARSE_INTERACTION
