from __future__ import annotations

from host_brain.app.reply_form_decider import MsgType, ResponseFormDecider


def test_reply_form_decider_honors_planned_text_plus_sticker() -> None:
    decider = ResponseFormDecider(voice_enabled=False)

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


def test_response_form_decider_adds_voice_for_warm_short_reply(monkeypatch) -> None:
    monkeypatch.setattr(ResponseFormDecider, "_is_quiet_hours", staticmethod(lambda _now_ts: False))
    decider = ResponseFormDecider(voice_enabled=True, probability_scale=1.0)

    decision = decider.decide(
        user_id="u2",
        user_msg_type=MsgType.TEXT,
        narrative_zh="晚安，早点休息。",
        emotion_tag="LOVING",
        bond_stage="FRIEND",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary == MsgType.VOICE
    assert decision.reason == "warmth_voice_supplement"


def test_response_form_decider_voices_short_interjection_as_presence(monkeypatch) -> None:
    monkeypatch.setattr(ResponseFormDecider, "_is_quiet_hours", staticmethod(lambda _now_ts: False))
    decider = ResponseFormDecider(voice_enabled=True, probability_scale=1.0)

    decision = decider.decide(
        user_id="u3",
        user_msg_type=MsgType.TEXT,
        narrative_zh="哈？",
        emotion_tag="SURPRISED",
        bond_stage="CONFIDANT",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary == MsgType.VOICE
    assert decision.reason == "embodied_presence_voice"


def test_response_form_decider_keeps_structured_reply_text_only() -> None:
    decider = ResponseFormDecider(voice_enabled=True, probability_scale=1.0)

    decision = decider.decide(
        user_id="u3b",
        user_msg_type=MsgType.TEXT,
        narrative_zh="```python\nprint('hi')\n```",
        emotion_tag="PLAYFUL",
        bond_stage="SOULMATE",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary is None
    assert decision.reason == "voice_unsuitable_text_shape"


def test_response_form_decider_echoes_voice_input_when_reply_is_speakable() -> None:
    decider = ResponseFormDecider(voice_enabled=True, probability_scale=1.0, min_voice_interval_ms=0)

    decision = decider.decide(
        user_id="u4",
        user_msg_type=MsgType.VOICE,
        user_text="我用语音问一下",
        narrative_zh="我听到了，这个我来慢慢跟你说。",
        emotion_tag="NEUTRAL",
        bond_stage="FRIEND",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary == MsgType.VOICE
    assert decision.reason == "voice_input_echo"


def test_response_form_decider_respects_text_only_request() -> None:
    decider = ResponseFormDecider(voice_enabled=True, probability_scale=1.0)

    decision = decider.decide(
        user_id="u5",
        user_msg_type=MsgType.TEXT,
        user_text="只发文字就行",
        narrative_zh="我在呢，先用文字给你说清楚。",
        emotion_tag="LOVING",
        bond_stage="SOULMATE",
    )

    assert decision.primary == MsgType.TEXT
    assert decision.secondary is None
    assert decision.reason == "user_requested_text"
