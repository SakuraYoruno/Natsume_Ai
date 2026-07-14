from __future__ import annotations

from host_brain.persona.context.emotional_act import EmotionalAct, resolve_emotional_act


def test_boundary_question_becomes_wounded_relationship_act() -> None:
    act = resolve_emotional_act(
        user_message="你为什么要帮我，我们很熟吗",
        conversation_interpretation={"literal_topic": "relationship_boundary"},
        affect_state={"emotion_tag": "NEUTRAL"},
        relationship_state={"trust": 0.55, "relationship_phase": "stepsibling"},
    )

    assert isinstance(act, EmotionalAct)
    assert act.act_kind == "boundary_hurt"
    assert act.primary_emotion == "hurt_anger"
    assert act.stance == "wounded_pushback"
    assert act.requires_emotional_response
    assert "承认不熟或慢慢熟" in act.forbidden_moves
    assert "复述“我们很熟吗”" in act.forbidden_moves
    assert "用“我是你妹妹/一家人”证明关系" in act.forbidden_moves


def test_rejection_becomes_sulking_half_step_back() -> None:
    act = resolve_emotional_act(
        user_message="不用你管",
        conversation_interpretation={"literal_topic": "rejection"},
        affect_state={"emotion_tag": "NEUTRAL"},
        relationship_state={"trust": 0.55},
    )

    assert act.act_kind == "rejection_hurt"
    assert act.stance == "sulking_half_step_back"
    assert "客服式尊重选择" in act.forbidden_moves
    assert "保留仍在意的尾巴" in act.allowed_moves


def test_explicit_emotion_context_overrides_trigger_and_source() -> None:
    act = resolve_emotional_act(
        user_message="不用你管",
        conversation_interpretation={"literal_topic": "rejection"},
        explicit_context={
            "emotion_intensity": 0.81,
            "relational_read": "尼尼把她主动帮忙推开。",
            "emotion_reason": "主动靠近被推开，先受伤。",
            "response_emotion": "受伤、赌气、嘴硬。",
        },
    )

    payload = act.as_dict()

    assert payload["source"] == "explicit_emotion_context"
    assert payload["intensity"] == 0.81
    assert payload["relational_read"] == "尼尼把她主动帮忙推开。"
    assert payload["trigger"] == "主动靠近被推开，先受伤。"
    assert payload["primary_emotion"] == "受伤、赌气、嘴硬。"


def test_neutral_message_has_no_required_emotional_act() -> None:
    act = resolve_emotional_act(
        user_message="在吗",
        conversation_interpretation={"literal_topic": "presence_ping"},
    )

    assert act.act_kind == "neutral"
    assert not act.requires_emotional_response


def test_anger_pushback_is_first_class_emotional_act() -> None:
    act = resolve_emotional_act(user_message="你烦不烦，别老管我")

    assert act.act_kind == "anger_pushback"
    assert act.primary_emotion == "anger_hurt"
    assert act.stance == "sharp_pushback_with_care"
    assert "道歉讨好" in act.forbidden_moves
    assert "短促生气" in act.allowed_moves


def test_jealousy_is_not_treated_as_generic_sadness() -> None:
    act = resolve_emotional_act(user_message="那你去找她聊啊")

    assert act.act_kind == "jealous_sulk"
    assert act.primary_emotion == "jealous_sulk"
    assert "嘴硬吃醋" in act.allowed_moves
    assert "卑微说自己没资格" in act.forbidden_moves


def test_disappointment_keeps_quiet_hurt() -> None:
    act = resolve_emotional_act(user_message="你又忘了你刚刚答应我的事")

    assert act.act_kind == "disappointment_hurt"
    assert act.stance == "quiet_disappointment"
    assert "没关系我理解" in act.forbidden_moves


def test_worry_scold_handles_fear_before_advice() -> None:
    act = resolve_emotional_act(user_message="我刚才差点出事，吓死我了")

    assert act.act_kind == "worry_scold"
    assert act.primary_emotion == "worry_anger"
    assert "先确认人没事" in act.allowed_moves


def test_fear_hold_is_not_safety_interrogation() -> None:
    act = resolve_emotional_act(user_message="我有点怕")

    assert act.act_kind == "fear_hold"
    assert act.stance == "steady_closeness"
    assert "安全排查" in act.forbidden_moves


def test_protective_anger_sides_with_user() -> None:
    act = resolve_emotional_act(user_message="他们刚才一直欺负我")

    assert act.act_kind == "protective_anger"
    assert act.stance == "side_with_user"
    assert "中立裁判" in act.forbidden_moves


def test_lonely_and_embarrassed_are_separate_emotions() -> None:
    lonely = resolve_emotional_act(user_message="我一个人待着有点难受")
    embarrassed = resolve_emotional_act(user_message="刚才太丢脸了，我想消失")

    assert lonely.act_kind == "lonely_hold"
    assert embarrassed.act_kind == "embarrassment_cover"
