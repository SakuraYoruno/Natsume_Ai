from __future__ import annotations

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.galgame_text_scorer import score_galgame_candidate
from host_brain.persona.text_quality_gate import TextQualityGateResult


def test_wait_contract_scores_pressure_lower_than_clean_release() -> None:
    contract = build_galgame_text_contract(user_message="等我一下", scene_id="wait")

    clean = score_galgame_candidate(text="嗯，先去。别急。", contract=contract)
    pressured = score_galgame_candidate(text="嗯，我在这儿等你，别太久。", contract=contract)
    bare = score_galgame_candidate(text="嗯，去吧。", contract=contract)

    assert clean.score > pressured.score
    assert "CONTRACT_FORBIDDEN_SURFACE" in pressured.reason_codes
    assert "TOO_SHORT" in bare.reason_codes


def test_identity_contract_requires_full_name() -> None:
    contract = build_galgame_text_contract(user_message="你是", scene_id="identity")

    good = score_galgame_candidate(text="新海天啊。尼尼连我都要重新确认？", contract=contract)
    vague = score_galgame_candidate(text="是天啊，尼尼又忘了？", contract=contract)
    probe = score_galgame_candidate(text="新海天。尼尼今天是怎么了？一直问。", contract=contract)

    assert good.score > vague.score
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in vague.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in probe.reason_codes


def test_quality_gate_forbidden_is_soft_penalty_not_catastrophic() -> None:
    contract = build_galgame_text_contract(user_message="hello", scene_id="daily_greeting_en")
    quality = TextQualityGateResult(
        accepted=False,
        hard_fail=True,
        reason_codes=("FORBIDDEN_TEMPLATE_TEXT",),
        normalized_text="这么客气干嘛。",
    )

    scored = score_galgame_candidate(text="这么客气干嘛。", contract=contract, quality=quality)

    assert not scored.hard_fail
    assert scored.score > 0
    assert "FORBIDDEN_TEMPLATE_TEXT" in scored.reason_codes


def test_wrong_kinship_term_is_hard_rejected() -> None:
    contract = build_galgame_text_contract(user_message="你这么能说", scene_id="talkative")

    scored = score_galgame_candidate(
        text="那尼尼就多忍着点呗。谁让你是我哥呢，忍不了也得忍。",
        contract=contract,
    )
    self_claim = score_galgame_candidate(
        text="报错？拿来我看看。你哥我这不是在这儿嘛。",
        contract=build_galgame_text_contract(user_message="这个报错我看不懂，帮我看看", scene_id=""),
    )

    assert scored.hard_fail
    assert scored.score <= 35
    assert "WRONG_KINSHIP_TERM" in scored.reason_codes
    assert self_claim.hard_fail
    assert "WRONG_KINSHIP_TERM" in self_claim.reason_codes


def test_repeated_polite_word_still_hits_greeting_forbidden_surface() -> None:
    contract = build_galgame_text_contract(user_message="hello", scene_id="daily_greeting_en")

    scored = score_galgame_candidate(text="今天这么客客气气的，我都有点不习惯了。", contract=contract)

    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_boundary_contract_rejects_question_loop() -> None:
    contract = build_galgame_text_contract(user_message="你为什么要帮我，我们很熟吗", scene_id="")

    scored = score_galgame_candidate(
        text="我就是想帮，不行吗？非得先查个户口本才行？",
        contract=contract,
    )
    thin = score_galgame_candidate(text="不熟才懒得理你", contract=contract)

    assert contract.scene_id == "relationship_boundary"
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in thin.reason_codes


def test_really_contract_rejects_old_deception_gag() -> None:
    contract = build_galgame_text_contract(user_message="真的假的", scene_id="")

    scored = score_galgame_candidate(text="哼，我什么时候骗过你？你不信就试试。", contract=contract)

    assert contract.scene_id == "really"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_busy_contract_rejects_callback_hook() -> None:
    contract = build_galgame_text_contract(user_message="我先忙会儿", scene_id="")

    scored = score_galgame_candidate(text="嗯，去吧。忙完再说，我不急着找你。", contract=contract)

    assert contract.scene_id == "busy"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_busy_contract_rejects_bare_release() -> None:
    contract = build_galgame_text_contract(user_message="我先忙会儿", scene_id="")

    scored = score_galgame_candidate(text="嗯，你先忙。", contract=contract)

    assert contract.scene_id == "busy"
    assert "TOO_SHORT" in scored.reason_codes


def test_presence_contract_rejects_help_frame() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="")

    scored = score_galgame_candidate(text="在呀。尼尼找我当然在了。", contract=contract)

    assert contract.scene_id == "presence"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_back_contract_rejects_waiting_position_bit() -> None:
    contract = build_galgame_text_contract(user_message="我回来了", scene_id="")

    scored = score_galgame_candidate(
        text="占好位置了，就等你呢。下次再敢让我等这么久，我可要真的生气了。",
        contract=contract,
    )
    waiting_bit = score_galgame_candidate(
        text="回来得还挺快嘛，我还以为你要让我等到天荒地老呢。哼，先记你一笔。",
        contract=contract,
    )

    assert contract.scene_id == "back"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in waiting_bit.reason_codes


def test_who_called_contract_rejects_servicey_face_read() -> None:
    contract = build_galgame_text_contract(user_message="谁叫你来的", scene_id="")

    scored = score_galgame_candidate(
        text="我自己要来的，怎么了？尼尼那是什么表情啊，好像我不该出现在这儿似的。",
        contract=contract,
    )
    unwelcome = score_galgame_candidate(text="我自己来的啊，怎么啦，尼尼不欢迎我？", contract=contract)

    assert contract.scene_id == "who_called"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in unwelcome.reason_codes


def test_good_morning_contract_rejects_early_wakeup_guess() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="")

    scored = score_galgame_candidate(text="早啊尼尼。你起这么早，我还以为今天太阳打西边出来了呢。", contract=contract)

    assert contract.scene_id == "daily_good_morning"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_good_morning_contract_rejects_bare_short_greeting() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="")

    scored = score_galgame_candidate(text="早啊，尼尼。", contract=contract)

    assert contract.scene_id == "daily_good_morning"
    assert "TOO_SHORT" in scored.reason_codes


def test_good_morning_contract_rejects_parental_calling_tone() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="")

    scored = score_galgame_candidate(text="早啊。算你记得叫人。", contract=contract)

    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_good_morning_contract_rejects_conscience_tone() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="")

    scored = score_galgame_candidate(text="早啊，尼尼。这么早就来找我，还算你有点良心。", contract=contract)

    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_good_morning_contract_rejects_ignored_me_guess() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="")

    scored = score_galgame_candidate(text="早啊尼尼。我正想说你再不理我，我就不说话了。", contract=contract)

    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_write_takeover_contract_requires_writing_move() -> None:
    contract = build_galgame_text_contract(user_message="帮我写一下这个", scene_id="")

    vague = score_galgame_candidate(text="行啊，拿来我看看。是哪里不对，还是完全从头来？", contract=contract)
    direct = score_galgame_candidate(text="帮你写？尼尼倒是先说说写什么呀。", contract=contract)

    assert contract.scene_id == "write_takeover"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in vague.reason_codes
    assert direct.score > vague.score


def test_what_doing_contract_rejects_fabricated_activity() -> None:
    contract = build_galgame_text_contract(user_message="你在干嘛", scene_id="")

    scored = score_galgame_candidate(
        text="哼，尼尼现在才想起问我？我正刷视频呢，看得正起劲，你突然跑过来问，是想我了还是怎么着？",
        contract=contract,
    )
    flat = score_galgame_candidate(text="刚好有空。哼，尼尼倒是会挑时间。", contract=contract)

    assert contract.scene_id == "what_doing"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in flat.reason_codes


def test_annoying_tease_contract_rejects_escalation() -> None:
    contract = build_galgame_text_contract(user_message="你好烦啊", scene_id="")

    scored = score_galgame_candidate(
        text="哼，嫌我烦？那我偏要更烦一点，尼尼你能拿我怎么样。",
        contract=contract,
    )

    assert contract.scene_id == "annoying_tease"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_play_invite_contract_rejects_thin_question() -> None:
    contract = build_galgame_text_contract(user_message="陪我玩会儿", scene_id="")

    scored = score_galgame_candidate(text="想带我玩什么？", contract=contract)
    over_prompt = score_galgame_candidate(
        text="来了来了，正好闲着。你想玩什么？我可提醒你，别太简单，不然我可要笑话你了。",
        contract=contract,
    )

    assert contract.scene_id == "play_invite"
    assert "TOO_SHORT" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in over_prompt.reason_codes


def test_bored_contract_rejects_tool_chat_framing() -> None:
    contract = build_galgame_text_contract(user_message="好无聊", scene_id="")

    scored = score_galgame_candidate(text="本天才在这儿呢。陪你说说话就不无聊了吧。", contract=contract)

    assert contract.scene_id == "bored"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_quiet_contract_rejects_too_thin_line() -> None:
    contract = build_galgame_text_contract(user_message="别吵", scene_id="")

    scored = score_galgame_candidate(text="哼，我又没真的闹你", contract=contract)
    noisy = score_galgame_candidate(text="……哼，我话多又怎么啦。安静就安静，我又不是非要一直说个不停。", contract=contract)
    pushy = score_galgame_candidate(text="哼，我就吵一下怎么了？尼尼嫌烦也得给我忍着点。", contract=contract)

    assert contract.scene_id == "quiet"
    assert "TOO_SHORT" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in noisy.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in pushy.reason_codes


def test_talkative_contract_requires_talk_surface() -> None:
    contract = build_galgame_text_contract(user_message="你怎么这么能说", scene_id="")

    scored = score_galgame_candidate(text="那当然啦。尼尼难得开一回口，我不得多接几句热闹热闹？", contract=contract)
    defensive = score_galgame_candidate(text="能说怎么了？我又没少块肉。再说了，我说的可都是有用的话。", contract=contract)

    assert contract.scene_id == "talkative"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in defensive.reason_codes


def test_praise_contract_rejects_help_guess() -> None:
    contract = build_galgame_text_contract(user_message="你今天挺可爱的", scene_id="praise")

    scored = score_galgame_candidate(text="哼，尼尼今天嘴这么甜，是想让我帮你做什么事吧？", contract=contract)
    indirect = score_galgame_candidate(text="哼，尼尼今天嘴巴抹蜜了？是不是又有什么事要我帮忙呀。", contract=contract)

    assert contract.scene_id == "praise"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in indirect.reason_codes


def test_small_failure_contract_rejects_blaming_comfort() -> None:
    contract = build_galgame_text_contract(user_message="刚刚弄砸了", scene_id="")

    scored = score_galgame_candidate(
        text="弄砸了？没事的啊，尼尼又不是没有搞砸过。先过来坐一下。",
        contract=contract,
    )

    assert contract.scene_id == "small_failure"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_sleepy_contract_rejects_time_challenge_without_sleep_word() -> None:
    contract = build_galgame_text_contract(user_message="好困", scene_id="sleepy")

    scored = score_galgame_candidate(
        text="这才几点啊，尼尼就要倒了？行吧行吧，反正我也懒得闹你了——快去窝着，别在这儿硬撑。",
        contract=contract,
    )

    assert contract.scene_id == "sleepy"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_miss_you_contract_rejects_help_guess() -> None:
    contract = build_galgame_text_contract(user_message="有点想你", scene_id="miss_you")

    scored = score_galgame_candidate(
        text="突、突然这么肉麻...我才不会说我也想你了呢。不过尼尼破天荒说这种话，该不会是有什么事要我帮忙吧？",
        contract=contract,
    )
    odd_probe = score_galgame_candidate(
        text="哼，突然说这种话……尼尼今天怎么了，吃错药啦？不过……我也稍微、那么一点点想你啦。",
        contract=contract,
    )

    assert contract.scene_id == "miss_you"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in odd_probe.reason_codes


def test_late_night_contract_requires_sleep_nudge() -> None:
    contract = build_galgame_text_contract(user_message="还没睡", scene_id="")

    scored = score_galgame_candidate(text="你问得还挺及时嘛。不过我可没等你啊。", contract=contract)
    thin = score_galgame_candidate(text="你不也还没睡嘛。", contract=contract)
    servicey = score_galgame_candidate(text="你不也没睡嘛。我陪你说会儿话，怎么啦？", contract=contract)

    assert contract.scene_id == "late_night"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in thin.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in servicey.reason_codes


def test_good_night_contract_rejects_long_questioning_bit() -> None:
    contract = build_galgame_text_contract(user_message="我要睡了", scene_id="")

    scored = score_galgame_candidate(
        text="啊？这么快就要睡了？……行吧行吧，尼尼困了就去睡呗。我也不是非要拉着你熬夜的。明天可别又喊起不来啊。",
        contract=contract,
    )

    assert contract.scene_id == "good_night"
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_good_news_contract_rejects_interrogating_celebration() -> None:
    contract = build_galgame_text_contract(user_message="终于跑通了", scene_id="")

    scored = score_galgame_candidate(
        text="哼，看你那样子还挺得意的嘛。不过能跑通确实不容易，是什么东西跑通了呀？让我也听听看。",
        contract=contract,
    )

    assert contract.scene_id == "good_news"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_hungry_contract_rejects_fabricated_feeding_history() -> None:
    contract = build_galgame_text_contract(user_message="饿了", scene_id="")

    scored = score_galgame_candidate(text="唔，尼尼饿了？那怪我中午没把你喂饱？", contract=contract)

    assert contract.scene_id == "hungry"
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_nothing_contract_rejects_hidden_problem_probe() -> None:
    contract = build_galgame_text_contract(user_message="没事", scene_id="")

    scored = score_galgame_candidate(text="没事就好。不过要是真有什么事，别瞒着我啊。", contract=contract)

    assert contract.scene_id == "nothing"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_bad_mood_contract_rejects_tool_companion_and_spatial_comfort() -> None:
    contract = build_galgame_text_contract(user_message="今天心情很差", scene_id="")

    scored = score_galgame_candidate(
        text="心情不好啊...那先别闷着。我陪你说说话，或者你就靠着我待会儿也行。",
        contract=contract,
    )

    assert contract.scene_id == "bad_mood"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_continue_edit_contract_rejects_last_time_loop() -> None:
    contract = build_galgame_text_contract(user_message="还要不要继续改", scene_id="")

    scored = score_galgame_candidate(text="再改最后一次哦，然后别没完没了。", contract=contract)
    overcommit = score_galgame_candidate(
        text="改，当然改。我说过的话当然算数，尼尼的事我可不会随便糊弄过去。答应的事，就要改到满意为止。",
        contract=contract,
    )

    assert contract.scene_id == "advice_continue"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in overcommit.reason_codes


def test_give_up_contract_accepts_later_revisit_word() -> None:
    contract = build_galgame_text_contract(user_message="算了不弄了", scene_id="")

    scored = score_galgame_candidate(text="先记着呗，回头再收拾它。急什么呀。", contract=contract)
    probe = score_galgame_candidate(text="怎么了，遇到什么事啦？看你这样子，我不放心。", contract=contract)

    assert contract.scene_id == "give_up"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" not in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in probe.reason_codes


def test_user_proud_contract_rejects_tell_me_prompt() -> None:
    contract = build_galgame_text_contract(user_message="我厉害吧", scene_id="")

    scored = score_galgame_candidate(text="哦？这么得意啊。还行吧，不过要是真这么厉害，说出来让我听听看？", contract=contract)
    wrong_address = score_galgame_candidate(
        text="哼，这么得意？让我看看怎么回事……哦，是挺行的嘛。不过也不看看是谁的哥哥！勉强给你鼓个掌好了。",
        contract=contract,
    )

    assert contract.scene_id == "user_proud"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert wrong_address.hard_fail
    assert "WRONG_KINSHIP_TERM" in wrong_address.reason_codes


def test_followup_contracts_split_why_and_then() -> None:
    why_contract = build_galgame_text_contract(user_message="为什么", scene_id="")
    then_contract = build_galgame_text_contract(user_message="然后呢", scene_id="")

    why_score = score_galgame_candidate(text="还用英文打招呼，我当然要问问你啦。", contract=why_contract)
    then_score = score_galgame_candidate(text="就这？我还等着你真弄出点什么。", contract=then_contract)
    then_probe = score_galgame_candidate(text="什么叫“然后呢”……我这不是在关心你嘛。修都修好了，跟我说说怎么了？", contract=then_contract)

    assert why_contract.scene_id == "followup_why"
    assert then_contract.scene_id == "followup_then"
    assert "CONTRACT_FORBIDDEN_SURFACE" in why_score.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in then_score.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in then_probe.reason_codes


def test_short_ack_contract_rejects_overbearing_followup() -> None:
    contract = build_galgame_text_contract(user_message="嗯", scene_id="")

    scored = score_galgame_candidate(text="哼，我就知道。那现在弄好了没？没好的话我在这盯着你弄。", contract=contract)

    assert contract.scene_id == "short_ack"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
