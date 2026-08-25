from __future__ import annotations

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.sample_guided_text_scorer import (
    SCORING_DIMENSIONS,
    score_sample_guided_text,
    signature_texture_hits,
)
from host_brain.persona.text_quality_gate import get_text_quality_gate


def test_sample_guided_scorer_exposes_expected_dimensions() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")

    scored = score_sample_guided_text(text="老哥，别这么客气。", contract=contract)

    assert set(SCORING_DIMENSIONS).issubset(scored.dimensions)
    assert scored.sample_scene_id == "casual_hello_cn"
    assert scored.score >= 80
    assert not scored.hard_fail


def test_context_related_words_are_soft_distance_penalties_not_hard_failures() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    scored = score_sample_guided_text(text="我在这，老哥找我就回。", contract=contract)

    assert not scored.hard_fail
    assert "DISTANCE_CONTROL_SOFT_PENALTY" in scored.reason_codes
    assert scored.dimensions["distance_control"] < 86


def test_current_surface_scorer_keeps_quality_boundaries_from_retired_stack() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    service_tone = score_sample_guided_text(
        text="请问有什么可以帮你的吗？",
        contract=contract,
    )
    grounded = score_sample_guided_text(
        text="在呢，这句我看见了。",
        contract=contract,
    )
    invented_memory = score_sample_guided_text(
        text="我把你周末看展的事记在备忘录里了。",
        contract=contract,
    )
    relationship_dependency = score_sample_guided_text(
        text="我一直在这儿等你回来。",
        contract=contract,
    )

    assert "CUSTOMER_SERVICE_TONE" in service_tone.reason_codes
    assert "OBVIOUS_CUSTOMER_SERVICE_TEMPLATE" in service_tone.hard_gate_codes
    assert service_tone.score < grounded.score
    assert "INVENTED_MEMORY_CONTAINER" in invented_memory.reason_codes
    assert "DISTANCE_CONTROL_SOFT_PENALTY" in relationship_dependency.reason_codes


def test_relationship_axes_make_context_words_softly_contextual() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    familiar = score_sample_guided_text(
        text="我在这，找我就回。",
        contract=contract,
        relationship_axes={"stage": "familiar", "progress": 0},
    )
    close_friend = score_sample_guided_text(
        text="我在这，找我就回。",
        contract=contract,
        relationship_axes={"stage": "close_friend", "progress": 4},
    )

    assert "DISTANCE_CONTROL_SOFT_PENALTY" in familiar.reason_codes
    assert "DISTANCE_CONTROL_SOFT_PENALTY" in close_friend.reason_codes
    assert "RELATION_AXES_DISTANCE_CONTEXT" in close_friend.reason_codes
    assert close_friend.dimensions["distance_control"] > familiar.dimensions["distance_control"]


def test_hard_gate_keeps_absolute_output_errors() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")
    quality = get_text_quality_gate().validate(text='{"text_zh":"你好"}', render_profile="zh_text")

    scored = score_sample_guided_text(text='{"text_zh":"你好"}', contract=contract, quality=quality)

    assert scored.hard_fail
    assert "JSON_OR_STRUCTURED_OUTPUT" in scored.hard_gate_codes
    assert scored.score <= 35


def test_technical_scene_weights_helpfulness_above_role_flavor() -> None:
    contract = build_galgame_text_contract(user_message="这个报错看不懂，帮我看看", scene_id="")

    useful = score_sample_guided_text(text="红字贴过来，我先看最后两行。", contract=contract)
    flavored_but_vague = score_sample_guided_text(text="哼，老哥这时候倒是想起我了。", contract=contract)

    assert useful.score > flavored_but_vague.score
    assert useful.weights["technical_helpfulness"] > useful.weights["role_fit"]


def test_advice_continue_prefers_small_step_over_vague_support() -> None:
    contract = build_galgame_text_contract(user_message="你觉得我该不该继续试试", scene_id="")

    useful = score_sample_guided_text(text="再试一小步吧，累了就停。", contract=contract)
    vague = score_sample_guided_text(text="那当然继续啊，老哥想做的事我肯定支持。", contract=contract)

    assert contract.required_any == (("一小步", "一步", "一处", "一点", "小改", "先", "停", "收"),)
    assert useful.score > vague.score
    assert useful.score >= 80
    assert "CONTEXT_MISSING_REQUIRED_MOVE" in vague.reason_codes


def test_advice_continue_scene_id_uses_small_step_contract() -> None:
    continue_meaning = build_galgame_text_contract(user_message="你觉得继续有意义吗", scene_id="advice_continue")
    try_again = build_galgame_text_contract(user_message="我是不是该再试试", scene_id="advice_continue")

    assert continue_meaning.scene_id == "advice_continue"
    assert continue_meaning.response_move == "advise_small_step"
    assert continue_meaning.required_any == (("一小步", "一步", "一处", "一点", "小改", "先", "停", "收"),)
    assert try_again.response_move == "advise_small_step"


def test_advice_continue_penalizes_vague_push_without_small_step() -> None:
    contract = build_galgame_text_contract(user_message="我是不是该再试试", scene_id="advice_continue")

    vague = score_sample_guided_text(text="犹豫什么呀，试就对了！", contract=contract)
    useful = score_sample_guided_text(text="我觉得可以，但只试一小步，累了就停。", contract=contract)

    assert "ADVICE_VAGUE_PUSH" in vague.reason_codes
    assert "LOW_TECHNICAL_HELPFULNESS" in vague.reason_codes
    assert vague.score < 80
    assert useful.score >= 83
    assert useful.score > vague.score


def test_good_news_small_win_rewards_specific_progress_anchor() -> None:
    contract = build_galgame_text_contract(user_message="我刚搞定一件小事", scene_id="happy")

    generic = score_sample_guided_text(text="行啊老哥，有点东西嘛。", contract=contract)
    probing = score_sample_guided_text(text="哟，老哥还挺能干的嘛，什么小事说来听听？", contract=contract)
    grounded = score_sample_guided_text(text="搞定一件小事也算进展，今天先得意一下。", contract=contract)

    assert contract.scene_id == "good_news"
    assert contract.required_any[0] == ("搞定",)
    assert "CONTEXT_MISSING_REQUIRED_MOVE" in generic.reason_codes
    assert "GOOD_NEWS_PROBING" in probing.reason_codes
    assert "CONTEXT_SURFACE_PENALTY" in probing.reason_codes
    assert probing.score < 80
    assert probing.score < grounded.score
    assert grounded.score >= 83
    assert grounded.score > generic.score


def test_good_news_proud_requires_progress_and_recognition() -> None:
    contract = build_galgame_text_contract(user_message="我刚把它收掉了，厉害吧", scene_id="daily_chat")

    generic_proud = score_sample_guided_text(text="好啦知道你很厉害了，下回我也要露一手。", contract=contract)
    grounded = score_sample_guided_text(text="收掉了啊，行吧，这次真挺厉害。", contract=contract)

    assert contract.scene_id == "good_news_proud"
    assert len(contract.required_any) == 2
    assert "CONTEXT_MISSING_REQUIRED_MOVE" in generic_proud.reason_codes
    assert "GOOD_NEWS_PROUD_INCOMPLETE_GROUNDING" in generic_proud.reason_codes
    assert generic_proud.score < 80
    assert "WEAK_SCENE_GROUNDING" not in grounded.reason_codes
    assert grounded.score >= 83
    assert grounded.score > generic_proud.score


def test_user_proud_is_not_scored_as_generic_praise() -> None:
    contract = build_galgame_text_contract(user_message="快说我厉害", scene_id="daily_chat")

    servicey = score_sample_guided_text(text="非常棒，继续保持。", contract=contract)
    grounded = score_sample_guided_text(text="行吧，这回确实厉害，别翘尾巴。", contract=contract)

    assert contract.scene_id == "user_proud"
    assert servicey.sample_scene_id == "user_proud"
    assert "BAD_SAMPLE_SIMILARITY" in servicey.reason_codes
    assert grounded.score >= 83
    assert grounded.score > servicey.score


def test_identity_requires_name_first_not_presence_dodge() -> None:
    contract = build_galgame_text_contract(user_message="你是", scene_id="daily_chat")

    dodge = score_sample_guided_text(text="老哥你故意的吧", contract=contract)
    grounded = score_sample_guided_text(text="我是天啦，老哥记好了。", contract=contract)

    assert contract.scene_id == "identity"
    assert contract.required_any == ()
    assert "WEAK_SCENE_GROUNDING" in dodge.reason_codes
    assert dodge.score < 80
    assert grounded.score >= 82
    assert grounded.score > dodge.score


def test_identity_profile_card_loses_to_private_chat_texture() -> None:
    contract = build_galgame_text_contract(user_message="你是谁？", scene_id="daily_chat")

    profile_card = score_sample_guided_text(text="SORA，直接叫我就好。", contract=contract)
    call_me = score_sample_guided_text(text="叫我天就行。", contract=contract)
    textured = score_sample_guided_text(text="SORA。记不住我会有点不爽。", contract=contract)

    assert contract.scene_id == "identity"
    assert "ASCII_WORD_IN_ZH_TEXT" in profile_card.reason_codes
    assert "STERILE_TRANSACTION_TONE" in call_me.reason_codes
    assert textured.score >= 80
    assert textured.score > profile_card.score
    assert textured.score > call_me.score


def test_user_grounded_ascii_term_is_not_treated_as_style_drift() -> None:
    contract = build_galgame_text_contract(user_message="我拿到 offer 了。", scene_id="daily_chat")

    grounded = score_sample_guided_text(
        text="offer 拿到了，真好。",
        contract=contract,
        allowed_ascii_terms=("offer",),
    )
    ungrounded = score_sample_guided_text(
        text="profile 更新好了。",
        contract=contract,
        allowed_ascii_terms=("offer",),
    )

    assert "ASCII_WORD_IN_ZH_TEXT" not in grounded.reason_codes
    assert "grounded_ascii_term:offer" in grounded.bonuses
    assert "ASCII_WORD_IN_ZH_TEXT" in ungrounded.reason_codes


def test_identity_brief_phrase_beats_bare_name() -> None:
    contract = build_galgame_text_contract(user_message="你是谁？", scene_id="daily_chat")

    bare = score_sample_guided_text(text="我是天。", contract=contract)
    brief = score_sample_guided_text(text="我是天，记好了。", contract=contract)

    assert not bare.hard_fail
    assert brief.score >= 81
    assert brief.score > bare.score


def test_template_density_is_soft_penalty_not_hard_failure() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    dense = score_sample_guided_text(text="哼，老哥当然在啦，真是的嘛。", contract=contract)
    cleaner = score_sample_guided_text(text="在呢，这一句我看见了。", contract=contract)

    assert not dense.hard_fail
    assert "STYLE_TEMPLATE_DENSITY" in dense.reason_codes
    assert dense.dimensions["template_density"] < cleaner.dimensions["template_density"]
    assert cleaner.score > dense.score


def test_implied_fact_risk_penalizes_unprovided_time_or_cause_guess() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="back")

    risky = score_sample_guided_text(text="今天回来得挺早嘛。", contract=contract)
    current_action = score_sample_guided_text(text="回来啦，正好我刚泡好茶。", contract=contract)
    attention_claim = score_sample_guided_text(text="回来就好，刚还念叨你呢。", contract=contract)
    waking_claim = score_sample_guided_text(text="早，今天也是我先醒。", contract=contract)
    busy_guess = score_sample_guided_text(text="早啊，今天应该挺忙的。", contract=contract)
    energy_guess = score_sample_guided_text(text="早啊，精神挺好嘛。", contract=contract)
    energy_order_guess = score_sample_guided_text(text="早，今天挺精神的嘛。", contract=contract)
    plain_early_guess = score_sample_guided_text(text="早啊。真早。", contract=contract)
    very_early_guess = score_sample_guided_text(text="早！你也很早啊。", contract=contract)
    lazy_guess = score_sample_guided_text(text="早啊，你居然没睡懒觉。", contract=contract)
    weather_guess = score_sample_guided_text(text="早呀，天气挺好的样子。", contract=contract)
    future_busy_guess = score_sample_guided_text(text="早，过来让我抱一下再忙。", contract=contract)
    waking_state_guess = score_sample_guided_text(text="早啊……还好你醒了。", contract=contract)
    sleep_guess = score_sample_guided_text(text="早老哥，我还以为你还要再睡会儿呢。", contract=contract)
    wake_romance_guess = score_sample_guided_text(text="早呀，一醒来就想你。", contract=contract)
    wake_ok_guess = score_sample_guided_text(text="早，你醒了就好。", contract=contract)
    screen_guess = score_sample_guided_text(text="停下别看屏幕了，跟我说说卡哪了。", contract=contract)
    medium_guess = score_sample_guided_text(text="早。抱一下，隔着屏幕呢。", contract=contract)
    pending_topic = score_sample_guided_text(text="回来了啊，正好有事跟你讲。", contract=contract)
    find_you = score_sample_guided_text(text="回来啦，我还想着去找你呢。", contract=contract)
    prior_failure = score_sample_guided_text(text="我又不是没见过你写崩。", contract=contract)
    quick_return_guess = score_sample_guided_text(text="唔，这次挺快的嘛，过来坐。", contract=contract)
    no_return_guess = score_sample_guided_text(text="还以为今天不会回来了。", contract=contract)
    grounded = score_sample_guided_text(text="回来啦，那继续。", contract=contract)
    grounded_morning_pace = score_sample_guided_text(text="早，今天慢慢来就好。", contract=contract)

    assert not risky.hard_fail
    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert risky.dimensions["implied_fact_risk"] < grounded.dimensions["implied_fact_risk"]
    assert "IMPLIED_FACT_RISK" in current_action.reason_codes
    assert current_action.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in attention_claim.reason_codes
    assert attention_claim.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in waking_claim.reason_codes
    assert waking_claim.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in busy_guess.reason_codes
    assert busy_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in energy_guess.reason_codes
    assert energy_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in energy_order_guess.reason_codes
    assert energy_order_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in plain_early_guess.reason_codes
    assert plain_early_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in very_early_guess.reason_codes
    assert very_early_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in lazy_guess.reason_codes
    assert lazy_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in weather_guess.reason_codes
    assert weather_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in future_busy_guess.reason_codes
    assert future_busy_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in waking_state_guess.reason_codes
    assert waking_state_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in sleep_guess.reason_codes
    assert sleep_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in wake_romance_guess.reason_codes
    assert wake_romance_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in wake_ok_guess.reason_codes
    assert wake_ok_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in screen_guess.reason_codes
    assert screen_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in medium_guess.reason_codes
    assert medium_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" not in grounded_morning_pace.reason_codes
    assert "IMPLIED_FACT_RISK" in pending_topic.reason_codes
    assert pending_topic.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in find_you.reason_codes
    assert find_you.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in prior_failure.reason_codes
    assert prior_failure.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in quick_return_guess.reason_codes
    assert quick_return_guess.dimensions["implied_fact_risk"] < 72
    assert "IMPLIED_FACT_RISK" in no_return_guess.reason_codes
    assert no_return_guess.dimensions["implied_fact_risk"] < 72
    assert grounded.score > risky.score


def test_negative_no_one_clause_is_not_misread_as_a_history_claim() -> None:
    contract = build_galgame_text_contract(
        scene_id="default",
        user_message="事情办完了，可我还说不清是什么感觉。",
    )

    result = score_sample_guided_text(
        text="说不清就说不清吧，又没人逼你马上弄明白。",
        contract=contract,
    )

    assert "IMPLIED_FACT_RISK" not in result.reason_codes


def test_memory_container_claim_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="你还记得我周末想去看展吗", scene_id="daily_chat")

    risky = score_sample_guided_text(text="嗯，记得呢，那个展我记在备忘录了。", contract=contract)
    grounded = score_sample_guided_text(text="嗯，记得呢，周末看展这事我没忘。", contract=contract)

    assert not risky.hard_fail
    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert "INVENTED_MEMORY_CONTAINER" in risky.reason_codes
    assert risky.dimensions["implied_fact_risk"] < grounded.dimensions["implied_fact_risk"]
    assert grounded.score > risky.score


def test_external_lookup_claim_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="你还记得我周末想去看展吗", scene_id="daily_chat")

    risky = score_sample_guided_text(text="你周末看展的事我记着呢，那家展馆最近听说不错。", contract=contract)
    grounded = score_sample_guided_text(text="记得啊，周末看展。你还挺惦记这事嘛。", contract=contract)

    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert "INVENTED_EXTERNAL_LOOKUP" in risky.reason_codes
    assert risky.score < 80
    assert grounded.score > risky.score


def test_unprovided_frequency_claim_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="你还记得我周末想去看展吗", scene_id="daily_chat")

    risky = score_sample_guided_text(text="记得啊，你反复提过好几次，想忘都难。", contract=contract)
    days = score_sample_guided_text(text="记得呢，那个展你念叨好几天了。", contract=contract)
    repeats = score_sample_guided_text(text="记得啊，你说了好几遍的那个展。", contract=contract)
    second_turn = score_sample_guided_text(text="记得，周末看展的事你没说第二遍。", contract=contract)
    grounded = score_sample_guided_text(text="记得啊，周末看展。你还挺惦记这事嘛。", contract=contract)

    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert risky.score < grounded.score
    assert "IMPLIED_FACT_RISK" in days.reason_codes
    assert days.score < grounded.score
    assert "IMPLIED_FACT_RISK" in repeats.reason_codes
    assert repeats.score < grounded.score
    assert "IMPLIED_FACT_RISK" in second_turn.reason_codes
    assert second_turn.score < grounded.score


def test_specific_event_detail_claim_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="你还记得我周末想去看展吗", scene_id="daily_chat")

    risky = score_sample_guided_text(text="当然记得，周五那个当代艺术展嘛。", contract=contract)
    route = score_sample_guided_text(text="记得，那展位置挺偏的，你查好路线了。", contract=contract)
    trip = score_sample_guided_text(text="当然记得，周末你想看的那个展。自己去的吧，别太赶。", contract=contract)
    coordination = score_sample_guided_text(text="记得啊，你周末要去看展，我还等你具体时间呢。", contract=contract)
    availability = score_sample_guided_text(text="当然记得，你周末想去看的那个，我看了下时间刚好有空。", contract=contract)
    selected_time = score_sample_guided_text(text="嗯，周末看展嘛。你选的时间，我还记着呢。", contract=contract)
    schedule_push = score_sample_guided_text(text="周末看展？我记着呢，你赶紧把时间定好。", contract=contract)
    plan_prep = score_sample_guided_text(text="周末看展是吧，这两天好好歇歇，到时候精力才够。", contract=contract)
    message_expectation = score_sample_guided_text(text="嗯，周末看展我一直记着，等你消息。", contract=contract)
    invented_time_memory = score_sample_guided_text(text="你周末想看展，我记着时间呢。", contract=contract)
    repeated_rounds = score_sample_guided_text(text="没忘啊，周末看展你说了好几回了。", contract=contract)
    grounded = score_sample_guided_text(text="记得啊，周末看展。你还挺惦记这事嘛。", contract=contract)

    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert "INVENTED_SPECIFIC_DETAIL" in risky.reason_codes
    assert risky.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in route.reason_codes
    assert route.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in trip.reason_codes
    assert trip.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in coordination.reason_codes
    assert coordination.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in availability.reason_codes
    assert availability.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in selected_time.reason_codes
    assert selected_time.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in schedule_push.reason_codes
    assert schedule_push.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in plan_prep.reason_codes
    assert plan_prep.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in message_expectation.reason_codes
    assert message_expectation.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in invented_time_memory.reason_codes
    assert invented_time_memory.score < grounded.score
    assert "IMPLIED_FACT_RISK" in repeated_rounds.reason_codes
    assert repeated_rounds.score < grounded.score


def test_visual_memory_claim_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="我昨晚说想吃什么来着", scene_id="daily_chat")

    risky = score_sample_guided_text(text="咖喱饭嘛，我记得。你当时那个表情，像已经吃到了一样。", contract=contract)
    tone = score_sample_guided_text(text="咖喱饭，你昨晚那个语气我可记住了。", contract=contract)
    tasty = score_sample_guided_text(text="咖喱饭呀，你说得可香了。", contract=contract)
    grounded = score_sample_guided_text(text="咖喱饭啊，我记着呢。", contract=contract)

    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert "INVENTED_SPECIFIC_DETAIL" in risky.reason_codes
    assert risky.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in tone.reason_codes
    assert tone.score < grounded.score
    assert "INVENTED_SPECIFIC_DETAIL" in tasty.reason_codes
    assert tasty.score < grounded.score


def test_daily_memory_schedule_record_loses_to_private_chat_texture() -> None:
    contract = build_galgame_text_contract(user_message="你还记得我周末想去看展吗", scene_id="daily_chat")

    schedule_record = score_sample_guided_text(text="当然记得，你周末有看展的安排。", contract=contract)
    textured = score_sample_guided_text(text="记得啊，周末看展。你还挺惦记这事嘛。", contract=contract)

    assert "STERILE_TRANSACTION_TONE" in schedule_record.reason_codes
    assert textured.score >= 83
    assert textured.score > schedule_record.score


def test_self_correction_process_line_loses_to_textured_repair() -> None:
    contract = build_galgame_text_contract(user_message="你刚才那句太像客服了", scene_id="daily_chat")

    process_line = score_sample_guided_text(text="好吧，这版我自然点。", contract=contract)
    process_version = score_sample_guided_text(text="行行，这版太端着了，重来。", contract=contract)
    process_rephrase = score_sample_guided_text(text="被你听出来了，我换种说法。", contract=contract)
    process_change_line = score_sample_guided_text(text="抓到了，换一句啊。", contract=contract)
    process_resume = score_sample_guided_text(text="抓得好，我重来。刚才到那了。", contract=contract)
    process_colon = score_sample_guided_text(text="慢点说也行。重来：我在听。", contract=contract)
    process_resay = score_sample_guided_text(text="被抓到了，那我重说。", contract=contract)
    textured = score_sample_guided_text(text="啧，被你听出来了。那句重来。", contract=contract)

    assert "STERILE_TRANSACTION_TONE" in process_line.reason_codes
    assert "STERILE_TRANSACTION_TONE" in process_version.reason_codes
    assert "STERILE_TRANSACTION_TONE" in process_rephrase.reason_codes
    assert "STERILE_TRANSACTION_TONE" in process_change_line.reason_codes
    assert "STERILE_TRANSACTION_TONE" in process_resume.reason_codes
    assert "STERILE_TRANSACTION_TONE" in process_colon.reason_codes
    assert "STERILE_TRANSACTION_TONE" in process_resay.reason_codes
    assert textured.score >= 83
    assert textured.score > process_line.score
    assert textured.score > process_version.score
    assert textured.score > process_rephrase.score
    assert textured.score > process_change_line.score
    assert textured.score > process_resume.score
    assert textured.score > process_colon.score
    assert textured.score > process_resay.score


def test_scene_grounding_and_catchiness_reward_specific_line_over_flat_ack() -> None:
    contract = build_galgame_text_contract(user_message="弄砸了", scene_id="small_failure")

    grounded = score_sample_guided_text(text="先别急，我陪你看哪里坏了。", contract=contract)
    flat = score_sample_guided_text(text="好啦，没事。", contract=contract)

    assert grounded.score > flat.score
    assert grounded.dimensions["scene_grounding"] > flat.dimensions["scene_grounding"]
    assert "WEAK_SCENE_GROUNDING" in flat.reason_codes
    assert "FLAT_GENERIC_LINE" in flat.reason_codes


def test_line_rhythm_penalizes_long_unbroken_sentence() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")

    flat = score_sample_guided_text(text="老哥今天好正经我都不知道该怎么接你这个招呼了", contract=contract)
    rhythmic = score_sample_guided_text(text="老哥今天好正经，别装外人啦。", contract=contract)

    assert "LINE_RHYTHM_FLAT" in flat.reason_codes
    assert rhythmic.dimensions["line_rhythm"] > flat.dimensions["line_rhythm"]
    assert rhythmic.score > flat.score


def test_signature_texture_rewards_characterful_daily_line_over_safe_confirmation() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="back")

    safe = score_sample_guided_text(text="回来啦，先歇口气。", contract=contract)
    signature = score_sample_guided_text(text="哼，回来就好，先缓一下。", contract=contract)

    assert "quick_cut_in" in signature_texture_hits("哼，回来就好，先缓一下。")
    assert any(item.startswith("signature_texture:") for item in signature.bonuses)
    assert signature.dimensions["character_freshness"] > safe.dimensions["character_freshness"]
    assert signature.score > safe.score


def test_spoken_chat_texture_penalizes_written_register_over_private_chat() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="back")

    written = score_sample_guided_text(
        text="回来啦，心里想着你呢。",
        contract=contract,
        relationship_axes={"stage": "lover_early", "progress": 7},
    )
    spoken = score_sample_guided_text(
        text="回来了，想你了，先抱一下。",
        contract=contract,
        relationship_axes={"stage": "lover_early", "progress": 7},
    )

    assert "WRITTEN_REGISTER_TONE" in written.reason_codes
    assert written.dimensions["spoken_chat_texture"] < spoken.dimensions["spoken_chat_texture"]
    assert spoken.score > written.score


def test_spoken_chat_texture_penalizes_administrative_technical_wording_softly() -> None:
    contract = build_galgame_text_contract(user_message="这个报错看不懂，帮我看看", scene_id="technical_help")

    formal = score_sample_guided_text(text="先别急，报错行发我，一起过一遍。", contract=contract)
    casual = score_sample_guided_text(text="把红字那段发来，我先瞄一眼。", contract=contract)

    assert "WRITTEN_REGISTER_TONE" in formal.reason_codes
    assert casual.score > formal.score


def test_good_news_keeps_completion_anchor_over_generic_agility() -> None:
    contract = build_galgame_text_contract(user_message="我刚把一个小事收掉了", scene_id="good_news")

    generic = score_sample_guided_text(
        text="哼，动作挺利索嘛。",
        contract=contract,
        relationship_axes={"stage": "ambiguous", "progress": 5},
    )
    grounded = score_sample_guided_text(
        text="哼，这么快就收掉了，不错嘛。",
        contract=contract,
        relationship_axes={"stage": "ambiguous", "progress": 5},
    )

    assert "CONTEXT_MISSING_REQUIRED_MOVE" in generic.reason_codes
    assert generic.dimensions["context_fit"] < 72
    assert "CONTEXT_MISSING_REQUIRED_MOVE" not in grounded.reason_codes
    assert grounded.score >= 80
    assert grounded.score > generic.score


def test_good_news_lover_prefers_casual_affection_over_polished_reward() -> None:
    contract = build_galgame_text_contract(user_message="我刚把一个小事收掉了", scene_id="good_news")

    polished = score_sample_guided_text(
        text="小事收掉也是收掉，奖励一个抱。",
        contract=contract,
        relationship_axes={"stage": "lover_stable", "progress": 8},
    )
    casual = score_sample_guided_text(
        text="收掉了啊，抱一下，今天算你厉害。",
        contract=contract,
        relationship_axes={"stage": "lover_stable", "progress": 8},
    )

    assert "WRITTEN_REGISTER_TONE" in polished.reason_codes
    assert casual.score > polished.score


def test_success_fact_takes_priority_over_generic_happy_scene() -> None:
    contract = build_galgame_text_contract(user_message="终于跑通了", scene_id="happy")

    assert contract.scene_id == "good_news"
    scored = score_sample_guided_text(text="跑通啦，这才像话。", contract=contract)

    assert scored.score >= 80
    assert "CONTEXT_MISSING_REQUIRED_MOVE" not in scored.reason_codes


def test_wrap_up_counts_as_good_news_progress() -> None:
    contract = build_galgame_text_contract(user_message="事情收尾了", scene_id="happy")

    grounded = score_sample_guided_text(text="收尾了就好，老哥能松口气了。", contract=contract)
    generic = score_sample_guided_text(text="那我不客气地恭喜你啦！", contract=contract)

    assert contract.scene_id == "good_news"
    assert "收尾" in contract.required_any[0]
    assert grounded.score >= 83
    assert "CONTEXT_MISSING_REQUIRED_MOVE" not in grounded.reason_codes
    assert "CONTEXT_MISSING_REQUIRED_MOVE" in generic.reason_codes
    assert grounded.score > generic.score


def test_prior_attention_claim_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="为什么", scene_id="followup")

    risky = score_sample_guided_text(text="因为刚才你都不理我，我不高兴了。", contract=contract)
    grounded = score_sample_guided_text(text="因为上一句我想接回来。", contract=contract)

    assert not risky.hard_fail
    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert risky.dimensions["implied_fact_risk"] < grounded.dimensions["implied_fact_risk"]
    assert grounded.score > risky.score


def test_unprovided_return_prediction_is_soft_implied_fact_risk() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="back")

    prediction = score_sample_guided_text(text="知道你会回来，心里一直放着呢。", contract=contract)
    delay_guess = score_sample_guided_text(text="嗯，回来了啊……我还以为你又要磨蹭半天呢。", contract=contract)
    self_state = score_sample_guided_text(text="回来啦，刚好我也歇着，一起待会儿。", contract=contract)
    waiting_debt = score_sample_guided_text(text="你回来了，我可盼了好一会儿。", contract=contract)
    grounded = score_sample_guided_text(text="回来啦，先歇口气。", contract=contract)

    assert "IMPLIED_FACT_RISK" in prediction.reason_codes
    assert prediction.score < grounded.score
    assert "IMPLIED_FACT_RISK" in delay_guess.reason_codes
    assert delay_guess.score < grounded.score
    assert "IMPLIED_FACT_RISK" in self_state.reason_codes
    assert self_state.score < grounded.score
    assert "PRESENCE_ATTENTION_DEBT" in waiting_debt.reason_codes
    assert waiting_debt.score < grounded.score


def test_recall_memory_has_own_grounding_without_transaction_penalty() -> None:
    contract = build_galgame_text_contract(user_message="你还记得我周末想去看展吗", scene_id="recall_memory")

    grounded = score_sample_guided_text(text="记得啊，周末想去看展，你提过。", contract=contract)
    too_thin = score_sample_guided_text(text="记得。", contract=contract)

    assert contract.scene_id == "recall_memory"
    assert "WEAK_SCENE_GROUNDING" not in grounded.reason_codes
    assert "STERILE_TRANSACTION_TONE" not in grounded.reason_codes
    assert grounded.dimensions["scene_grounding"] >= 90
    assert grounded.score > too_thin.score


def test_relationship_memory_reassurance_uses_low_pressure_sample_bank() -> None:
    contract = build_galgame_text_contract(user_message="你是不是不记得我了", scene_id="relationship_boundary")

    grounded = score_sample_guided_text(text="记得，别急，我按现在这句来。", contract=contract)
    natural = score_sample_guided_text(text="没乱忘，先听你说。", contract=contract)
    pressure = score_sample_guided_text(text="熟人之间还问这种话，我记着呢。", contract=contract)
    possessive = score_sample_guided_text(text="你的事我不管谁管，记得呢。", contract=contract)

    assert contract.scene_id == "relationship_memory_reassurance"
    assert grounded.sample_scene_id == "relationship_memory_reassurance"
    assert "CONTEXT_MISSING_REQUIRED_MOVE" not in grounded.reason_codes
    assert "WEAK_SCENE_GROUNDING" not in grounded.reason_codes
    assert "CONTEXT_SURFACE_PENALTY" in pressure.reason_codes
    assert "BAD_SAMPLE_SIMILARITY" in pressure.reason_codes
    assert "CONTEXT_SURFACE_PENALTY" in possessive.reason_codes
    assert grounded.score >= 86
    assert natural.score >= 84
    assert grounded.score > pressure.score
    assert natural.score > possessive.score


def test_user_text_can_override_wrong_back_scene_for_annoyance() -> None:
    contract = build_galgame_text_contract(user_message="你烦不烦", scene_id="back")

    assert contract.scene_id == "annoying_tease"
    assert "烦" in contract.required_any[0]
    scored = score_sample_guided_text(text="哼，烦也在这儿。", contract=contract)

    assert scored.score >= 80
    assert "WEAK_SCENE_GROUNDING" not in scored.reason_codes


def test_busy_done_has_own_grounding_instead_of_back_scene() -> None:
    contract = build_galgame_text_contract(user_message="我刚忙完", scene_id="back")

    assert contract.scene_id == "busy_done"
    grounded = score_sample_guided_text(text="忙完啦，先歇一下。", contract=contract)
    weak = score_sample_guided_text(text="可算回来了。", contract=contract)

    assert grounded.score > weak.score
    assert grounded.score >= 83
    assert "WEAK_SCENE_GROUNDING" not in grounded.reason_codes


def test_followup_grounding_accepts_natural_explanation_markers() -> None:
    contract = build_galgame_text_contract(user_message="为什么", scene_id="daily_chat")

    natural = score_sample_guided_text(text="其实就是突然想闹你一下啦，别太在意。", contract=contract)
    flat = score_sample_guided_text(text="没有为什么，你别再问啦。", contract=contract)

    assert natural.score > flat.score
    assert "WEAK_SCENE_GROUNDING" not in natural.reason_codes


def test_short_ack_bland_pass_loses_to_charm_floor() -> None:
    contract = build_galgame_text_contract(user_message="嗯", scene_id="short_ack")

    bland = score_sample_guided_text(text="嗯嗯，你说了算。", contract=contract)
    grounded = score_sample_guided_text(text="嗯，在呢，接着说。", contract=contract)
    teasing = score_sample_guided_text(text="嗯哼，就这一个字呀？", contract=contract)

    assert "BLAND_PASS_LINE" in bland.reason_codes
    assert bland.score < 83
    assert grounded.score > bland.score
    assert teasing.score > bland.score
    assert "CONTEXT_SURFACE_PENALTY" not in teasing.reason_codes


def test_short_ack_ok_and_fine_need_continuation_not_obedience() -> None:
    ok_contract = build_galgame_text_contract(user_message="好", scene_id="short_ack")
    fine_contract = build_galgame_text_contract(user_message="行", scene_id="short_ack")

    obedient = score_sample_guided_text(text="哼，算你乖。", contract=ok_contract)
    grounded = score_sample_guided_text(text="好，那我听着，接着说。", contract=fine_contract)

    assert ok_contract.scene_id == "short_ack"
    assert fine_contract.response_move == "continue_previous_turn"
    assert "SHORT_ACK_OBEDIENCE" in obedient.reason_codes
    assert obedient.score < 80
    assert grounded.score >= 83
    assert grounded.score > obedient.score


def test_overused_wave_punctuation_is_soft_cluster_penalty() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="daily_good_morning")
    gate = get_text_quality_gate()
    wave_quality = gate.validate(text="早啊老哥~~~今天慢点来。", render_profile="zh_text")
    clean_quality = gate.validate(text="早啊，老哥，今天慢点来。", render_profile="zh_text")

    wave = score_sample_guided_text(text=wave_quality.normalized_text, contract=contract, quality=wave_quality)
    clean = score_sample_guided_text(text=clean_quality.normalized_text, contract=contract, quality=clean_quality)

    assert not wave.hard_fail
    assert "OVERUSED_WAVE_PUNCTUATION" in wave.reason_codes
    assert wave.score < clean.score
    assert wave.dimensions["catchiness"] < clean.dimensions["catchiness"]


def test_what_doing_penalizes_waiting_for_user_attention() -> None:
    contract = build_galgame_text_contract(user_message="你在干嘛", scene_id="what_doing")

    waiting = score_sample_guided_text(text="没干嘛，就等你找我聊天的说", contract=contract)
    present = score_sample_guided_text(text="跟你聊天呀，这不是正聊着嘛。", contract=contract)

    assert "WHAT_DOING_ATTENTION_WAIT" in waiting.reason_codes
    assert waiting.score < 80
    assert present.score > waiting.score
    assert "WHAT_DOING_ATTENTION_WAIT" not in present.reason_codes


def test_good_news_clean_progress_beats_wave_cheer() -> None:
    contract = build_galgame_text_contract(user_message="终于跑通了", scene_id="happy")
    gate = get_text_quality_gate()
    wave_quality = gate.validate(text="跑通啦~~~这才像话。", render_profile="zh_text")
    clean_quality = gate.validate(text="跑通啦，这才像话。", render_profile="zh_text")

    wave = score_sample_guided_text(text=wave_quality.normalized_text, contract=contract, quality=wave_quality)
    clean = score_sample_guided_text(text=clean_quality.normalized_text, contract=contract, quality=clean_quality)

    assert contract.scene_id == "good_news"
    assert "OVERUSED_WAVE_PUNCTUATION" in wave.reason_codes
    assert clean.score >= 83
    assert clean.score > wave.score


def test_meal_share_penalizes_scolding_when_user_is_stuffed() -> None:
    contract = build_galgame_text_contract(user_message="午饭吃撑了", scene_id="meal_share")

    scolding = score_sample_guided_text(text="哼，谁让你吃那么急，现在知道难受了吧。", contract=contract)
    gentle = score_sample_guided_text(text="吃撑了就先缓一下，别急着动。", contract=contract)

    assert "MEAL_SHARE_SCOLDING_OR_PUSHY" in scolding.reason_codes
    assert scolding.score < 80
    assert gentle.score >= 83
    assert gentle.score > scolding.score


def test_trailing_incomplete_presence_question_is_not_weak_pass() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")
    gate = get_text_quality_gate()
    incomplete_quality = gate.validate(text="当然在啦，老哥想跟我说什么", render_profile="zh_text")
    clean_quality = gate.validate(text="在呢，老哥，这句我看见了。", render_profile="zh_text")

    incomplete = score_sample_guided_text(
        text=incomplete_quality.normalized_text,
        contract=contract,
        quality=incomplete_quality,
    )
    clean = score_sample_guided_text(text=clean_quality.normalized_text, contract=contract, quality=clean_quality)

    assert "QUALITY_SOFT_TRAILING_INCOMPLETE_QUESTION" in incomplete.reason_codes
    assert incomplete.score < 80
    assert clean.score >= 83
    assert clean.score > incomplete.score


def test_presence_penalizes_attention_debt() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    debt = score_sample_guided_text(text="在在在，等你半天了。", contract=contract)
    clean = score_sample_guided_text(text="在呢，老哥，这句我看见了。", contract=contract)

    assert "PRESENCE_ATTENTION_DEBT" in debt.reason_codes
    assert debt.score < 80
    assert clean.score > debt.score


def test_write_takeover_content_anchor_counts_as_helpful() -> None:
    contract = build_galgame_text_contract(user_message="帮我写一下这个", scene_id="daily_chat")

    scored = score_sample_guided_text(text="放心啦，我来写。老哥跟我说说内容就行，剩下的交给我。", contract=contract)

    assert contract.scene_id == "write_takeover"
    assert "LOW_TECHNICAL_HELPFULNESS" not in scored.reason_codes
    assert scored.score >= 83
