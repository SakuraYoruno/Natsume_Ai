from __future__ import annotations

from host_brain.app import chat_context_builder as ccb
from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_LONG_ABSENT



def test_day_level_reconnect_uses_light_reconnect_behavior() -> None:
    voice = PersonaProactiveVoice()
    now_ts = 10 * 24 * 3600 * 1000
    result = voice.evaluate_scene(
        user_id="matrix-day-reconnect",
        context={
            "current_ts_ms": now_ts,
            "user_online": False,
            "bond_stage": "FRIEND",
            "offline_state": {
                "offline_duration_ms": 26 * 3600 * 1000,
                "absence_band": "24h_plus",
                "loneliness_level": 0.38,
                "anticipation_level": 0.44,
            },
            "motive_state": {
                "reach_out_drive": 0.58,
                "care_drive": 0.46,
                "withdraw_drive": 0.10,
                "continuity_drive": 0.78,
                "self_protect_drive": 0.06,
                "dominant_motive": "continuity",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_LONG_ABSENT
    assert "reconnect_behavior=light_reconnect" in result.motive_influence
    assert "久别重逢" not in result.content
    assert "终于又见到你了" not in result.content



def test_week_level_deep_bond_uses_deep_reunion_behavior() -> None:
    voice = PersonaProactiveVoice()
    now_ts = 10 * 24 * 3600 * 1000
    result = voice.evaluate_scene(
        user_id="matrix-week-reunion",
        context={
            "current_ts_ms": now_ts,
            "user_online": False,
            "bond_stage": "INTIMATE",
            "offline_state": {
                "offline_duration_ms": 8 * 24 * 3600 * 1000,
                "absence_band": "7d_plus",
                "loneliness_level": 0.42,
                "anticipation_level": 0.48,
            },
            "motive_state": {
                "reach_out_drive": 0.72,
                "care_drive": 0.68,
                "withdraw_drive": 0.08,
                "continuity_drive": 0.84,
                "self_protect_drive": 0.04,
                "dominant_motive": "continuity",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_LONG_ABSENT
    assert "reconnect_behavior=deep_reunion" in result.motive_influence
    assert any(token in result.content for token in ("终于又见到你了", "这段时间", "先确认"))



def test_shallow_bond_week_gap_stays_reserved() -> None:
    voice = PersonaProactiveVoice()
    now_ts = 10 * 24 * 3600 * 1000
    result = voice.evaluate_scene(
        user_id="matrix-shallow-reserved",
        context={
            "current_ts_ms": now_ts,
            "user_online": False,
            "bond_stage": "ACQUAINTANCE",
            "offline_state": {
                "offline_duration_ms": 8 * 24 * 3600 * 1000,
                "absence_band": "7d_plus",
                "loneliness_level": 0.30,
                "anticipation_level": 0.32,
            },
            "motive_state": {
                "reach_out_drive": 0.62,
                "care_drive": 0.38,
                "withdraw_drive": 0.18,
                "continuity_drive": 0.58,
                "self_protect_drive": 0.28,
                "dominant_motive": "reach_out",
            },
        },
    )

    assert result is not None
    assert "reconnect_behavior=light_reconnect" in result.motive_influence
    assert "reconnect_tone=reserved" in result.motive_influence
    assert all(token not in result.content for token in ("想你", "一直惦记", "终于又见到你了"))



def test_offline_hint_marks_reconnect_style() -> None:
    base_hint = {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.0,
        "follow_up_tendency": 0.0,
        "pace_hint": "steady",
    }

    short = ccb._apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 8.0,
            "absence_band": "6h_plus",
            "loneliness_level": 0.22,
            "anticipation_level": 0.24,
        },
    )
    light = ccb._apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 26.0,
            "absence_band": "24h_plus",
            "loneliness_level": 0.28,
            "anticipation_level": 0.32,
        },
    )
    reunion = ccb._apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 80.0,
            "absence_band": "72h_plus",
            "loneliness_level": 0.48,
            "anticipation_level": 0.46,
        },
    )
    deep_reunion = ccb._apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 8 * 24.0,
            "absence_band": "7d_plus",
            "loneliness_level": 0.48,
            "anticipation_level": 0.46,
        },
    )

    assert short["offline_reconnect_style"] == "soft_resume"
    assert light["offline_reconnect_style"] == "light_reconnect"
    assert reunion["offline_reconnect_style"] == "reunion"
    assert deep_reunion["offline_reconnect_style"] == "deep_reunion"
