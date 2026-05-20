from __future__ import annotations

import datetime as dt

from host_brain.persona.emotional_proactive import (
    ACTION_DRAFT_ONLY,
    ACTION_QUIET_PRESENCE,
    ACTION_SEND_LIGHT,
    ACTION_SEND_NORMAL,
    build_emotional_proactive_episode,
    episode_to_motive_state,
    episode_to_render_policy,
)


def _ts_ms(hour: int = 12) -> int:
    return int(dt.datetime(2026, 5, 20, hour, 0, 0).timestamp() * 1000)


def test_emotional_episode_sends_from_affection_without_external_task() -> None:
    now = _ts_ms(15)
    episode = build_emotional_proactive_episode(
        inner_state={
            "loneliness": 0.82,
            "anticipation": 0.48,
            "total_offline_hours": 7.0,
            "last_interaction_ts_ms": now - 8 * 3_600_000,
            "last_spontaneous_note": "别急着装可爱，先把他在意的地方弄清楚。",
            "inner_stream": [{"note": "别急着装可爱，先把他在意的地方弄清楚。", "salience": 0.82}],
        },
        bond_summary={"stage": "CONFIDANT", "trust": 0.78},
        now_ms=now,
        last_foreground_ts_ms=now - 8 * 3_600_000,
    )

    assert episode.source == "emotional_impulse"
    assert episode.send_allowed is True
    assert episode.chosen_action in {ACTION_SEND_LIGHT, ACTION_SEND_NORMAL}
    assert episode.emotional_drive >= 0.5
    assert "今天过得怎么样" not in episode.opening_message
    assert episode.no_reply_policy == "user_may_ignore_without_penalty"

    motive_state = episode_to_motive_state(episode)
    render_policy = episode_to_render_policy(episode)
    assert motive_state["proactive_episode"]["episode_id"] == episode.episode_id
    assert motive_state["reach_out_drive"] > 0.0
    assert render_policy["allow_proactive_after_reply"] is True


def test_emotional_episode_holds_when_recent_outbound_would_disturb() -> None:
    now = _ts_ms(16)
    episode = build_emotional_proactive_episode(
        inner_state={
            "loneliness": 0.78,
            "anticipation": 0.52,
            "total_offline_hours": 5.0,
            "last_interaction_ts_ms": now - 5 * 3_600_000,
        },
        bond_summary={"stage": "CLOSE_FRIEND"},
        now_ms=now,
        last_foreground_ts_ms=now - 5 * 3_600_000,
        last_proactive_outbox_ts_ms=now - 5 * 60_000,
    )

    assert episode.send_allowed is False
    assert episode.chosen_action in {ACTION_DRAFT_ONLY, ACTION_QUIET_PRESENCE}
    assert episode.disturbance_risk >= 0.56
    assert "disturbance_risk_high" in episode.reason_tags

    render_policy = episode_to_render_policy(episode)
    assert render_policy["allow_proactive_after_reply"] is False
    assert render_policy["should_be_withdrawn"] is True


def test_emotional_episode_can_start_from_first_presence_pull() -> None:
    episode = build_emotional_proactive_episode(
        inner_state={},
        bond_summary={},
        now_ms=_ts_ms(13),
        last_foreground_ts_ms=0,
        last_proactive_outbox_ts_ms=0,
    )

    assert episode.send_allowed is True
    assert episode.chosen_action == ACTION_SEND_LIGHT
    assert episode.motive == "presence_check"
    assert "今天还没有真正和你建立过存在感" in episode.felt_reason
    assert "你忙的话不用回" in episode.opening_message
