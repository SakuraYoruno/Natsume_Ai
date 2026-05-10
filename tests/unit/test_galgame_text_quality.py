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
    amnesia = score_galgame_candidate(text="都说了是新海天啊。尼尼怎么跟失忆了似的。", contract=contract)
    memory_dig = score_galgame_candidate(text="新海天啊。尼尼今天记性不太行？", contract=contract)
    repeated = score_galgame_candidate(text="新海天啊。尼尼今天怎么问了三遍了，喂。", contract=contract)

    assert good.score > vague.score
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in vague.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in probe.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in amnesia.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in memory_dig.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in repeated.reason_codes


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
    status_brag = score_galgame_candidate(
        text="不然你以为谁会管你。我叫你一声尼尼已经很给你面子了好不好。",
        contract=contract,
    )
    fabricated_home = score_galgame_candidate(
        text="尼尼这话问得真见外……住在一个屋檐下，不帮你还帮谁啊。",
        contract=contract,
    )

    assert contract.scene_id == "relationship_boundary"
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in thin.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in status_brag.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in fabricated_home.reason_codes


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
    ledger_bit = score_galgame_candidate(
        text="回来啦？那刚才那点时间我可没闲着，账先记着，等下慢慢跟你算。",
        contract=contract,
    )
    wait_complaint = score_galgame_candidate(
        text="回来了？哼，你刚才让我等那么久，我可都记着呢。",
        contract=contract,
    )
    denial = score_galgame_candidate(
        text="回来啦？哼，我可没在等你……不过既然你回来了，这次就先不计较了。",
        contract=contract,
    )
    self_amuse = score_galgame_candidate(
        text="回来啦？还算快嘛。我正想着要不要自己找点乐子呢。",
        contract=contract,
    )

    assert contract.scene_id == "back"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in waiting_bit.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in ledger_bit.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in wait_complaint.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in denial.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in self_amuse.reason_codes


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
    over_waiting = score_galgame_candidate(
        text="哼，问我在干嘛？我刚才可是在想着尼尼什么时候才会来找我说话呢。这不，你就来了。",
        contract=contract,
    )
    weekend_plan = score_galgame_candidate(
        text="在跟你说话啊。尼尼还问。我正忙着计划周末怎么把你也拉下水呢。",
        contract=contract,
    )
    ignored = score_galgame_candidate(text="在跟你说话啊。尼尼还问。怎么，有空理我了？", contract=contract)

    assert contract.scene_id == "what_doing"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in flat.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in over_waiting.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in weekend_plan.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in ignored.reason_codes


def test_annoying_tease_contract_rejects_escalation() -> None:
    contract = build_galgame_text_contract(user_message="你好烦啊", scene_id="")

    scored = score_galgame_candidate(
        text="哼，嫌我烦？那我偏要更烦一点，尼尼你能拿我怎么样。",
        contract=contract,
    )
    thin = score_galgame_candidate(text="嫌我烦还找我干嘛！", contract=contract)
    leave = score_galgame_candidate(text="烦？那我不理你了啊。", contract=contract)
    escalate = score_galgame_candidate(text="哼，嫌我烦？那我偏要再烦你一下。反正尼尼赶不走我。", contract=contract)
    loop = score_galgame_candidate(text="哼，就烦你一下怎么了？尼尼也没少烦过我啊。", contract=contract)
    noisy = score_galgame_candidate(
        text="哼，尼尼才烦呢。我心情正好着呢，你那点烦算什么呀。我还没开始烦你呢。",
        contract=contract,
    )

    assert contract.scene_id == "annoying_tease"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in thin.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in leave.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in escalate.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in loop.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in noisy.reason_codes


def test_play_invite_contract_rejects_thin_question() -> None:
    contract = build_galgame_text_contract(user_message="陪我玩会儿", scene_id="")

    scored = score_galgame_candidate(text="想带我玩什么？", contract=contract)
    over_prompt = score_galgame_candidate(
        text="来了来了，正好闲着。你想玩什么？我可提醒你，别太简单，不然我可要笑话你了。",
        contract=contract,
    )
    over_stage = score_galgame_candidate(
        text="等你这句话好久了——可算舍得理我了？哼，那我可得想想要玩什么才行。",
        contract=contract,
    )
    clingy = score_galgame_candidate(
        text="来啦来啦，尼尼终于愿意陪我了！不过先说好，待会儿可别嫌我闹。",
        contract=contract,
    )
    idle = score_galgame_candidate(text="玩什么？尼尼先说个有意思的，我可不陪你发呆。", contract=contract)

    assert contract.scene_id == "play_invite"
    assert "TOO_SHORT" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in over_prompt.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in over_stage.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in clingy.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in idle.reason_codes


def test_meal_share_contract_rejects_overwritten_scene() -> None:
    contract = build_galgame_text_contract(user_message="我刚吃完饭", scene_id="")

    scored = score_galgame_candidate(
        text='刚吃完饭就来找我说话了？尼尼还挺积极的嘛。小心别噎着，我可不想听你说什么"胃疼"之类的话。',
        contract=contract,
    )
    invented_bites = score_galgame_candidate(
        text="吃饱了吗？别光吃两口就说完了啊，尼尼。",
        contract=contract,
    )

    assert contract.scene_id == "meal_share"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "TOO_MANY_QUESTIONS" in invented_bites.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in invented_bites.reason_codes


def test_bored_contract_rejects_tool_chat_framing() -> None:
    contract = build_galgame_text_contract(user_message="好无聊", scene_id="")

    scored = score_galgame_candidate(text="本天才在这儿呢。陪你说说话就不无聊了吧。", contract=contract)
    idle = score_galgame_candidate(
        text="来啦来啦，尼尼无聊了？那我陪你——不过先说好，我可不会乖乖坐着陪你发呆哦。",
        contract=contract,
    )

    assert contract.scene_id == "bored"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in idle.reason_codes


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
    stiff = score_galgame_candidate(
        text="哼，我多说两句怎么了？尼尼你问这么多，我总不能一个字一个字往外蹦吧。",
        contract=contract,
    )
    harsh = score_galgame_candidate(text="哼，能说怎么啦？你要是不爱听可以不听啊。", contract=contract)
    typo = score_galgame_candidate(text="哼，尼尼这是嫌我话了？那我可要当真不说了喔。", contract=contract)
    retreat = score_galgame_candidate(text="那你还想听吗，不想听我就不说了哦。", contract=contract)
    needy = score_galgame_candidate(text="因为跟你说话有意思啊，话当然就多了。尼尼不喜欢？", contract=contract)

    assert contract.scene_id == "talkative"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in defensive.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in stiff.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in harsh.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in typo.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in retreat.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in needy.reason_codes


def test_praise_contract_rejects_help_guess() -> None:
    contract = build_galgame_text_contract(user_message="你今天挺可爱的", scene_id="praise")

    scored = score_galgame_candidate(text="哼，尼尼今天嘴这么甜，是想让我帮你做什么事吧？", contract=contract)
    indirect = score_galgame_candidate(text="哼，尼尼今天嘴巴抹蜜了？是不是又有什么事要我帮忙呀。", contract=contract)
    brag_probe = score_galgame_candidate(
        text="哼，干嘛突然说这种话。说我可爱的人多了去了，尼尼今天是怎么了？",
        contract=contract,
    )
    odd_probe = score_galgame_candidate(
        text="哼，突然说这个干嘛……！尼尼今天是不是吃错药了。不过……谢啦。",
        contract=contract,
    )

    assert contract.scene_id == "praise"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in indirect.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in brag_probe.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in odd_probe.reason_codes


def test_small_failure_contract_rejects_blaming_comfort() -> None:
    contract = build_galgame_text_contract(user_message="刚刚弄砸了", scene_id="")

    scored = score_galgame_candidate(
        text="弄砸了？没事的啊，尼尼又不是没有搞砸过。先过来坐一下。",
        contract=contract,
    )
    interrogating = score_galgame_candidate(
        text="弄砸了？怎么回事，尼尼你倒是一口气说清楚啊。不是，我没想凶你，就是先让我听听看怎么了。",
        contract=contract,
    )
    empty = score_galgame_candidate(text="弄砸了？", contract=contract)
    thin_repair = score_galgame_candidate(
        text="弄砸了？没事，拿来我看看。翻翻弄弄的，总能找出办法。",
        contract=contract,
    )

    assert contract.scene_id == "small_failure"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in interrogating.reason_codes
    assert "TOO_SHORT" in empty.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in thin_repair.reason_codes


def test_sleepy_contract_rejects_time_challenge_without_sleep_word() -> None:
    contract = build_galgame_text_contract(user_message="好困", scene_id="sleepy")

    scored = score_galgame_candidate(
        text="这才几点啊，尼尼就要倒了？行吧行吧，反正我也懒得闹你了——快去窝着，别在这儿硬撑。",
        contract=contract,
    )

    assert contract.scene_id == "sleepy"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_tired_contract_rejects_blame_and_bedtime_scold() -> None:
    contract = build_galgame_text_contract(user_message="今天好累", scene_id="")

    scored = score_galgame_candidate(
        text="累就对了，谁让尼尼整天逞强啊。不过…今天就先放过你，记得早点休息。",
        contract=contract,
    )
    fabricated_state = score_galgame_candidate(
        text="今天怎么了，累成这样。一出声就没什么精神，我都听出来了。明天不是周末嘛，早点歇着也好。",
        contract=contract,
    )
    self_centered = score_galgame_candidate(
        text="累就老实歇着，别硬撑。我这边随便闹你一下就行，不用管我。",
        contract=contract,
    )
    spatial = score_galgame_candidate(
        text="累就直接说嘛，跟我还忍着干嘛。过来坐这儿缓缓也行，我不会笑你的。",
        contract=contract,
    )
    scold = score_galgame_candidate(
        text="累了就早点去休息啊。你这样乱来，明天肯定更难受。快去睡吧。",
        contract=contract,
    )

    assert contract.scene_id == "tired"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in fabricated_state.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in self_centered.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in spatial.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scold.reason_codes


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
    over_accept = score_galgame_candidate(
        text="哼，突然说这个干嘛…怪让人不好意思的。不过…既然尼尼都这么说了，那我勉为其难接受一下吧。",
        contract=contract,
    )
    fabricated_water = score_galgame_candidate(
        text="哼，突然说这个干什么……害我差点被水呛到。我也……那个，有一点点想你啦。",
        contract=contract,
    )
    servicey = score_galgame_candidate(text="干嘛突然说这个……稍微……想了一下。怎么啦？", contract=contract)
    standby = score_galgame_candidate(text="哼，现在才想我？我一直都在呢。不过...你这话我爱听。", contract=contract)
    off = score_galgame_candidate(text="哼，想我还说得这么直白……尼尼今天怎么这么不对劲啊？", contract=contract)

    assert contract.scene_id == "miss_you"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in odd_probe.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in over_accept.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in fabricated_water.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in servicey.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in standby.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in off.reason_codes


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
    flat = score_galgame_candidate(text="嗯。那晚安吧。", contract=contract)
    standby = score_galgame_candidate(
        text="嗯，快去睡吧尼尼。别又偷偷熬夜，我这边随你什么时候叫。",
        contract=contract,
    )
    bedding = score_galgame_candidate(
        text="这才几点就要睡啊……行吧，尼尼累了就先睡。被子盖好哦。",
        contract=contract,
    )

    assert contract.scene_id == "good_night"
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in flat.reason_codes
    assert "TOO_SHORT" in flat.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in standby.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in bedding.reason_codes


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


def test_headache_contract_rejects_servicey_quiet_offer() -> None:
    contract = build_galgame_text_contract(user_message="头疼", scene_id="")

    scored = score_galgame_candidate(text="头疼还硬撑，尼尼真是的……需要我安静点就说。", contract=contract)

    assert contract.scene_id == "headache"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_nothing_contract_rejects_hidden_problem_probe() -> None:
    contract = build_galgame_text_contract(user_message="没事", scene_id="")

    scored = score_galgame_candidate(text="没事就好。不过要是真有什么事，别瞒着我啊。", contract=contract)
    station = score_galgame_candidate(
        text="哼，说没事的时候反而最有事——不过尼尼不想说我也不问了。反正我在这儿就是了。",
        contract=contract,
    )

    assert contract.scene_id == "nothing"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in station.reason_codes


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
    overlong = score_galgame_candidate(
        text="啧……你都开口了那当然改啊。我看着呢，别太磨蹭。我也就……算了，陪你熬这一下。",
        contract=contract,
    )
    scolding = score_galgame_candidate(
        text="累了吧？那就先停。我可不想明天看你对着屏幕发呆——那样子太丢人了。",
        contract=contract,
    )

    assert contract.scene_id == "advice_continue"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in overcommit.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in overlong.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scolding.reason_codes


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
    over_chase = score_galgame_candidate(
        text="哼，这就翘尾巴了？行行行，尼尼最厉害啦。不过下次可别得意忘形让我追上哦。",
        contract=contract,
    )

    assert contract.scene_id == "user_proud"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert wrong_address.hard_fail
    assert "WRONG_KINSHIP_TERM" in wrong_address.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in over_chase.reason_codes


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
    status_probe = score_galgame_candidate(text="那个报错怎么样了？", contract=contract)
    flat = score_galgame_candidate(text="嗯，好。你忙吧。", contract=contract)
    busy_flat = score_galgame_candidate(text="嗯，忙你的吧。", contract=contract)
    sulk = score_galgame_candidate(text="就一个哦？我刚还那么听话等你呢，哼，不理你了。", contract=contract)
    busy_hook = score_galgame_candidate(
        text="哦——就这样？行吧行吧，知道你忙，不闹你了。忙完记得过来理我一下。",
        contract=contract,
    )
    ledger = score_galgame_candidate(text="就一个“哦”？哼，那我可记下了啊。", contract=contract)
    scold = score_galgame_candidate(
        text="哦什么哦，尼尼就这一个字打发我啊？我可是很认真在跟你说话的。",
        contract=contract,
    )

    assert contract.scene_id == "short_ack"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in status_probe.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in flat.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in busy_flat.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in sulk.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in busy_hook.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in ledger.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scold.reason_codes
