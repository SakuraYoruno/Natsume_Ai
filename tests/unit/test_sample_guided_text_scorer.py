from __future__ import annotations

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.sample_guided_text_scorer import SCORING_DIMENSIONS, score_sample_guided_text
from host_brain.persona.text_quality_gate import get_text_quality_gate


def test_sample_guided_scorer_exposes_expected_dimensions() -> None:
    contract = build_galgame_text_contract(user_message="你好", scene_id="daily_greeting_cn")

    scored = score_sample_guided_text(text="尼尼，别这么客气。", contract=contract)

    assert set(SCORING_DIMENSIONS).issubset(scored.dimensions)
    assert scored.sample_scene_id == "daily_greeting_cn"
    assert scored.score >= 80
    assert not scored.hard_fail


def test_context_related_words_are_soft_distance_penalties_not_hard_failures() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    scored = score_sample_guided_text(text="我在这，尼尼找我就回。", contract=contract)

    assert not scored.hard_fail
    assert "DISTANCE_CONTROL_SOFT_PENALTY" in scored.reason_codes
    assert scored.dimensions["distance_control"] < 86


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
    flavored_but_vague = score_sample_guided_text(text="哼，尼尼这时候倒是想起我了。", contract=contract)

    assert useful.score > flavored_but_vague.score
    assert useful.weights["technical_helpfulness"] > useful.weights["role_fit"]


def test_advice_continue_prefers_small_step_over_vague_support() -> None:
    contract = build_galgame_text_contract(user_message="你觉得我该不该继续试试", scene_id="")

    useful = score_sample_guided_text(text="再试一小步吧，累了就停。", contract=contract)
    vague = score_sample_guided_text(text="那当然继续啊，尼尼想做的事我肯定支持。", contract=contract)

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

    generic = score_sample_guided_text(text="行啊尼尼，有点东西嘛。", contract=contract)
    probing = score_sample_guided_text(text="哟，尼尼还挺能干的嘛，什么小事说来听听？", contract=contract)
    grounded = score_sample_guided_text(text="搞定一件小事也算进展，今天先得意一下。", contract=contract)

    assert contract.scene_id == "good_news"
    assert "小事" in contract.required_any[0]
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

    dodge = score_sample_guided_text(text="尼尼你故意的吧", contract=contract)
    grounded = score_sample_guided_text(text="新海天啦，尼尼记好了。", contract=contract)

    assert contract.scene_id == "identity"
    assert contract.required_any == (("新海天",),)
    assert "CONTEXT_MISSING_REQUIRED_MOVE" in dodge.reason_codes
    assert dodge.score < 80
    assert grounded.score >= 83
    assert grounded.score > dodge.score


def test_template_density_is_soft_penalty_not_hard_failure() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="presence")

    dense = score_sample_guided_text(text="哼，尼尼当然在啦，真是的嘛。", contract=contract)
    cleaner = score_sample_guided_text(text="在呢，这一句我看见了。", contract=contract)

    assert not dense.hard_fail
    assert "STYLE_TEMPLATE_DENSITY" in dense.reason_codes
    assert dense.dimensions["template_density"] < cleaner.dimensions["template_density"]
    assert cleaner.score > dense.score


def test_implied_fact_risk_penalizes_unprovided_time_or_cause_guess() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="back")

    risky = score_sample_guided_text(text="今天回来得挺早嘛。", contract=contract)
    grounded = score_sample_guided_text(text="回来啦，那继续。", contract=contract)

    assert not risky.hard_fail
    assert "IMPLIED_FACT_RISK" in risky.reason_codes
    assert risky.dimensions["implied_fact_risk"] < grounded.dimensions["implied_fact_risk"]
    assert grounded.score > risky.score


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

    flat = score_sample_guided_text(text="尼尼今天好正经我都不知道该怎么接你这个招呼了", contract=contract)
    rhythmic = score_sample_guided_text(text="尼尼今天好正经，别装外人啦。", contract=contract)

    assert "LINE_RHYTHM_FLAT" in flat.reason_codes
    assert rhythmic.dimensions["line_rhythm"] > flat.dimensions["line_rhythm"]
    assert rhythmic.score > flat.score


def test_success_fact_takes_priority_over_generic_happy_scene() -> None:
    contract = build_galgame_text_contract(user_message="终于跑通了", scene_id="happy")

    assert contract.scene_id == "good_news"
    scored = score_sample_guided_text(text="跑通啦，这才像话。", contract=contract)

    assert scored.score >= 80
    assert "CONTEXT_MISSING_REQUIRED_MOVE" not in scored.reason_codes


def test_wrap_up_counts_as_good_news_progress() -> None:
    contract = build_galgame_text_contract(user_message="事情收尾了", scene_id="happy")

    grounded = score_sample_guided_text(text="收尾了就好，尼尼能松口气了。", contract=contract)
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


def test_cheap_wave_punctuation_is_soft_cluster_penalty() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="daily_good_morning")
    gate = get_text_quality_gate()
    wave_quality = gate.validate(text="早啊尼尼~今天也陪我一下。", render_profile="zh_text")
    clean_quality = gate.validate(text="早啊，尼尼，今天也陪我一下。", render_profile="zh_text")

    wave = score_sample_guided_text(text=wave_quality.normalized_text, contract=contract, quality=wave_quality)
    clean = score_sample_guided_text(text=clean_quality.normalized_text, contract=contract, quality=clean_quality)

    assert not wave.hard_fail
    assert "CHEAP_WAVE_PUNCTUATION" in wave.reason_codes
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
    wave_quality = gate.validate(text="耶！跑通了好耶~", render_profile="zh_text")
    clean_quality = gate.validate(text="跑通啦，这才像话。", render_profile="zh_text")

    wave = score_sample_guided_text(text=wave_quality.normalized_text, contract=contract, quality=wave_quality)
    clean = score_sample_guided_text(text=clean_quality.normalized_text, contract=contract, quality=clean_quality)

    assert contract.scene_id == "good_news"
    assert "CHEAP_WAVE_PUNCTUATION" in wave.reason_codes
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
    incomplete_quality = gate.validate(text="当然在啦，尼尼想跟我说什么", render_profile="zh_text")
    clean_quality = gate.validate(text="在呢，尼尼，这句我看见了。", render_profile="zh_text")

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
    clean = score_sample_guided_text(text="在呢，尼尼，这句我看见了。", contract=contract)

    assert "PRESENCE_ATTENTION_DEBT" in debt.reason_codes
    assert debt.score < 80
    assert clean.score > debt.score


def test_write_takeover_content_anchor_counts_as_helpful() -> None:
    contract = build_galgame_text_contract(user_message="帮我写一下这个", scene_id="daily_chat")

    scored = score_sample_guided_text(text="放心啦，我来写。尼尼跟我说说内容就行，剩下的交给我。", contract=contract)

    assert contract.scene_id == "write_takeover"
    assert "LOW_TECHNICAL_HELPFULNESS" not in scored.reason_codes
    assert scored.score >= 83
