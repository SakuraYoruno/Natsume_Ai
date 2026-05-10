from __future__ import annotations

from host_brain.persona.galgame_text_contract import build_galgame_text_contract
from host_brain.persona.galgame_text_scorer import score_galgame_candidate
from host_brain.persona.text_quality_gate import TextQualityGateResult


def test_wait_contract_scores_pressure_lower_than_clean_release() -> None:
    contract = build_galgame_text_contract(user_message="等我一下", scene_id="wait")

    clean = score_galgame_candidate(text="嗯，先去。别急。", contract=contract)
    pressured = score_galgame_candidate(text="嗯，我在这儿等你，别太久。", contract=contract)

    assert clean.score > pressured.score
    assert "CONTRACT_FORBIDDEN_SURFACE" in pressured.reason_codes


def test_identity_contract_requires_full_name() -> None:
    contract = build_galgame_text_contract(user_message="你是", scene_id="identity")

    good = score_galgame_candidate(text="新海天啊。尼尼连我都要重新确认？", contract=contract)
    vague = score_galgame_candidate(text="是天啊，尼尼又忘了？", contract=contract)

    assert good.score > vague.score
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in vague.reason_codes


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

    assert scored.hard_fail
    assert scored.score <= 35
    assert "WRONG_KINSHIP_TERM" in scored.reason_codes


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

    assert contract.scene_id == "relationship_boundary"
    assert "TOO_MANY_QUESTIONS" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


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


def test_presence_contract_rejects_help_frame() -> None:
    contract = build_galgame_text_contract(user_message="在吗", scene_id="")

    scored = score_galgame_candidate(text="在呀。尼尼找我当然在了。", contract=contract)

    assert contract.scene_id == "presence"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_good_morning_contract_rejects_early_wakeup_guess() -> None:
    contract = build_galgame_text_contract(user_message="早", scene_id="")

    scored = score_galgame_candidate(text="早啊尼尼。你起这么早，我还以为今天太阳打西边出来了呢。", contract=contract)

    assert contract.scene_id == "daily_good_morning"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


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

    assert contract.scene_id == "play_invite"
    assert "TOO_SHORT" in scored.reason_codes


def test_bored_contract_rejects_tool_chat_framing() -> None:
    contract = build_galgame_text_contract(user_message="好无聊", scene_id="")

    scored = score_galgame_candidate(text="本天才在这儿呢。陪你说说话就不无聊了吧。", contract=contract)

    assert contract.scene_id == "bored"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_quiet_contract_rejects_too_thin_line() -> None:
    contract = build_galgame_text_contract(user_message="别吵", scene_id="")

    scored = score_galgame_candidate(text="哼，我又没真的闹你", contract=contract)

    assert contract.scene_id == "quiet"
    assert "TOO_SHORT" in scored.reason_codes


def test_talkative_contract_requires_talk_surface() -> None:
    contract = build_galgame_text_contract(user_message="你怎么这么能说", scene_id="")

    scored = score_galgame_candidate(text="那当然啦。尼尼难得开一回口，我不得多接几句热闹热闹？", contract=contract)

    assert contract.scene_id == "talkative"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes


def test_praise_contract_rejects_help_guess() -> None:
    contract = build_galgame_text_contract(user_message="你今天挺可爱的", scene_id="praise")

    scored = score_galgame_candidate(text="哼，尼尼今天嘴这么甜，是想让我帮你做什么事吧？", contract=contract)

    assert contract.scene_id == "praise"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_small_failure_contract_rejects_blaming_comfort() -> None:
    contract = build_galgame_text_contract(user_message="刚刚弄砸了", scene_id="")

    scored = score_galgame_candidate(
        text="弄砸了？没事的啊，尼尼又不是没有搞砸过。先过来坐一下。",
        contract=contract,
    )

    assert contract.scene_id == "small_failure"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_late_night_contract_requires_sleep_nudge() -> None:
    contract = build_galgame_text_contract(user_message="还没睡", scene_id="")

    scored = score_galgame_candidate(text="你问得还挺及时嘛。不过我可没等你啊。", contract=contract)

    assert contract.scene_id == "late_night"
    assert "CONTRACT_MISSING_REQUIRED_MOVE" in scored.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_continue_edit_contract_rejects_last_time_loop() -> None:
    contract = build_galgame_text_contract(user_message="还要不要继续改", scene_id="")

    scored = score_galgame_candidate(text="再改最后一次哦，然后别没完没了。", contract=contract)

    assert contract.scene_id == "advice_continue"
    assert "CONTRACT_FORBIDDEN_SURFACE" in scored.reason_codes


def test_followup_contracts_split_why_and_then() -> None:
    why_contract = build_galgame_text_contract(user_message="为什么", scene_id="")
    then_contract = build_galgame_text_contract(user_message="然后呢", scene_id="")

    why_score = score_galgame_candidate(text="还用英文打招呼，我当然要问问你啦。", contract=why_contract)
    then_score = score_galgame_candidate(text="就这？我还等着你真弄出点什么。", contract=then_contract)

    assert why_contract.scene_id == "followup_why"
    assert then_contract.scene_id == "followup_then"
    assert "CONTRACT_FORBIDDEN_SURFACE" in why_score.reason_codes
    assert "CONTRACT_FORBIDDEN_SURFACE" in then_score.reason_codes
