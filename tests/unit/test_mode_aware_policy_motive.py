from __future__ import annotations

from host_brain.persona.motive_engine import MotiveInput, compute_motive_state
from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy


def _base_motive_input(*, runtime_flags: dict | None = None) -> MotiveInput:
    return MotiveInput(
        silence_state={"response_willingness": 0.92, "willingness_level": "NORMAL"},
        body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
        offline_state={
            "offline_hours": 18.0,
            "loneliness_level": 0.42,
            "anticipation_level": 0.38,
            "anticipation_topics": [{"topic_label": "project"}],
        },
        bond_summary={"stage": "FRIEND", "shared_memory_count": 3},
        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.35},
        pending_followup_topics=[{"topic_label": "project"}],
        shared_memory_count=3,
        runtime_flags=runtime_flags,
    )


def test_response_policy_focus_mode_restrains_reply() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.92, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            runtime_flags={"sys_mode": "focus"},
        )
    ).as_dict()

    assert policy["max_sentences"] == 2
    assert policy["allow_topic_shift"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["should_be_brief"] is True
    assert "mode:focus" in policy["policy_reason_tags"]


def test_response_policy_sleep_mode_blocks_followup() -> None:
    policy = decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.88, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            runtime_flags={"sys_mode": "sleep"},
        )
    ).as_dict()

    assert policy["max_chars"] == 80
    assert policy["max_sentences"] == 2
    assert policy["allow_followup"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["withdrawal_guard"] is True
    assert "mode:sleep" in policy["policy_reason_tags"]


def test_motive_engine_gaming_mode_promotes_companion_drives() -> None:
    baseline = compute_motive_state(_base_motive_input()).as_dict()
    gaming = compute_motive_state(_base_motive_input(runtime_flags={"sys_mode": "gaming"})).as_dict()

    assert gaming["reach_out_drive"] > baseline["reach_out_drive"]
    assert gaming["continuity_drive"] > baseline["continuity_drive"]
    assert gaming["care_drive"] >= baseline["care_drive"]
    assert "mode:gaming" in gaming["motive_reason_tags"]


def test_motive_engine_focus_mode_raises_retreat_drives() -> None:
    baseline = compute_motive_state(_base_motive_input()).as_dict()
    focus = compute_motive_state(_base_motive_input(runtime_flags={"sys_mode": "focus"})).as_dict()

    assert focus["reach_out_drive"] < baseline["reach_out_drive"]
    assert focus["continuity_drive"] < baseline["continuity_drive"]
    assert focus["withdraw_drive"] > baseline["withdraw_drive"]
    assert focus["self_protect_drive"] > baseline["self_protect_drive"]
    assert "mode:focus" in focus["motive_reason_tags"]


def test_motive_engine_idle_activity_gently_increases_reconnect() -> None:
    baseline = compute_motive_state(_base_motive_input()).as_dict()
    idle = compute_motive_state(
        _base_motive_input(runtime_flags={"activity_monitor": {"idle_minutes": 28.0}})
    ).as_dict()

    assert idle["reach_out_drive"] > baseline["reach_out_drive"]
    assert idle["continuity_drive"] > baseline["continuity_drive"]
    assert "activity:idle" in idle["motive_reason_tags"]
