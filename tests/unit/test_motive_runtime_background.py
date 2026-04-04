from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from host_brain.app import chat_context_builder as ccb
from host_brain.persona.emotional_silence import EmotionalSilenceEngine
from host_brain.persona.inner_world import InnerWorldState, OfflineInnerWorld
from host_brain.persona.motive_runtime import build_offline_state_from_inner_state
from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_TOPIC_FOLLOWUP


def test_build_offline_state_from_inner_state_uses_elapsed_hours() -> None:
    now_ms = 10 * 3600 * 1000
    offline_state = build_offline_state_from_inner_state(
        {
            "last_interaction_ts_ms": now_ms - 4 * 3600 * 1000,
            "total_offline_hours": 1.5,
            "loneliness": 0.6,
            "anticipation": 0.4,
            "anticipation_topics": ["上次没说完的话题"],
        },
        now_ms=now_ms,
    )

    assert offline_state["offline_hours"] == pytest.approx(4.0, abs=1e-4)
    assert offline_state["offline_duration_ms"] == 4 * 3600 * 1000
    assert offline_state["absence_band"] == "recent"
    assert offline_state["loneliness_level"] == pytest.approx(0.6, abs=1e-6)
    assert offline_state["anticipation_topics"] == ["上次没说完的话题"]


def test_build_offline_state_from_inner_state_classifies_long_absence_bands() -> None:
    now_ms = 10 * 24 * 3600 * 1000
    offline_state = build_offline_state_from_inner_state(
        {
            "last_interaction_ts_ms": now_ms - 80 * 3600 * 1000,
            "total_offline_hours": 12.0,
            "loneliness": 0.4,
            "anticipation": 0.3,
            "anticipation_topics": ["久别的话题"],
        },
        now_ms=now_ms,
    )

    assert offline_state["offline_hours"] == pytest.approx(80.0, abs=1e-4)
    assert offline_state["offline_duration_ms"] == 80 * 3600 * 1000
    assert offline_state["absence_band"] == "72h_plus"


def test_emotional_silence_tick_uses_motive_state_to_adjust_recovery(monkeypatch) -> None:
    import host_brain.persona.emotional_silence as emotional_silence_module

    monkeypatch.setattr(emotional_silence_module, "_now_ms", lambda: 60_000)

    fast_engine = EmotionalSilenceEngine()
    fast_engine._response_willingness = 0.4
    fast_engine._last_tick_ms = 0
    fast_engine._topic_resistance_map = {"工作": 0.6}
    fast_engine._current_valence = -0.1
    fast_engine._current_arousal = 0.4

    slow_engine = EmotionalSilenceEngine()
    slow_engine._response_willingness = 0.4
    slow_engine._last_tick_ms = 0
    slow_engine._topic_resistance_map = {"工作": 0.6}
    slow_engine._current_valence = -0.1
    slow_engine._current_arousal = 0.4

    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: {
            "reach_out_drive": 0.82,
            "continuity_drive": 0.66,
            "withdraw_drive": 0.10,
            "self_protect_drive": 0.08,
            "dominant_motive": "reach_out",
        },
    )
    fast_result = fast_engine.tick(user_id="user_1")

    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: {
            "reach_out_drive": 0.10,
            "continuity_drive": 0.12,
            "withdraw_drive": 0.84,
            "self_protect_drive": 0.80,
            "dominant_motive": "withdraw",
        },
    )
    slow_result = slow_engine.tick(user_id="user_1")

    assert fast_result["motive_state"]["dominant_motive"] == "reach_out"
    assert slow_result["motive_state"]["dominant_motive"] == "withdraw"
    assert fast_result["willingness"] > slow_result["willingness"]
    assert fast_result["motive_recovery_factor"] > slow_result["motive_recovery_factor"]
    assert fast_result["resistance_decay_factor"] > slow_result["resistance_decay_factor"]
    assert fast_engine.topic_resistance_map["工作"] < slow_engine.topic_resistance_map["工作"]


def test_inner_world_tick_uses_motive_state_to_adjust_offline_growth(monkeypatch, tmp_path: Path) -> None:
    import host_brain.persona.inner_world as inner_world_module

    now_ms = 28_800_000
    monkeypatch.setattr(inner_world_module, "_now_ms", lambda: now_ms)

    def _seed_world(base_dir: Path) -> OfflineInnerWorld:
        world = OfflineInnerWorld(state_dir=str(base_dir))
        world._save_state(
            InnerWorldState(
                user_id="user_1",
                last_interaction_ts_ms=now_ms - 6 * 3_600_000,
                last_tick_ts_ms=now_ms - 3_600_000,
                loneliness=0.2,
                anticipation=0.2,
                anticipation_topics=["上次的话题"],
                drifted_valence=0.1,
                drifted_arousal=0.5,
                mood_baseline=0.0,
                total_offline_hours=6.0,
            )
        )
        return world

    fast_world = _seed_world(tmp_path / "fast")
    slow_world = _seed_world(tmp_path / "slow")

    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: {
            "reach_out_drive": 0.78,
            "continuity_drive": 0.72,
            "withdraw_drive": 0.10,
            "self_protect_drive": 0.08,
            "dominant_motive": "continuity",
        },
    )
    fast_result = fast_world.tick("user_1")

    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: {
            "reach_out_drive": 0.15,
            "continuity_drive": 0.10,
            "withdraw_drive": 0.82,
            "self_protect_drive": 0.86,
            "dominant_motive": "self_protect",
        },
    )
    slow_result = slow_world.tick("user_1")

    assert fast_result["motive_state"]["dominant_motive"] == "continuity"
    assert slow_result["motive_state"]["dominant_motive"] == "self_protect"
    assert fast_result["loneliness"] > slow_result["loneliness"]
    assert fast_result["anticipation"] > slow_result["anticipation"]
    assert fast_result["anticipation_rate_multiplier"] > slow_result["anticipation_rate_multiplier"]


def test_motive_behavior_matrix_changes_proactive_silence_and_expression_paths(monkeypatch) -> None:
    import host_brain.persona.emotional_silence as emotional_silence_module

    supportive_motive = {
        "care_drive": 0.84,
        "reach_out_drive": 0.72,
        "continuity_drive": 0.78,
        "withdraw_drive": 0.08,
        "self_protect_drive": 0.06,
        "dominant_motive": "care",
    }
    retreat_motive = {
        "care_drive": 0.18,
        "reach_out_drive": 0.14,
        "continuity_drive": 0.16,
        "withdraw_drive": 0.86,
        "self_protect_drive": 0.89,
        "dominant_motive": "self_protect",
    }

    now = datetime.datetime.now()
    topic_ts = int(now.replace(hour=15, minute=0, second=0, microsecond=0).timestamp() * 1000)

    voice = PersonaProactiveVoice()
    base_topic_context = {
        "current_ts_ms": topic_ts,
        "user_online": False,
        "last_interaction_ts_ms": topic_ts - 1000,
        "last_emotion_intensity": 0.3,
        "pending_followup_topics": [
            {"topic_label": "project", "topic_keywords": ["project"]},
        ],
    }
    supportive_expr = voice.evaluate_scene(
        user_id="matrix-supportive",
        context={**base_topic_context, "motive_state": supportive_motive},
    )
    retreat_expr = voice.evaluate_scene(
        user_id="matrix-retreat",
        context={**base_topic_context, "motive_state": retreat_motive},
    )

    assert supportive_expr is not None
    assert supportive_expr.scene_type == SCENE_TOPIC_FOLLOWUP
    assert retreat_expr is None

    base_hint = {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.0,
        "follow_up_tendency": 0.0,
        "pace_hint": "steady",
    }
    supportive_hint = ccb._apply_motive_state_to_persona_expression_hint(base_hint, supportive_motive)
    retreat_hint = ccb._apply_motive_state_to_persona_expression_hint(base_hint, retreat_motive)

    assert supportive_hint["tone"] == "caring"
    assert supportive_hint["follow_up_tendency"] > retreat_hint["follow_up_tendency"]
    assert supportive_hint["echo_weight_modifier"] > retreat_hint["echo_weight_modifier"]
    assert retreat_hint["tone"] == "gentle"

    monkeypatch.setattr(emotional_silence_module, "_now_ms", lambda: 60_000)

    supportive_engine = EmotionalSilenceEngine()
    supportive_engine._response_willingness = 0.4
    supportive_engine._last_tick_ms = 0
    supportive_engine._topic_resistance_map = {"工作": 0.6}
    supportive_engine._current_valence = -0.1
    supportive_engine._current_arousal = 0.4

    retreat_engine = EmotionalSilenceEngine()
    retreat_engine._response_willingness = 0.4
    retreat_engine._last_tick_ms = 0
    retreat_engine._topic_resistance_map = {"工作": 0.6}
    retreat_engine._current_valence = -0.1
    retreat_engine._current_arousal = 0.4

    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: supportive_motive,
    )
    supportive_tick = supportive_engine.tick(user_id="matrix-user")

    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: retreat_motive,
    )
    retreat_tick = retreat_engine.tick(user_id="matrix-user")

    assert supportive_tick["willingness"] > retreat_tick["willingness"]
    assert supportive_tick["motive_recovery_factor"] > retreat_tick["motive_recovery_factor"]
    assert supportive_tick["resistance_decay_factor"] > retreat_tick["resistance_decay_factor"]
