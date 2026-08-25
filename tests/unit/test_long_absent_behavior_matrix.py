from __future__ import annotations

from host_brain.persona.proactive_voice import PersonaProactiveVoice, SCENE_LONG_ABSENT
from host_brain.contracts.runtime_context import SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2



def test_day_level_reconnect_uses_light_reconnect_behavior() -> None:
    voice = PersonaProactiveVoice()
    now_ts = 10 * 24 * 3600 * 1000
    scene = SceneDecisionV2(
        workspace_id="workspace_behavior_matrix",
        subject_id="matrix-day-reconnect",
        scene_id="scene.casual-chat",
        revision=1,
        world_snapshot_digest="a" * 64,
        context_digest="b" * 64,
        guardian_snapshot_digest="c" * 64,
        constraints=(),
    )
    result = voice.evaluate_scene(
        user_id="matrix-day-reconnect",
        context={
            "current_ts_ms": now_ts,
            "user_online": False,
            "bond_stage": "FRIEND",
            "scene_decision_v2": scene.to_dict(),
            "scene_identity_v2": SceneIdentityV2.from_decision(scene).to_dict(),
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
