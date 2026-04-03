from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.persona.emotional_silence import EmotionalSilenceEngine
from host_brain.persona.inner_world import InnerWorldState, OfflineInnerWorld
from host_brain.persona.motive_runtime import build_offline_state_from_inner_state


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
    assert offline_state["loneliness_level"] == pytest.approx(0.6, abs=1e-6)
    assert offline_state["anticipation_topics"] == ["上次没说完的话题"]


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
