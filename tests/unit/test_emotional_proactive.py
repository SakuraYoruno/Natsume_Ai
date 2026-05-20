from __future__ import annotations

import datetime as dt

from host_brain.persona.emotional_proactive import (
    ACTION_DRAFT_ONLY,
    ACTION_QUIET_PRESENCE,
    ACTION_SEND_LIGHT,
    ACTION_SEND_NORMAL,
    build_emotional_proactive_aftereffect,
    build_emotional_proactive_episode,
    classify_proactive_reply,
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


def test_emotional_aftereffect_turns_warm_reply_into_state_update_hint() -> None:
    episode = build_emotional_proactive_episode(
        inner_state={},
        bond_summary={},
        now_ms=_ts_ms(13),
        last_foreground_ts_ms=0,
        last_proactive_outbox_ts_ms=0,
    )

    aftereffect = build_emotional_proactive_aftereffect(
        episode=episode,
        outcome="replied",
        user_reply="在啊，怎么啦？",
        reply_text="我在。",
        now_ms=_ts_ms(13) + 60_000,
        delivery_status="DELIVERED_DRY_RUN",
        thalamus_reason_code="THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION",
    )

    assert aftereffect.episode_id == episode.episode_id
    assert aftereffect.outcome == "replied"
    assert aftereffect.reply_mode == "warm_ack"
    assert aftereffect.reply_warmth >= 0.7
    assert aftereffect.loneliness_delta < 0
    assert aftereffect.memory_candidate["should_remember"] is True
    assert "被接住了" in aftereffect.emotional_resolution


def test_emotional_aftereffect_does_not_treat_transport_failure_as_rejection() -> None:
    aftereffect = build_emotional_proactive_aftereffect(
        episode={"episode_id": "ep_1", "motive": "presence_check"},
        outcome="route_failed",
        user_reply="",
        now_ms=_ts_ms(13),
        delivery_status="DELIVERED_DRY_RUN",
        thalamus_reason_code="",
    )

    assert aftereffect.outcome == "route_failed"
    assert aftereffect.loneliness_delta == 0
    assert aftereffect.state_update_hint["technical_failure_is_not_rejection"] is True
    assert "不解读为关系拒绝" in aftereffect.emotional_resolution


def test_classify_proactive_reply_detects_boundary() -> None:
    reply_mode, warmth = classify_proactive_reply("先别发了，我在忙")

    assert reply_mode == "boundary"
    assert warmth < 0.3
