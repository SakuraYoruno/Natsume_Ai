from __future__ import annotations

from types import SimpleNamespace

import pytest

import host_brain.persona.emotional_silence as emotional_silence_module
from host_brain.app import chat_context_builder as ccb
from host_brain.persona.emotional_silence import EmotionalSilenceEngine
from host_brain.persona.motive_engine import MotiveInput, compute_motive_state
from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_LONG_ABSENT
from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy


ABSENCE_CASES: list[tuple[str, float]] = [
    ("recent", 2.0),
    ("6h_plus", 8.0),
    ("24h_plus", 26.0),
    ("72h_plus", 80.0),
    ("7d_plus", 8 * 24.0),
]


def _offline_state_for_band(
    absence_band: str,
    offline_hours: float,
    *,
    loneliness: float = 0.22,
    anticipation: float = 0.28,
) -> dict:
    return {
        "offline_hours": offline_hours,
        "offline_duration_ms": int(offline_hours * 3600 * 1000),
        "absence_band": absence_band,
        "loneliness_level": loneliness,
        "anticipation_level": anticipation,
        "anticipation_topics": ["project"],
    }



def _policy_for_band(absence_band: str, offline_hours: float) -> dict:
    return decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.92, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            offline_state=_offline_state_for_band(absence_band, offline_hours),
            bond_summary={"stage": "FRIEND"},
        )
    ).as_dict()



def _motive_for_band(
    absence_band: str,
    offline_hours: float,
    *,
    bond_stage: str = "FRIEND",
    shared_memory_count: int = 2,
    loneliness: float = 0.22,
    anticipation: float = 0.28,
    response_willingness: float = 0.92,
) -> dict:
    return compute_motive_state(
        MotiveInput(
            silence_state={"response_willingness": response_willingness, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            offline_state=_offline_state_for_band(
                absence_band,
                offline_hours,
                loneliness=loneliness,
                anticipation=anticipation,
            ),
            bond_summary={"stage": bond_stage, "shared_memory_count": shared_memory_count},
            emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.3},
            pending_followup_topics=["project"],
            shared_memory_count=shared_memory_count,
        )
    ).as_dict()



def _silence_tick_for_band(monkeypatch: pytest.MonkeyPatch, absence_band: str, offline_hours: float) -> dict:
    monkeypatch.setattr(emotional_silence_module, "_now_ms", lambda: 60_000)
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: {
            "reach_out_drive": 0.82,
            "continuity_drive": 0.68,
            "withdraw_drive": 0.10,
            "self_protect_drive": 0.08,
            "dominant_motive": "reach_out",
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_world.get_inner_world",
        lambda: SimpleNamespace(get_state=lambda _user_id: {}),
    )
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda _inner_state, now_ms=None: {
            "absence_band": absence_band,
            "offline_hours": offline_hours,
        },
    )

    engine = EmotionalSilenceEngine()
    engine._response_willingness = 0.4
    engine._last_tick_ms = 0
    engine._topic_resistance_map = {"工作": 0.6}
    engine._current_valence = -0.1
    engine._current_arousal = 0.4
    return engine.tick(user_id="matrix-user")



def _evaluate_long_absent_scene(
    *,
    user_id: str,
    absence_band: str,
    offline_hours: float,
    bond_stage: str,
    motive_state: dict,
    loneliness: float,
    anticipation: float,
) -> object:
    return PersonaProactiveVoice().evaluate_scene(
        user_id=user_id,
        context={
            "current_ts_ms": 10 * 24 * 3600 * 1000,
            "user_online": False,
            "sys_mode": "focus",
            "bond_stage": bond_stage,
            "offline_state": _offline_state_for_band(
                absence_band,
                offline_hours,
                loneliness=loneliness,
                anticipation=anticipation,
            ),
            "motive_state": motive_state,
        },
    )



def _base_hint() -> dict:
    return {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.0,
        "follow_up_tendency": 0.0,
        "pace_hint": "steady",
    }



def test_offline_continuity_regression_matrix_progresses_policy_motive_and_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = [_policy_for_band(absence_band, offline_hours) for absence_band, offline_hours in ABSENCE_CASES]
    motives = [_motive_for_band(absence_band, offline_hours) for absence_band, offline_hours in ABSENCE_CASES]
    ticks = [
        _silence_tick_for_band(monkeypatch, absence_band, offline_hours)
        for absence_band, offline_hours in ABSENCE_CASES
    ]

    warmth_floors = [policy["warmth_floor"] for policy in policies]
    assert warmth_floors == sorted(warmth_floors)
    assert warmth_floors == pytest.approx([0.0, 0.08, 0.15, 0.18, 0.22], abs=1e-6)

    reach_out_drives = [motive["reach_out_drive"] for motive in motives]
    continuity_drives = [motive["continuity_drive"] for motive in motives]
    care_drives = [motive["care_drive"] for motive in motives]
    assert reach_out_drives == sorted(reach_out_drives)
    assert continuity_drives == sorted(continuity_drives)
    assert care_drives == sorted(care_drives)
    assert not any(tag.startswith("reach_out:absence_band=") for tag in motives[0]["motive_reason_tags"])
    for (absence_band, _offline_hours), motive in zip(ABSENCE_CASES[1:], motives[1:]):
        assert f"reach_out:absence_band={absence_band}" in motive["motive_reason_tags"]
        assert f"continuity:absence_band={absence_band}" in motive["motive_reason_tags"]

    absence_recovery_factors = [tick["absence_recovery_factor"] for tick in ticks]
    motive_recovery_factors = [tick["motive_recovery_factor"] for tick in ticks]
    resistance_decay_factors = [tick["resistance_decay_factor"] for tick in ticks]
    willingness_values = [tick["willingness"] for tick in ticks]
    assert absence_recovery_factors == pytest.approx([1.0, 1.03, 1.08, 1.14, 1.20], abs=1e-6)
    assert motive_recovery_factors == sorted(motive_recovery_factors)
    assert resistance_decay_factors == sorted(resistance_decay_factors)
    assert willingness_values == sorted(willingness_values)



def test_long_absent_regression_matrix_routes_reconnect_behaviors() -> None:
    recent = _evaluate_long_absent_scene(
        user_id="matrix-recent",
        absence_band="recent",
        offline_hours=2.0,
        bond_stage="FRIEND",
        motive_state=_motive_for_band("recent", 2.0),
        loneliness=0.22,
        anticipation=0.28,
    )
    short_gap = _evaluate_long_absent_scene(
        user_id="matrix-6h",
        absence_band="6h_plus",
        offline_hours=8.0,
        bond_stage="FRIEND",
        motive_state=_motive_for_band("6h_plus", 8.0),
        loneliness=0.22,
        anticipation=0.28,
    )
    day_gap = _evaluate_long_absent_scene(
        user_id="matrix-24h",
        absence_band="24h_plus",
        offline_hours=26.0,
        bond_stage="INTIMATE",
        motive_state=_motive_for_band(
            "24h_plus",
            26.0,
            bond_stage="INTIMATE",
            shared_memory_count=6,
            loneliness=0.46,
            anticipation=0.55,
            response_willingness=0.96,
        ),
        loneliness=0.46,
        anticipation=0.55,
    )
    three_day_gap = _evaluate_long_absent_scene(
        user_id="matrix-72h",
        absence_band="72h_plus",
        offline_hours=80.0,
        bond_stage="INTIMATE",
        motive_state=_motive_for_band(
            "72h_plus",
            80.0,
            bond_stage="INTIMATE",
            shared_memory_count=6,
            loneliness=0.46,
            anticipation=0.55,
            response_willingness=0.96,
        ),
        loneliness=0.46,
        anticipation=0.55,
    )
    week_gap = _evaluate_long_absent_scene(
        user_id="matrix-7d",
        absence_band="7d_plus",
        offline_hours=8 * 24.0,
        bond_stage="INTIMATE",
        motive_state=_motive_for_band(
            "7d_plus",
            8 * 24.0,
            bond_stage="INTIMATE",
            shared_memory_count=6,
            loneliness=0.46,
            anticipation=0.55,
            response_willingness=0.96,
        ),
        loneliness=0.46,
        anticipation=0.55,
    )
    reserved_week_gap = _evaluate_long_absent_scene(
        user_id="matrix-7d-reserved",
        absence_band="7d_plus",
        offline_hours=8 * 24.0,
        bond_stage="ACQUAINTANCE",
        motive_state={
            "reach_out_drive": 0.62,
            "care_drive": 0.38,
            "withdraw_drive": 0.18,
            "continuity_drive": 0.58,
            "self_protect_drive": 0.62,
            "dominant_motive": "reach_out",
        },
        loneliness=0.30,
        anticipation=0.32,
    )

    assert recent is None
    assert short_gap is None

    assert day_gap is not None
    assert day_gap.scene_type == SCENE_LONG_ABSENT
    assert "reconnect_behavior=light_reconnect" in day_gap.motive_influence

    assert three_day_gap is not None
    assert three_day_gap.scene_type == SCENE_LONG_ABSENT
    assert "reconnect_behavior=reunion" in three_day_gap.motive_influence

    assert week_gap is not None
    assert week_gap.scene_type == SCENE_LONG_ABSENT
    assert "reconnect_behavior=reunion" in week_gap.motive_influence

    assert reserved_week_gap is not None
    assert reserved_week_gap.scene_type == SCENE_LONG_ABSENT
    assert "reconnect_behavior=light_reconnect" in reserved_week_gap.motive_influence
    assert "reconnect_tone=reserved" in reserved_week_gap.motive_influence



def test_offline_reconnect_hint_matrix_matches_scene_semantics() -> None:
    recent = ccb._apply_offline_state_to_persona_expression_hint(
        _base_hint(),
        _offline_state_for_band("recent", 2.0, loneliness=0.30, anticipation=0.20),
    )
    short_gap = ccb._apply_offline_state_to_persona_expression_hint(
        _base_hint(),
        _offline_state_for_band("6h_plus", 8.0, loneliness=0.30, anticipation=0.20),
    )
    day_gap = ccb._apply_offline_state_to_persona_expression_hint(
        _base_hint(),
        _offline_state_for_band("24h_plus", 26.0, loneliness=0.46, anticipation=0.55),
    )
    three_day_gap = ccb._apply_offline_state_to_persona_expression_hint(
        _base_hint(),
        _offline_state_for_band("72h_plus", 80.0, loneliness=0.46, anticipation=0.55),
    )
    week_gap = ccb._apply_offline_state_to_persona_expression_hint(
        _base_hint(),
        _offline_state_for_band("7d_plus", 8 * 24.0, loneliness=0.46, anticipation=0.55),
    )

    assert "offline_reconnect_style" not in recent
    assert "offline_reconnect_style" not in short_gap
    assert day_gap["offline_reconnect_style"] == "light_reconnect"
    assert three_day_gap["offline_reconnect_style"] == "reunion"
    assert week_gap["offline_reconnect_style"] == "reunion"
    assert "轻度失联后的重新连上" in day_gap["offline_guidance"]
    assert "久别重逢后的接住与确认" in three_day_gap["offline_guidance"]
    assert "久别后的重逢" in week_gap["offline_guidance"]
