from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.app import chat_context_builder as ccb
from host_brain.persona.inner_world import (
    InnerWorldState,
    OfflineInnerWorld,
    _build_awakening_hint,
)
from host_brain.persona.proactive_voice import (
    PersonaProactiveVoice,
    SCENE_LONG_ABSENT,
    _get_absent_level,
)
from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy



def test_inner_world_awaken_snapshot_includes_absence_band(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import host_brain.persona.inner_world as inner_world_module

    now_ms = 10 * 24 * 3600 * 1000
    monkeypatch.setattr(inner_world_module, "_now_ms", lambda: now_ms)

    world = OfflineInnerWorld(state_dir=str(tmp_path))
    world._save_state(
        InnerWorldState(
            user_id="user_1",
            last_interaction_ts_ms=now_ms - 8 * 24 * 3600 * 1000,
            loneliness=0.74,
            anticipation=0.66,
            anticipation_topics=["之前说的旅行计划"],
            drifted_valence=-0.15,
            drifted_arousal=0.25,
        )
    )

    snapshot = world.on_user_awaken("user_1")

    assert snapshot.absence_band == "7d_plus"
    assert snapshot.as_dict()["absence_band"] == "7d_plus"
    assert snapshot.should_express_waiting is True
    assert "旅行计划" in snapshot.awakening_hint
    assert any(token in snapshot.awakening_hint for token in ("想你", "惦记"))



def test_build_awakening_hint_strengthens_with_absence_band() -> None:
    short_hint = _build_awakening_hint(
        offline_hours=8.0,
        absence_band="6h_plus",
        loneliness=0.35,
        anticipation=0.0,
        topics=[],
    )
    long_hint = _build_awakening_hint(
        offline_hours=8 * 24.0,
        absence_band="7d_plus",
        loneliness=0.35,
        anticipation=0.0,
        topics=[],
    )

    assert "离开了好一阵" in short_hint
    assert "很久很久" in long_hint
    assert long_hint != short_hint



def _policy_for_band(absence_band: str, offline_hours: float) -> dict:
    return decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.92, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            offline_state={
                "offline_hours": offline_hours,
                "absence_band": absence_band,
                "loneliness_level": 0.24,
            },
            bond_summary={"stage": "FRIEND"},
        )
    ).as_dict()



def test_response_policy_warmth_floor_progresses_by_absence_band() -> None:
    warmth_floors = [
        _policy_for_band("6h_plus", 6.5)["warmth_floor"],
        _policy_for_band("24h_plus", 26.0)["warmth_floor"],
        _policy_for_band("72h_plus", 80.0)["warmth_floor"],
        _policy_for_band("7d_plus", 8 * 24.0)["warmth_floor"],
    ]

    assert warmth_floors == sorted(warmth_floors)
    assert warmth_floors == pytest.approx([0.08, 0.15, 0.18, 0.22], abs=1e-6)



def test_offline_state_modulates_persona_expression_hint_for_reunion() -> None:
    hint = {
        "style_hint": "自然回应",
        "tone": "gentle",
        "echo_weight_modifier": 0.0,
        "follow_up_tendency": 0.0,
        "pace_hint": "steady",
    }

    adjusted = ccb._apply_offline_state_to_persona_expression_hint(
        hint,
        {
            "offline_hours": 80.0,
            "absence_band": "72h_plus",
            "loneliness_level": 0.58,
            "anticipation_level": 0.46,
        },
    )

    assert adjusted["tone"] == "caring"
    assert adjusted["follow_up_tendency"] >= 0.12
    assert adjusted["echo_weight_modifier"] >= 0.1
    assert adjusted["pace_hint"] in {"steady", "slow"}
    assert "久别重逢" in str(adjusted.get("style_hint") or "")
    assert "offline_guidance" in adjusted



def test_get_absent_level_maps_24h_72h_7d_bands() -> None:
    assert _get_absent_level(26 * 3600 * 1000) == "short"
    assert _get_absent_level(4 * 24 * 3600 * 1000) == "medium"
    assert _get_absent_level(8 * 24 * 3600 * 1000) == "long"



def test_proactive_voice_long_absent_carries_absence_band_signal() -> None:
    voice = PersonaProactiveVoice()
    now_ts = 10 * 24 * 3600 * 1000

    result = voice.evaluate_scene(
        user_id="offline-band-user",
        context={
            "current_ts_ms": now_ts,
            "user_online": False,
            "bond_stage": "FRIEND",
            "offline_state": {
                "offline_duration_ms": 8 * 24 * 3600 * 1000,
                "absence_band": "7d_plus",
                "loneliness_level": 0.42,
                "anticipation_level": 0.46,
            },
            "motive_state": {
                "reach_out_drive": 0.58,
                "care_drive": 0.54,
                "continuity_drive": 0.78,
                "withdraw_drive": 0.08,
                "self_protect_drive": 0.05,
                "dominant_motive": "continuity",
            },
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_LONG_ABSENT
    assert "absence_band=7d_plus" in result.motive_influence
