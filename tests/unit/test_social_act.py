from __future__ import annotations

from host_brain.app.context.social_act import resolve_social_act


def test_social_act_presence_avoids_need_question() -> None:
    act = resolve_social_act(user_message="在吗", conversation_interpretation={"literal_topic": "presence_ping"})

    payload = act.as_dict()
    assert payload["act_kind"] == "presence_ack"
    assert "问有什么事" in payload["avoid"]
    assert "别问需求" in payload["zh_directive"]
    assert "主动接住对话" in payload["should_do"]
    assert "凑过来" not in payload["zh_directive"]


def test_social_act_greeting_pulls_formality_back_to_chat() -> None:
    act = resolve_social_act(user_message="你好", conversation_interpretation={})

    payload = act.as_dict()
    assert payload["act_kind"] == "greeting_reply"
    assert "只复读你好/hello" in payload["avoid"]
    assert "不要只回“你好呀”" in payload["zh_directive"]


def test_social_act_wait_back_and_busy_are_distinct_daily_scenes() -> None:
    wait = resolve_social_act(user_message="等我一下", conversation_interpretation={}).as_dict()
    back = resolve_social_act(user_message="我回来了", conversation_interpretation={}).as_dict()
    busy = resolve_social_act(user_message="我先忙会儿", conversation_interpretation={}).as_dict()

    assert wait["act_kind"] == "wait_hold"
    assert "问要等多久" in wait["avoid"]
    assert back["act_kind"] == "back_return"
    assert "说被丢下" in back["avoid"]
    assert busy["act_kind"] == "busy_space"
    assert "催忙完" in busy["avoid"]


def test_social_act_boredom_is_active_join_not_requirements() -> None:
    act = resolve_social_act(user_message="好无聊", conversation_interpretation={})

    payload = act.as_dict()
    assert payload["act_kind"] == "boredom_join"
    assert "主动把气氛接起来" in payload["should_do"]
    assert "问想做什么/玩什么" in payload["avoid"]


def test_social_act_good_news_reads_running_as_success() -> None:
    act = resolve_social_act(user_message="终于跑通了", conversation_interpretation={})

    payload = act.as_dict()
    assert payload["act_kind"] == "good_news_share"
    assert "误解成跑步" in payload["avoid"]


def test_social_act_handles_daily_relationship_micro_scenes() -> None:
    assert resolve_social_act(user_message="刚才语气不好，抱歉", conversation_interpretation={}).act_kind == "apology_accept"
    assert resolve_social_act(user_message="我要睡了", conversation_interpretation={}).act_kind == "good_night_soft"
    assert resolve_social_act(user_message="有点想你", conversation_interpretation={}).act_kind == "miss_you_shy"
    assert resolve_social_act(user_message="你是", conversation_interpretation={}).act_kind == "identity_confirm"


def test_social_act_neutral_when_no_daily_scene() -> None:
    act = resolve_social_act(user_message="明天温度多少", conversation_interpretation={})

    assert act.act_kind == "neutral"
