from __future__ import annotations

import datetime

from host_brain.persona.proactive_voice import (
    SCENE_LONG_ABSENT,
    SCENE_MORNING_GREET,
    SCENE_TOPIC_FOLLOWUP,
    PersonaProactiveVoice,
)
from host_brain.persona.response_policy import ResponsePolicyInput, decide_response_policy


def _ts_at(hour: int, minute: int = 0) -> int:
    return int(datetime.datetime(2026, 4, 4, hour, minute, 0).timestamp() * 1000)


def _policy(runtime_flags: dict | None = None) -> dict:
    return decide_response_policy(
        ResponsePolicyInput(
            silence_state={"response_willingness": 0.94, "willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH", "drowsiness_level": "AWAKE"},
            bond_summary={"stage": "FRIEND"},
            runtime_flags=runtime_flags,
        )
    ).as_dict()


def _motive_state(**overrides: float | str) -> dict:
    state: dict[str, float | str] = {
        "reach_out_drive": 0.62,
        "care_drive": 0.56,
        "withdraw_drive": 0.12,
        "continuity_drive": 0.78,
        "self_protect_drive": 0.08,
        "dominant_motive": "continuity",
    }
    state.update(overrides)
    return state


def test_joint_matrix_gaming_activity_and_battle_scene_prefers_companion_followup() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at(15, 30)

    result = voice.evaluate_scene(
        user_id="matrix-gaming-battle",
        context={
            "current_ts_ms": ts,
            "sys_mode": "gaming",
            "last_interaction_ts_ms": ts - 5 * 24 * 60 * 60 * 1000,
            "pending_followup_topics": [{"topic_label": "副本"}],
            "activity_monitor": {
                "is_gaming": True,
                "recent_event_type": "gaming_detected",
                "recent_event_age_s": 30.0,
            },
            "scene_awareness": {
                "scene_tags": ["battle"],
                "current_activity": "gaming",
                "companion_suitable": True,
            },
            "motive_state": _motive_state(care_drive=0.52, continuity_drive=0.84),
        },
    )

    assert result is not None
    assert result.scene_type == SCENE_TOPIC_FOLLOWUP
    assert "你先专心打" in result.content
    assert "mode=gaming" in result.motive_influence
    assert "scene=gaming" in result.motive_influence
    assert "activity:gaming_detected" in result.motive_influence
    assert "activity:gaming_companion_followup" in result.motive_influence

    policy = _policy(
        {
            "sys_mode": "gaming",
            "activity_monitor": {
                "is_gaming": True,
                "recent_event_type": "gaming_detected",
                "recent_event_age_s": 30.0,
            },
        }
    )

    assert policy["max_sentences"] == 3
    assert policy["allow_topic_shift"] is False
    assert policy["warmth_floor"] >= 0.14
    assert "mode:gaming" in policy["policy_reason_tags"]
    assert "activity:gaming_detected" in policy["policy_reason_tags"]


def test_joint_matrix_focus_work_scene_blocks_long_absent_even_when_focus_mode_allows_it() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at(15, 30)
    base_context = {
        "current_ts_ms": ts,
        "sys_mode": "focus",
        "last_interaction_ts_ms": ts - 5 * 24 * 60 * 60 * 1000,
        "motive_state": _motive_state(reach_out_drive=0.68, care_drive=0.64),
    }

    baseline = voice.evaluate_scene(user_id="matrix-focus-baseline", context=base_context)
    blocked = voice.evaluate_scene(
        user_id="matrix-focus-scene-block",
        context={
            **base_context,
            "scene_awareness": {
                "scene_tags": ["focus_work"],
                "current_activity": "coding",
                "companion_suitable": True,
            },
        },
    )

    assert baseline is not None
    assert baseline.scene_type == SCENE_LONG_ABSENT
    assert blocked is None

    policy = _policy({"sys_mode": "focus"})

    assert policy["max_sentences"] == 2
    assert policy["allow_topic_shift"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["should_be_brief"] is True
    assert "mode:focus" in policy["policy_reason_tags"]


def test_joint_matrix_sleep_with_recent_wakeup_stays_suppressed_and_reply_minimal() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at(7, 30)
    wakeup_context = {
        "current_ts_ms": ts,
        "pending_followup_topics": [{"topic_label": "项目"}],
        "activity_monitor": {
            "recent_event_type": "wakeup",
            "recent_event_age_s": 20.0,
        },
        "motive_state": _motive_state(reach_out_drive=0.44, care_drive=0.68),
    }

    baseline = voice.evaluate_scene(user_id="matrix-wakeup-baseline", context=wakeup_context)
    suppressed = voice.evaluate_scene(
        user_id="matrix-sleep-wakeup",
        context={
            **wakeup_context,
            "sys_mode": "sleep",
        },
    )

    assert baseline is not None
    assert baseline.scene_type == SCENE_MORNING_GREET
    assert baseline.content == "醒啦？先慢慢清醒一下，我在。"
    assert suppressed is None

    policy = _policy(
        {
            "sys_mode": "sleep",
            "activity_monitor": {
                "recent_event_type": "wakeup",
                "recent_event_age_s": 20.0,
            },
        }
    )

    assert policy["max_chars"] == 80
    assert policy["max_sentences"] == 2
    assert policy["allow_topic_shift"] is False
    assert policy["allow_proactive_after_reply"] is False
    assert policy["should_be_brief"] is True
    assert "mode:sleep" in policy["policy_reason_tags"]
    assert "activity:wakeup" in policy["policy_reason_tags"]


def test_joint_matrix_idle_morning_suppresses_generic_ping_but_keeps_idle_reply_bias() -> None:
    voice = PersonaProactiveVoice()
    ts = _ts_at(8, 0)
    base_context = {
        "current_ts_ms": ts,
        "motive_state": _motive_state(reach_out_drive=0.66, care_drive=0.72),
    }

    baseline = voice.evaluate_scene(user_id="matrix-idle-baseline", context=base_context)
    suppressed = voice.evaluate_scene(
        user_id="matrix-idle-suppress",
        context={
            **base_context,
            "activity_monitor": {"idle_minutes": 30.0},
        },
    )

    assert baseline is not None
    assert baseline.scene_type == SCENE_MORNING_GREET
    assert suppressed is None

    policy = _policy(
        {
            "sys_mode": "normal",
            "activity_monitor": {"idle_minutes": 30.0},
        }
    )

    assert policy["warmth_floor"] >= 0.08
    assert "activity:idle" in policy["policy_reason_tags"]
