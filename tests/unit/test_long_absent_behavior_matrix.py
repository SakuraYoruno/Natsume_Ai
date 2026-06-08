from __future__ import annotations

from host_brain.app.context.persona_expression import _apply_offline_state_to_persona_expression_hint
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



def test_offline_hint_marks_reconnect_style() -> None:
    base_hint = {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.0,
        "follow_up_tendency": 0.0,
        "pace_hint": "steady",
    }

    short = _apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 8.0,
            "absence_band": "6h_plus",
            "loneliness_level": 0.22,
            "anticipation_level": 0.24,
        },
    )
    light = _apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 26.0,
            "absence_band": "24h_plus",
            "loneliness_level": 0.28,
            "anticipation_level": 0.32,
        },
    )
    reunion = _apply_offline_state_to_persona_expression_hint(
        base_hint,
        {
            "offline_hours": 80.0,
            "absence_band": "72h_plus",
            "loneliness_level": 0.48,
            "anticipation_level": 0.46,
        },
    )
    deep_reunion = _apply_offline_state_to_persona_expression_hint(
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
