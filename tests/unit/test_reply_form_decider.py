from __future__ import annotations

from host_brain.app.reply_form_decider import MsgType, ReplyFormDecider, ResponseFormDecider


def test_reply_form_decider_honors_planned_text_plus_sticker() -> None:
    decider = ReplyFormDecider(voice_enabled=False)

    decision = decider.decide(
        user_id="u1",
        user_msg_type=MsgType.TEXT,
        narrative_zh="我是新海天。",
        emotion_tag="PLAYFUL",
        bond_stage="FRIEND",
        planned_primary=MsgType.TEXT,
        planned_secondary=MsgType.IMAGE,
        planned_reason="sticker_social_reply",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary == MsgType.IMAGE
    assert decision.reason == "sticker_social_reply"


def test_response_form_decider_honors_planned_sticker_primary() -> None:
    decider = ResponseFormDecider(voice_enabled=False)

    decision = decider.decide(
        user_id="u1",
        user_msg_type=MsgType.TEXT,
        narrative_zh="猫猫贴纸。",
        emotion_tag="PLAYFUL",
        bond_stage="FRIEND",
        planned_primary=MsgType.IMAGE,
        planned_secondary=None,
        planned_reason="text_plus_sticker",
    )

    assert decision.primary == MsgType.IMAGE
    assert decision.secondary is None
    assert decision.reason == "text_plus_sticker"


def test_response_form_decider_does_not_invent_voice_when_unplanned() -> None:
    decider = ResponseFormDecider(voice_enabled=True, probability_scale=1.0)

    decision = decider.decide(
        user_id="u2",
        user_msg_type=MsgType.TEXT,
        narrative_zh="晚安，早点休息。",
        emotion_tag="LOVING",
        bond_stage="FRIEND",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary is None
    assert decision.reason == "unplanned_default_text"
