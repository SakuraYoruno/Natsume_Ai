from __future__ import annotations

from host_brain.app.context.interaction_facts_plane import InteractionFactsPlane
from host_brain.persona.positive_style_bank import render_positive_style_guidance, select_positive_style_sample


def test_positive_style_bank_selects_presence_without_need_question() -> None:
    facts = InteractionFactsPlane(
        user_message="在吗",
        conversation_interpretation={"literal_topic": "presence_ping"},
        social_act={"act_kind": "presence_ack"},
    )

    sample = select_positive_style_sample(user_message="在吗", facts=facts, render_profile="zh_text")
    guidance, trace = render_positive_style_guidance(user_message="在吗", facts=facts, render_profile="zh_text")

    assert sample is not None
    assert sample.scene_id == "presence"
    assert trace["scene_id"] == "presence"
    assert "明确在场/正在回他" in guidance
    assert "不要追问需求" in guidance
    assert "怎么了" in guidance
    assert "不是最终回复模板" in guidance
    assert "可借局部语气碎片" not in guidance
    assert "不借固定短语" in guidance


def test_positive_style_bank_selects_boundary_hurt_as_relationship_pushback() -> None:
    facts = InteractionFactsPlane(
        user_message="你为什么要帮我，我们很熟吗",
        emotional_act={"act_kind": "boundary_hurt"},
    )

    sample = select_positive_style_sample(
        user_message="你为什么要帮我，我们很熟吗",
        facts=facts,
        render_profile="zh_text",
    )

    assert sample is not None
    assert sample.scene_id == "boundary_hurt"
    assert "不要承认不熟" in "；".join(sample.response_shape)
    assert "慢慢熟" in "；".join(sample.avoid)


def test_positive_style_bank_keeps_ja_voice_out_of_zh_guidance() -> None:
    facts = InteractionFactsPlane(user_message="在吗", social_act={"act_kind": "presence_ack"})

    sample = select_positive_style_sample(user_message="在吗", facts=facts, render_profile="ja_voice")
    guidance, trace = render_positive_style_guidance(user_message="在吗", facts=facts, render_profile="ja_voice")

    assert sample is None
    assert guidance == ""
    assert trace["enabled"] is False
    assert trace["reason"] == "UNSUPPORTED_PROFILE"


def test_positive_style_bank_boredom_is_not_activity_menu() -> None:
    facts = InteractionFactsPlane(user_message="好无聊", social_act={"act_kind": "boredom_join"})

    sample = select_positive_style_sample(user_message="好无聊", facts=facts, render_profile="zh_text")

    assert sample is not None
    assert sample.scene_id == "bored"
    rendered = sample.render()
    assert "不给选择题/活动清单" in rendered
    assert "你想做什么" in rendered
    assert "我也无聊" in rendered


def test_positive_style_bank_covers_daily_extended_scenes() -> None:
    samples = [
        ("刚才语气不好，抱歉", "apology_accept", "apology"),
        ("这个报错我看不懂，帮我看看", "help_takeover", "help_takeover"),
        ("帮我写一下这个", "write_takeover", "write_takeover"),
        ("你觉得我该不该继续试试", "advice_small_step", "advice_continue"),
        ("你是", "identity_confirm", "identity"),
        ("我要睡了", "good_night_soft", "good_night"),
        ("我刚吃完饭", "meal_share", "meal_share"),
        ("饿了", "hungry_care", "hungry"),
        ("困了", "sleepy_care", "sleepy"),
        ("头疼", "headache_care", "headache"),
        ("今天心情很差", "bad_mood_hold", "bad_mood"),
        ("刚刚弄砸了", "small_failure_hold", "small_failure"),
        ("你在干嘛", "what_doing_reply", "what_doing"),
        ("陪我玩会儿", "play_invite", "play_invite"),
        ("别吵", "quiet_hold", "quiet"),
        ("你怎么这么能说", "talkative_tease", "talkative"),
        ("谁叫你来的", "self_invited", "who_called"),
        ("有点想你", "miss_you_shy", "miss_you"),
        ("今天心情不错", "happy_share", "happy"),
        ("嗯", "short_ack_followup", "short_ack"),
        ("那怎么办", "next_step_help", "next_step"),
        ("算了不弄了", "give_up_pause", "give_up"),
    ]
    for user_message, act_kind, expected_scene in samples:
        facts = InteractionFactsPlane(user_message=user_message, social_act={"act_kind": act_kind})
        sample = select_positive_style_sample(
            user_message=user_message,
            facts=facts,
            render_profile="zh_text",
        )

        assert sample is not None, user_message
        assert sample.scene_id == expected_scene


def test_positive_style_bank_followup_and_annoying_from_text() -> None:
    followup_facts = InteractionFactsPlane(user_message="为什么")
    annoying_facts = InteractionFactsPlane(user_message="你好烦啊")

    followup = select_positive_style_sample(user_message="为什么", facts=followup_facts, render_profile="zh_text")
    annoying = select_positive_style_sample(user_message="你好烦啊", facts=annoying_facts, render_profile="zh_text")

    assert followup is not None
    assert followup.scene_id == "followup"
    assert "不要回因为" in "；".join(followup.response_shape)
    assert annoying is not None
    assert annoying.scene_id == "annoying_tease"
    assert "不道歉退场" in "；".join(annoying.response_shape)
