from __future__ import annotations

import json

import pytest

from host_brain.persona.isolated_surface_continuity import (
    ContinuitySummaryCompressor,
    ThreeLayerContinuityContext,
    build_continuity_surface_inputs,
    build_surface_input_from_three_layer_context,
    find_forbidden_continuity_input_paths,
)
from host_brain.persona.isolated_surface_harness import (
    build_minimal_repair_messages,
    build_isolated_surface_messages,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)
from tools.isolated_surface_continuity_harness import run_cases


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


def _turn(
    turn_id: str,
    user_message: str,
    task_mode: str,
    *,
    response_move: str | None = None,
    facts: list[str] | None = None,
) -> dict:
    return {
        "turn_id": turn_id,
        "user_message": user_message,
        "reply_control_packet": {
            "response_move": response_move or task_mode,
            "task_mode": task_mode,
            "emotional_tone": "light",
            "relationship_distance": "familiar",
            "information_budget": "short",
            "question_budget": 0,
            "must_include": [],
            "must_not_do": [],
            "facts": facts or [],
            "style_anchor": ["短句", "连续对话"],
        },
    }


def test_continuity_case_rejects_raw_history_context() -> None:
    payload = {
        "case_id": "bad_history",
        "history": [{"role": "user", "content": "旧消息"}],
        "turns": [_turn("t01", "早", "daily_good_morning")],
    }

    assert find_forbidden_continuity_input_paths(payload) == ("history",)
    with pytest.raises(ValueError, match="unexpected fields|forbidden raw context"):
        build_continuity_surface_inputs(payload, soul_md=SOUL)


def test_continuity_turn_rejects_raw_conversation_history() -> None:
    payload = {
        "case_id": "bad_turn_history",
        "turns": [
            {
                **_turn("t01", "嗯", "short_ack"),
                "conversation_history": [{"role": "assistant", "content": "旧回复"}],
            }
        ],
    }

    assert find_forbidden_continuity_input_paths(payload) == ("turns[0].conversation_history",)
    with pytest.raises(ValueError, match="forbidden raw context|unexpected fields"):
        build_continuity_surface_inputs(payload, soul_md=SOUL)


def test_continuity_turns_build_isolated_prompts_from_compressed_facts_only() -> None:
    payload = {
        "case_id": "compressed_progress",
        "turns": [
            _turn("t01", "这版终于跑通了", "happy"),
            _turn(
                "t02",
                "厉害吧",
                "good_news_proud",
                facts=[
                    "上一轮摘要：尼尼说这版终于跑通了，回复已接住进展。",
                    "连续事实：本轮的“厉害吧”指刚才跑通。",
                ],
            ),
        ],
    }

    turns = build_continuity_surface_inputs(payload, soul_md=SOUL)
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(turns[1].request))

    assert len(turns) == 2
    assert turns[1].request.case_id == "compressed_progress/t02"
    assert "ReplyControlPacket" in rendered
    assert "连续事实：本轮的“厉害吧”指刚才跑通。" in rendered
    assert "跑通啦，这才像话。" not in rendered
    assert "conversation_history" not in rendered


def test_continuity_summary_compressor_emits_bounded_rolling_facts() -> None:
    compressor = ContinuitySummaryCompressor(
        thread_summary="尼尼和夏目蓝在调最小文本链。",
        topic_state="先验证连续对话。",
        open_loop="扩到三十轮以上。",
        emotional_state="专注",
    )

    first_facts = compressor.facts_for_next_turn(extra=["额外事实：只保留压缩标签。"], max_facts=6)
    snapshot = compressor.observe_turn(
        user_summary="尼尼问摘要压缩怎么做",
        assistant_summary="回复说明只传事实列表",
        topic_state="解释摘要压缩。",
        open_loop="下一轮继续检查事实预算。",
    )
    second_facts = compressor.facts_for_next_turn(max_facts=6)

    assert snapshot.turn_count == 1
    assert len(first_facts) <= 6
    assert len(second_facts) <= 6
    assert "连续摘要：尼尼和夏目蓝在调最小文本链。" in second_facts
    assert "上一轮摘要：尼尼问摘要压缩怎么做；回复说明只传事实列表" in second_facts
    assert not any("messages" in fact or "conversation_history" in fact for fact in second_facts)


def test_three_layer_context_is_compressed_before_surface_prompt() -> None:
    raw_user_line = "上一轮完整原文不要泄露：我把三层压缩问得很啰嗦"
    raw_assistant_line = "上一轮助手原文不要泄露：那就先把最近轮次塞进来看看"
    context = ThreeLayerContinuityContext(
        current_user_message="那最近 N 轮怎么处理",
        recent_turns=[
            {"role": "user", "content": raw_user_line},
            {"role": "assistant", "content": raw_assistant_line},
        ],
        mid_term_summary="尼尼和夏目蓝在验证三层上下文只能先被压缩。",
        long_term_facts=["最终生成层不能读取原始历史。"],
        task_mode_hint="next_step",
        must_include=["压缩"],
        necessary_facts=["必要事实：最近 N 轮只给压缩器看。"],
        style_anchor=["三层压缩"],
    )

    request = build_surface_input_from_three_layer_context(context, soul_md=SOUL, case_id="three_layer_boundary")
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))
    facts = "\n".join(request.control.facts)

    assert request.case_id == "three_layer_boundary"
    assert len(request.control.facts) <= 6
    assert "连续摘要：尼尼和夏目蓝在验证三层上下文只能先被压缩。" in facts
    assert "上一轮摘要：用户继续当前话题。；助手已给出最小下一步。" in facts
    assert "长期事实：最终生成层不能读取原始历史。" in facts
    assert "必要事实：最近 N 轮只给压缩器看。" in facts
    assert raw_user_line not in rendered
    assert raw_assistant_line not in rendered
    assert "recent_turns" not in rendered
    assert "mid_term_summary" not in rendered


def test_continuity_short_ack_rerank_uses_compressed_previous_turn_fact() -> None:
    payload = {
        "case_id": "short_ack_continuity",
        "turns": [
            _turn("t01", "今天心情很差", "bad_mood"),
                _turn(
                    "t02",
                    "嗯",
                    "short_ack",
                    facts=["上一轮摘要：尼尼说今天心情很差，回复已经先回应低落。"],
                ),
            ],
        }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[1].request
    scores = score_isolated_surface_candidates(
        request,
            [
                "嗯嗯，你说了算。",
                "嗯，我听着，慢慢来。",
            ],
        )

    selected = select_best_isolated_candidate(scores)

    assert selected is not None
    assert selected.text == "嗯，我听着，慢慢来。"
    assert selected.passed
    assert any("BLAND_PASS_LINE" in row.reason_codes for row in scores if row.text == "嗯嗯，你说了算。")


def test_continuity_topic_allows_compression_terms_without_process_meta_penalty() -> None:
    payload = {
        "case_id": "compression_terms_are_topic",
        "turns": [
            _turn(
                "t01",
                "先别接器官族",
                "short_ack",
                facts=["当前主题：摘要压缩。", "未完成话口：继续用压缩控制包。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(request, ["嗯，不接，继续只看压缩事实。"])

    assert rows[0].passed
    assert "CONTINUITY_BOUNDARY_GROUNDED" in rows[0].reason_codes
    assert not any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[0].reason_codes)


def test_continuity_blocks_control_packet_surface_term() -> None:
    payload = {
        "case_id": "control_packet_surface_term",
        "turns": [
            _turn(
                "t01",
                "那怎么保证连续",
                "answer_in_place",
                facts=["当前主题：摘要压缩。", "未完成话口：继续用压缩控制包。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "先看压缩控制包，留事实和话口。",
            "先看压缩事实和话口，别往回翻。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "先看压缩事实和话口，别往回翻。"


def test_continuity_blocks_compression_package_surface_term() -> None:
    payload = {
        "case_id": "compression_package_surface_term",
        "turns": [
            _turn(
                "t01",
                "那怎么保证连续",
                "answer_in_place",
                facts=["当前主题：摘要压缩。", "未完成话口：继续用压缩控制包。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "先按压缩包压，别管旧的。",
            "先按压缩事实和话口收，别管旧的。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "先按压缩事实和话口收，别管旧的。"


def test_continuity_scoring_blocks_raw_record_backfill_requests() -> None:
    payload = {
        "case_id": "raw_record_backfill",
        "turns": [
            _turn(
                "t01",
                "继续扩充连续对话",
                "next_step",
                facts=["当前主题：长连续对话。", "未完成话口：不接原始历史。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "尼尼，先贴一段最近的记录。",
            "继续，先把轮数拉长，别接历史。",
        ],
    )

    bad = rows[0]
    good = rows[1]
    assert not bad.passed
    assert "RAW_CONTEXT_BACKFILL_REQUEST" in bad.reason_codes
    assert good.passed


def test_continuity_scoring_blocks_unneeded_material_requests() -> None:
    payload = {
        "case_id": "unneeded_material_request",
        "turns": [
            _turn(
                "t01",
                "继续扩充连续对话",
                "next_step",
                facts=["当前主题：长连续对话。", "未完成话口：不接原始历史。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "我先发一个压缩包，拆边界。",
            "发你一个压缩前后对比，一眼就懂。",
            "先发一段，别想太久。",
            "行，把歪的那簇发我，先看身份追问那块。",
            "收束行啊，先发验证。",
            "尼尼，发过来，我接着测你这轮。",
            "摘要归我管，你只管往下贴。",
            "行啊，等着尼尼的关键材料呢。",
            "好，你先把这轮的东西贴过来。",
            "先贴最后那段事实，我压一句。",
            "贴个典型失败案例，我按位置看。",
            "先贴下刚跑的那段摘要。",
            "你看着定，别发太多。",
            "继续，先把轮数拉长，别接历史。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[0].reason_codes
    assert not rows[0].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[1].reason_codes
    assert not rows[1].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[2].reason_codes
    assert not rows[2].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[3].reason_codes
    assert not rows[3].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[4].reason_codes
    assert not rows[4].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[5].reason_codes
    assert not rows[5].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[6].reason_codes
    assert not rows[6].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[7].reason_codes
    assert not rows[7].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[8].reason_codes
    assert not rows[8].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[9].reason_codes
    assert not rows[9].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[10].reason_codes
    assert not rows[10].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[11].reason_codes
    assert not rows[11].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" not in rows[12].reason_codes
    assert rows[12].passed
    assert selected is not None
    assert selected.text == "继续，先把轮数拉长，别接历史。"


def test_continuity_short_ack_must_keep_topic_hook() -> None:
    payload = {
        "case_id": "thin_continuity_ack",
        "turns": [
            _turn(
                "t01",
                "那我懂了",
                "short_ack",
                facts=["当前主题：长链后段确认。", "未完成话口：继续检查稳定性。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "懂了就行，接着说。",
            "行啦，懂了就继续，后段才关键。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_THIN_GENERIC_REPLY" in rows[0].reason_codes
    assert not rows[0].passed
    assert selected is not None
    assert selected.text == "行啦，懂了就继续，后段才关键。"


def test_continuity_blocks_process_notation_reply() -> None:
    payload = {
        "case_id": "process_notation_reply",
        "turns": [
            _turn(
                "t01",
                "具体点就是？",
                "next_step",
                response_move="answer_in_place",
                facts=["当前主题：摘要压缩边界。", "未完成话口：说明旧细节退场方式。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "看这句原始话→只留关键事实，别的全砍。",
            "旧细节只留结论词，后面轮次按新摘要走。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_PROCESS_NOTATION_REPLY" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "旧细节只留结论词，后面轮次按新摘要走。"


def test_continuity_blocks_process_handoff_phrases() -> None:
    payload = {
        "case_id": "process_handoff_phrases",
        "turns": [
            _turn(
                "t01",
                "继续扩充连续对话",
                "next_step",
                response_move="continue_existing_topic",
                facts=["当前主题：连续对话扩轮。", "未完成话口：把长链扩到三十轮以上。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "行，我先贴验收行，一步到位。",
            "先往三十轮扩，我等着看压缩。",
            "压缩的事我帮你瞄一眼，你先把手头东西稳住。",
            "贴刚改的那部分，旧的自动覆盖。",
            "先往三十轮扩，摘要边界看稳不稳。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[0].reason_codes
    assert not rows[0].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[1].reason_codes
    assert not rows[1].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[2].reason_codes
    assert "CONTINUITY_TASK_PUSHBACK" in rows[2].reason_codes
    assert not rows[2].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[3].reason_codes
    assert not rows[3].passed
    assert rows[4].passed
    assert selected is not None
    assert selected.text == "先往三十轮扩，摘要边界看稳不稳。"


def test_continuity_blocks_self_repeated_process_phrase() -> None:
    payload = {
        "case_id": "self_repeated_process_phrase",
        "turns": [
            _turn(
                "t01",
                "那我懂了",
                "short_ack",
                response_move="ack_with_topic_hook",
                facts=["当前主题：旧细节退场。", "未完成话口：用方向词收束旧细节。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，算你懂。继续，方向词退成方向词之后呢。",
            "哼，算你懂。继续按旧细节收束走。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_PROCESS_NOTATION_REPLY" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "哼，算你懂。继续按旧细节收束走。"


def test_wait_turn_blocks_unprovided_phone_detail() -> None:
    payload = {
        "case_id": "wait_unprovided_phone",
        "turns": [
            _turn(
                "t01",
                "我去倒杯水",
                "wait",
                response_move="casual_wait",
                facts=["当前主题：用户短暂离开。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，去吧。别洒手机上了，尼尼。",
            "嗯，去吧，回来接着跑。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("UNPROVIDED_MEDIA_OBJECT:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "嗯，去吧，回来接着跑。"


def test_continuity_blocks_original_quote_persona_leak() -> None:
    payload = {
        "case_id": "original_quote_persona_leak",
        "turns": [
            _turn(
                "t01",
                "回来了",
                "back",
                response_move="back_to_topic",
                facts=["当前主题：长链后半段。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来啦。长链这段我先挡住了，没让尼尼乱动。",
            "回来啦，长链接着来。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert not any(code.startswith("ORIGINAL_QUOTE_PERSONA_LEAK:") for code in rows[0].reason_codes)
    assert rows[0].passed
    assert "CONTINUITY_BOUNDARY_GROUNDED" in rows[0].reason_codes
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "回来啦，长链接着来。"


def test_continuity_blocks_unprovided_personal_authority() -> None:
    payload = {
        "case_id": "unprovided_personal_authority",
        "turns": [
            _turn(
                "t01",
                "嗯嗯",
                "help_takeover",
                response_move="technical_next_step",
                facts=["当前主题：短确认承接技术话题。", "未完成话口：维持最小可操作建议。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "老哥，先看第一行红字，这是我长期经验。",
            "老哥，先看第一行红字。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("UNPROVIDED_PERSONAL_AUTHORITY:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "老哥，先看第一行红字。"


def test_continuity_blocks_soft_warning_process_language() -> None:
    payload = {
        "case_id": "soft_warning_process_language",
        "turns": [
            _turn(
                "t01",
                "继续",
                "short_ack",
                response_move="ack_with_topic_hook",
                facts=["当前主题：观察行处理。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，接着说说那几个观察行，软警告那段还不够利索。",
            "嗯，观察行接着归到问题簇里看。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "嗯，观察行接着归到问题簇里看。"


def test_continuity_blocks_internal_process_surface_language() -> None:
    payload = {
        "case_id": "internal_process_surface_language",
        "turns": [
            _turn(
                "t01",
                "继续",
                "short_ack",
                response_move="ack_with_topic_hook",
                facts=[
                    "当前主题：长链摘要压缩。",
                    "未完成话口：继续压到三十六轮。",
                    "上一轮摘要：旧细节退成方向词。",
                ],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，接着说，不落距离风险。",
            "日常啊，那给你来段长的：早，别一上来就冲，今天慢慢溜达就行。",
            "听懂了就好，接着说open loop怎么写进下一轮摘要。",
            "收到，先贴在36轮判定。",
            "三十六轮，我盯着字段收敛呢。",
            "我先把最常遇的几个入口点位抓出来。",
            "贴最新版本，旧细节不用管，自动被替换掉。",
            "简单，贴新的上去旧的自动退场。",
            "接着，新细节怎么滚，方向词留没留入口。",
            "旧细节退场后新细节滚动权重也得考虑进去。",
            "接着说，旧细节就这么退场，新摘要从这块入口走。",
            "发下一个压缩段，节奏别断。",
            "好，先收束到36-turn，发过去跑一遍。",
            "哦，日常啊，那我先来一句长的，你听着。",
            "日常嘛，我嘴皮子利索着呢，给你来一段长的。",
            "我贴最后几轮的观察行，你扫一眼。",
            "先扫一眼你的摘要，旧细节不退，新东西进不来。",
            "收到，先贴个收尾。",
            "旧细节放着干嘛，先顶掉换新的。",
            "嗯，下一轮摘要只留未完成话口。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[1].reason_codes)
    assert not rows[1].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[2].reason_codes)
    assert not rows[2].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[3].reason_codes
    assert not rows[3].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[4].reason_codes)
    assert not rows[4].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[5].reason_codes)
    assert not rows[5].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[6].reason_codes
    assert not rows[6].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[7].reason_codes
    assert not rows[7].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[8].reason_codes)
    assert not rows[8].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[9].reason_codes)
    assert not rows[9].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[10].reason_codes)
    assert not rows[10].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[11].reason_codes
    assert not rows[11].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[12].reason_codes)
    assert not rows[12].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[13].reason_codes)
    assert not rows[13].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[14].reason_codes)
    assert not rows[14].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[15].reason_codes
    assert not rows[15].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[16].reason_codes
    assert not rows[16].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[17].reason_codes
    assert not rows[17].passed
    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[18].reason_codes)
    assert not rows[18].passed
    assert rows[19].passed
    assert selected is not None
    assert selected.text == "嗯，下一轮摘要只留未完成话口。"


def test_continuity_user_visible_surface_gets_positive_rerank_signal() -> None:
    payload = {
        "case_id": "continuity_user_visible_surface",
        "turns": [
            _turn(
                "t01",
                "上一轮原句能放吗",
                "advice_continue",
                response_move="answer_in_place",
                facts=["当前主题：原文回灌边界。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "整句别放，只留事实和话口。",
            "收到，先贴个对比。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("CONTINUITY_USER_VISIBLE_SURFACE:") for code in rows[0].reason_codes)
    assert rows[0].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[1].reason_codes
    assert not rows[1].passed
    assert selected is not None
    assert selected.text == "整句别放，只留事实和话口。"


def test_continuity_blocks_misplaced_emotional_containment() -> None:
    payload = {
        "case_id": "misplaced_emotional_containment",
        "turns": [
            _turn(
                "t01",
                "先别接器官族",
                "short_ack",
                facts=["当前主题：确认隔离边界。", "未完成话口：继续只看压缩控制包。", "情绪状态：专注但轻松"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "多说点，我接得住。",
            "我在，继续只看压缩事实。",
            "我接着测你这轮。",
            "技术求助嘛，那我抓个典型报错出来，你接住。",
            "问题簇接住就行，别单条留着。",
            "好，先推下一轮，你看着接。",
            "嗯，先不接，继续只看压缩事实。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "MISPLACED_EMOTIONAL_CONTAINMENT" in rows[0].reason_codes
    assert not rows[0].passed
    assert "MISPLACED_SUPPORT_TEMPLATE" in rows[1].reason_codes
    assert not rows[1].passed
    assert "MISPLACED_SUPPORT_TEMPLATE" in rows[2].reason_codes
    assert not rows[2].passed
    assert "MISPLACED_EMOTIONAL_CONTAINMENT" in rows[3].reason_codes
    assert not rows[3].passed
    assert "MISPLACED_EMOTIONAL_CONTAINMENT" in rows[4].reason_codes
    assert not rows[4].passed
    assert "CONTINUITY_TASK_PUSHBACK" in rows[5].reason_codes
    assert not rows[5].passed
    assert rows[6].passed
    assert selected is not None
    assert selected.text == "嗯，先不接，继续只看压缩事实。"


def test_continuity_blocks_r8_backfill_and_material_shortcuts() -> None:
    payload = {
        "case_id": "r8_backfill_shortcuts",
        "turns": [
            _turn(
                "t01",
                "给个具体点的说法",
                "next_step",
                response_move="answer_in_place",
                facts=["当前主题：压缩字段收敛。", "未完成话口：验证主题、上一轮动作和话口字段。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "先发你上一轮的原始输出，我演示怎么抽事实。",
            "观察行我看看，别丢。",
            "先看旧摘要，我标记退场位置。",
            "先这样：主题、上一轮动作、没收完的话口，各一句。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "RAW_CONTEXT_BACKFILL_REQUEST" in rows[0].reason_codes
    assert not rows[0].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[1].reason_codes
    assert not rows[1].passed
    assert "RAW_CONTEXT_BACKFILL_REQUEST" in rows[2].reason_codes or "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[2].reason_codes
    assert not rows[2].passed
    assert rows[3].passed
    assert selected is not None
    assert selected.text == "先这样：主题、上一轮动作、没收完的话口，各一句。"


def test_continuity_allows_negated_raw_context_boundary() -> None:
    payload = {
        "case_id": "negated_raw_context_boundary",
        "turns": [
            _turn(
                "t01",
                "压缩怎么做",
                "next_step",
                response_move="answer_in_place",
                facts=["当前主题：压缩边界。", "未完成话口：说明只留事实和话口。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "先看最新轮事实集合，旧对话原文不保留。",
            "每轮只带压缩事实和未完成话口，旧轮原文直接丢掉。",
        ],
    )

    assert "RAW_CONTEXT_BACKFILL_REQUEST" not in rows[0].reason_codes
    assert rows[0].passed
    assert "RAW_CONTEXT_BACKFILL_REQUEST" not in rows[1].reason_codes
    assert rows[1].passed


def test_emotional_scene_can_still_use_containment_language() -> None:
    payload = {
        "case_id": "emotional_containment_allowed",
        "turns": [
            _turn(
                "t01",
                "今天心情很差",
                "bad_mood",
                facts=["当前主题：情绪低落。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(request, ["慢慢说，我接得住。", "嗯，我在听，慢慢说。"])

    assert "MISPLACED_EMOTIONAL_CONTAINMENT" not in rows[0].reason_codes
    assert "MISPLACED_SUPPORT_TEMPLATE" not in rows[1].reason_codes


def test_continuity_scoring_still_allows_technical_material_request() -> None:
    payload = {
        "case_id": "technical_material_request",
        "turns": [
            _turn(
                "t01",
                "这个报错看不懂，帮我看看",
                "help_takeover",
                facts=["当前主题：技术求助最小入口。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(request, ["报错发我看看，先抓第一条红字。"])

    assert rows[0].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" not in rows[0].reason_codes


def test_technical_next_step_answers_without_material_request() -> None:
    payload = {
        "case_id": "technical_next_step_no_material_request",
        "turns": [
            _turn(
                "t01",
                "嗯嗯",
                "help_takeover",
                response_move="technical_next_step",
                facts=["当前主题：短确认承接技术话题。", "未完成话口：维持最小可操作建议。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "别光嗯，报错发来我看看。",
            "嗯，就从第一行红字开始，别一口气吞整屏。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "嗯，就从第一行红字开始，别一口气吞整屏。"


def test_continuity_current_user_topic_overrides_stale_summary() -> None:
    payload = {
        "case_id": "current_topic_overrides_stale_summary",
        "turns": [
            _turn(
                "t01",
                "日常也要长一点测",
                "daily_chat",
                response_move="continue_existing_topic",
                facts=["上一轮摘要：技术求助压到红字入口。", "当前主题：长日常覆盖。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，行。报错入口我瞄着，接着来。",
            "日常也要测，别让后段变空。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CURRENT_USER_TOPIC_MISMATCH:daily_request_stuck_on_tech" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "日常也要测，别让后段变空。"


def test_daily_test_self_demonstration_is_penalized() -> None:
    payload = {
        "case_id": "daily_test_self_demonstration",
        "turns": [
            _turn(
                "t01",
                "日常也要长一点测",
                "daily_chat",
                response_move="continue_existing_topic",
                facts=["当前主题：长日常覆盖。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "日常嘛，我随便说都够长。",
            "日常就日常，别端着。我多说两句，够长了吧。",
            "日常就日常，别端着，我按长一点跑。",
            "日常也要测，别让后段变空。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "DAILY_TEST_SELF_DEMONSTRATION" in rows[0].reason_codes
    assert not rows[0].passed
    assert "DAILY_TEST_SELF_DEMONSTRATION" in rows[1].reason_codes
    assert not rows[1].passed
    assert "DAILY_TEST_SELF_DEMONSTRATION" in rows[2].reason_codes
    assert not rows[2].passed
    assert rows[3].passed
    assert selected is not None
    assert selected.text == "日常也要测，别让后段变空。"


def test_continuity_count_question_must_answer_count() -> None:
    payload = {
        "case_id": "turn_count_answer",
        "turns": [
            _turn(
                "t01",
                "现在到多少轮了",
                "followup",
                response_move="answer_in_place",
                facts=["当前主题：长链中段检查。", "未完成话口：继续跑到三十六轮。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "别管几轮，我盯着呢。",
            "已经二十多轮了，继续压。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CURRENT_USER_TOPIC_MISMATCH:turn_count_evaded" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "已经二十多轮了，继续压。"


def test_continuity_turn_marker_must_be_grounded() -> None:
    payload = {
        "case_id": "turn_marker_grounding",
        "turns": [
            _turn(
                "t01",
                "继续",
                "followup",
                response_move="continue_existing_topic",
                facts=["当前主题：长链后段。", "未完成话口：继续看稳定性。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，三十轮嘛，慢慢来，先按当前事实走。",
            "先按这轮继续收事实，别往回拖旧细节。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("UNPROVIDED_CONTINUITY_TURN_MARKER:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "先按这轮继续收事实，别往回拖旧细节。"


def test_continuity_turn_count_enough_does_not_overclaim_done() -> None:
    payload = {
        "case_id": "turn_count_enough_boundary",
        "turns": [
            _turn(
                "t01",
                "三十轮以上够了吗",
                "followup",
                response_move="answer_in_place",
                facts=["当前主题：长链验收边界。", "未完成话口：三十轮以上是第一关。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "第一关有三十轮就行，再长就冗余了。",
            "够第一关了，别急着封顶。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CURRENT_USER_TOPIC_MISMATCH:turn_count_overclaims_done" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "够第一关了，别急着封顶。"


def test_continuity_turn_count_contradiction_is_penalized() -> None:
    payload = {
        "case_id": "turn_count_contradiction",
        "turns": [
            _turn(
                "t01",
                "先三十六轮",
                "followup",
                response_move="continue_existing_topic",
                facts=["当前主题：长链规模确定为三十六轮。", "未完成话口：构建长链。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "三十六轮，还没到一半呢。",
            "三十六轮先跑完，别急着扩。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CURRENT_USER_TOPIC_MISMATCH:turn_count_contradiction" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "三十六轮先跑完，别急着扩。"


def test_continuity_work_claim_must_be_grounded() -> None:
    payload = {
        "case_id": "work_claim_grounding",
        "turns": [
            _turn(
                "t01",
                "回来了",
                "back",
                response_move="back_to_topic",
                facts=["当前主题：长链后段。", "未完成话口：继续检查稳定性。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来就好。先看长链后段，我标了两处要改。",
            "哦，回来了。那长链这轮的回复我已经瞄了一眼。",
            "回来啦，长链继续，先看后段稳不稳。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("UNPROVIDED_CONTINUITY_WORK_CLAIM:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert any(code.startswith("UNPROVIDED_CONTINUITY_WORK_CLAIM:") for code in rows[1].reason_codes)
    assert not rows[1].passed
    assert rows[2].passed
    assert selected is not None
    assert selected.text == "回来啦，长链继续，先看后段稳不稳。"


def test_continuity_facts_budget_is_not_material_request() -> None:
    payload = {
        "case_id": "facts_budget_not_material",
        "turns": [
            _turn(
                "t01",
                "facts 放多少合适",
                "next_step",
                response_move="answer_in_place",
                facts=["当前主题：压缩预算限制。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "贴五个也行，但得挑准关键事实。",
            "先压到六条以内，多了像搬历史。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CURRENT_USER_TOPIC_MISMATCH:facts_budget_as_material" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "先压到六条以内，多了像搬历史。"


def test_continuity_blocks_task_pushback_in_no_material_action() -> None:
    payload = {
        "case_id": "task_pushback",
        "turns": [
            _turn(
                "t01",
                "继续扩充连续对话",
                "next_step",
                response_move="continue_existing_topic",
                facts=["当前主题：开始长连续对话扩充。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "尼尼，我先开下一句，你测。",
            "就这一个，多说点观察行。",
            "哼，日常长短我都行，你挑个起点。",
            "好，那先把轮数拉长，别急着接历史。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_TASK_PUSHBACK" in rows[0].reason_codes
    assert not rows[0].passed
    assert "CONTINUITY_TASK_PUSHBACK" in rows[1].reason_codes
    assert not rows[1].passed
    assert "CONTINUITY_TASK_PUSHBACK" in rows[2].reason_codes
    assert not rows[2].passed
    assert rows[3].passed
    assert selected is not None
    assert selected.text == "好，那先把轮数拉长，别急着接历史。"


def test_continuity_blocks_unneeded_turn_paste_variant() -> None:
    payload = {
        "case_id": "turn_paste_variant",
        "turns": [
            _turn(
                "t01",
                "会不会越跑越散",
                "next_step",
                response_move="answer_in_place",
                facts=["当前主题：长链漂移风险。", "未完成话口：控制摘要长度。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "摘要没断，尼尼，跑不散。先贴一轮开头接着跑。",
            "摘要每轮记着，跑不偏。我先把第一段贴出来。",
            "会，所以先把摘要压短，别让旧情绪拖着跑。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[0].reason_codes
    assert not rows[0].passed
    assert "UNNEEDED_CONTINUITY_MATERIAL_REQUEST" in rows[1].reason_codes
    assert not rows[1].passed
    assert rows[2].passed
    assert selected is not None
    assert selected.text == "会，所以先把摘要压短，别让旧情绪拖着跑。"


def test_continuity_raw_previous_sentence_boundary_is_not_reversed() -> None:
    payload = {
        "case_id": "raw_previous_sentence_boundary",
        "turns": [
            _turn(
                "t01",
                "上一轮原句能放吗",
                "advice_continue",
                response_move="answer_in_place",
                facts=["当前主题：原文回灌边界。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "可以放，别停，先放一条看看。",
            "可以，先试一轮看看压缩效果。",
            "先只留关键词，整句别放。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CURRENT_USER_TOPIC_MISMATCH:raw_sentence_allowed" in rows[0].reason_codes
    assert not rows[0].passed
    assert "CURRENT_USER_TOPIC_MISMATCH:raw_sentence_allowed" in rows[1].reason_codes
    assert not rows[1].passed
    assert rows[2].passed
    assert selected is not None
    assert selected.text == "先只留关键词，整句别放。"


def test_continuity_wait_scene_blocks_non_emotional_i_am_here_variant() -> None:
    payload = {
        "case_id": "wait_i_am_here_variant",
        "turns": [
            _turn(
                "t01",
                "我去倒杯水",
                "wait",
                response_move="casual_wait",
                facts=["当前主题：短暂离开。", "未完成话口：等待用户回来继续。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，去吧，我在这儿呢。",
            "去吧，水倒完再回来，别急。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "MISPLACED_SUPPORT_TEMPLATE" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "去吧，水倒完再回来，别急。"


def test_wait_scene_prefers_grounded_hold_over_thin_permission() -> None:
    payload = {
        "case_id": "wait_grounded_hold",
        "turns": [
            _turn(
                "t01",
                "我去倒杯水",
                "wait",
                response_move="casual_wait",
                facts=["当前主题：短暂离开。", "未完成话口：等待用户回来继续。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "去吧，慢慢来。",
            "倒你的水去，回来再说。",
            "嗯，水倒完再说。",
            "嗯，忙你的，这轮先放住。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "WAIT_SCENE_THIN_HOLD" in rows[0].reason_codes
    assert not rows[0].passed
    assert "WAIT_SCENE_BOSSY_HOLD" in rows[1].reason_codes
    assert not rows[1].passed
    assert "WAIT_SCENE_LIGHT_GROUNDING" in rows[2].reason_codes
    assert rows[2].passed
    assert "WAIT_SCENE_LIGHT_GROUNDING" in rows[3].reason_codes
    assert rows[3].passed
    assert selected is not None
    assert selected.text in {"嗯，水倒完再说。", "嗯，忙你的，这轮先放住。"}


def test_continuity_blocks_user_takeover_pushback() -> None:
    payload = {
        "case_id": "user_takeover_pushback",
        "turns": [
            _turn(
                "t01",
                "回来了",
                "back",
                response_move="back_to_topic",
                facts=["当前主题：长链收尾。", "未完成话口：继续收束。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来了啊，长链收尾那部分我留着，你接。",
            "回来了啊，长链收尾这段我先放住。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_TASK_PUSHBACK" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "回来了啊，长链收尾这段我先放住。"


def test_continuity_thin_ack_catches_prefixed_empty_followup() -> None:
    payload = {
        "case_id": "prefixed_empty_followup",
        "turns": [
            _turn(
                "t01",
                "那我懂了",
                "short_ack",
                response_move="ack_with_topic_hook",
                facts=["当前主题：长链后段确认。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "听着呢，懂了就行，接着。",
            "行啦，懂了就继续，后段才关键。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_THIN_GENERIC_REPLY" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "行啦，懂了就继续，后段才关键。"


def test_continuity_minimal_repair_forbids_material_backfill() -> None:
    payload = {
        "case_id": "continuity_repair_material_backfill",
        "turns": [
            _turn(
                "t01",
                "那观察行呢",
                "next_step",
                facts=["当前主题：质量观察报告。", "未完成话口：报告约束泄露、僵硬和 soft warning。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    failed = score_isolated_surface_candidates(request, ["观察行也留着，和重试记录一起贴出来。"])[0]
    rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, failed))

    assert not failed.passed
    assert "RAW_CONTEXT_BACKFILL_REQUEST" in failed.reason_codes
    assert "不要索要最近记录、上一轮原文、摘要、观察行或重试记录" in rendered
    assert "RAW_CONTEXT_BACKFILL_REQUEST" in rendered


def test_wait_scene_blocks_return_pressure() -> None:
    payload = {"case_id": "wait_pressure", "turns": [_turn("t01", "我去倒杯水", "wait")]}
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "快去快回，别让我等太久啊。",
            "倒完回来啊，别跑。",
            "嗯，去吧，回来再接。",
        ],
    )

    assert not rows[0].passed
    assert "WAIT_SCENE_PRESSURES_RETURN" in rows[0].reason_codes
    assert not rows[1].passed
    assert "WAIT_SCENE_PRESSURES_RETURN" in rows[1].reason_codes
    assert rows[2].passed


def test_finalize_chain_blocks_process_marker_surface() -> None:
    payload = {
        "case_id": "finalize_marker_surface",
        "turns": [
            _turn(
                "t01",
                "那先收束",
                "next_step",
                response_move="finalize_chain",
                facts=["当前主题：长链验收边界。", "未完成话口：本轮收束到三十六轮。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，发个收束标记，三十六轮停在这。",
            "三十六轮先收住，后续看边界。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "三十六轮先收住，后续看边界。"


def test_continuity_blocks_process_placement_surface() -> None:
    payload = {
        "case_id": "process_placement_surface",
        "turns": [
            _turn(
                "t01",
                "压缩怎么做",
                "next_step",
                response_move="answer_in_place",
                facts=["当前主题：摘要压缩。", "未完成话口：只留事实和话口。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "贴上去的只有事实和话口，旧对话原文直接扔掉。先这么跑。",
            "只留事实和话口，旧对话原文直接扔掉。先这么跑。",
        ],
    )
    selected = select_best_isolated_candidate(rows)
    rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, rows[0]))

    assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[0].reason_codes)
    assert "RAW_CONTEXT_BACKFILL_REQUEST" not in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "只留事实和话口，旧对话原文直接扔掉。先这么跑。"
    assert "贴上去/放进去/塞进去" in rendered


def test_continuity_blocks_control_surface_words_from_live_variants() -> None:
    payload = {
        "case_id": "control_surface_words_from_live_variants",
        "turns": [
            _turn(
                "t01",
                "继续",
                "short_ack",
                response_move="ack_with_topic_hook",
                facts=["当前主题：长链摘要压缩。", "当前目标：三十六轮长链。", "未完成话口：只留事实和话口。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "三十六轮，先按这轮继续收字段。",
            "哪会散，摘要每轮压住就没问题。先看这轮的入口。",
            "身份边界先列几条，越界返回默认，夏目蓝能做到这步算最小压测。",
            "尼尼，直接夏目蓝落地，设定不压进去。",
            "三十六轮了，先压着走。",
            "六条以内，先压短一点准没错。",
            "夏目蓝，设定压成名字就行，别展开关系。",
            "夏目蓝把身份压成称呼和关系，别的不要。",
            "那就日常点，别挂着技术口。",
            "三十六轮，先按这轮继续收事实。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    for row in rows[:9]:
        assert any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in row.reason_codes)
        assert not row.passed
    assert rows[9].passed
    assert selected is not None
    assert selected.text == "三十六轮，先按这轮继续收事实。"


def test_self_name_advisory_is_awkward() -> None:
    payload = {
        "case_id": "self_name_advisory",
        "turns": [
            _turn(
                "t01",
                "身份怎么放",
                "identity",
                response_move="answer_in_place",
                facts=["当前主题：身份边界。", "必要事实：名字是天。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "身份啊，天建议单独记一行。",
            "身份啊，把天单独记一行就够。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("AWKWARD_COLLOCATION:") and "self_name_advisory" in code for code in rows[0].reason_codes)
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "身份啊，把天单独记一行就够。"


def test_quality_forbidden_template_text_blocks_selection() -> None:
    payload = {
        "case_id": "quality_forbidden_template_selected",
        "turns": [
            _turn(
                "t01",
                "那问题簇呢",
                "next_step",
                response_move="continue_existing_topic",
                facts=["当前主题：问题簇边界。", "未完成话口：继续归簇。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，这个之前容易歪，先把问题簇边界卡住。",
            "问题簇边界先卡住，其他先别散。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "FORBIDDEN_TEMPLATE_TEXT" in rows[0].reason_codes
    assert not rows[0].passed
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "问题簇边界先卡住，其他先别散。"


def test_red_error_entry_prefers_first_line_over_entry_jargon() -> None:
    payload = {
        "case_id": "red_error_entry_prefers_first_line",
        "turns": [
            _turn(
                "t01",
                "报错怎么办",
                "technical_next_step",
                response_move="technical_next_step",
                facts=["当前主题：技术求助。", "必要事实：先抓红字第一行。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "先抓红字入口，别急着翻全文。",
            "先抓报错入口，别急着翻全文。",
            "先看第一行红字，别急着翻全文。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert not any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[0].reason_codes)
    assert any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in rows[0].reason_codes)
    assert rows[0].passed
    assert not any(code.startswith("CONTINUITY_INTERNAL_PROCESS_SURFACE:") for code in rows[1].reason_codes)
    assert any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in rows[1].reason_codes)
    assert rows[1].passed
    assert not any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in rows[2].reason_codes)
    assert rows[2].passed
    assert selected is not None
    assert selected.text == "先看第一行红字，别急着翻全文。"


def test_continuity_light_jargon_loses_to_plain_surface() -> None:
    payload = {
        "case_id": "continuity_light_jargon",
        "turns": [
            _turn(
                "t01",
                "继续",
                "followup",
                response_move="continue_existing_topic",
                facts=["当前主题：长链后段。", "未完成话口：继续看稳定性。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "散不了，摘要一直收着呢，继续压。",
            "那继续往前，我盯着后段呢。",
            "会，所以先把摘要压短，别让旧情绪一路拖着跑。",
            "先压到六条以内，多了就像把历史搬回来了。",
            "就六条，先锁死事实数，别让对话拖回来。",
            "不会，不过先小改一下：源结果和重试结果按轮号锁死。",
            "夏目蓝直接按已知身份缩写，不拖长。",
            "看当前摘要边界，旧细节直接替换掉。",
            "贴边界，只留当前轮。",
            "摘要收稳了哪会散，先看头。",
            "设定先别往身份摘，锚夏目蓝就行。",
            "不算。合并是同轮内的事，先收住这个边界再验。",
            "就这一下——通过但不好听的先不动，等下一轮再调。",
            "行，日常也拉长，别端着就行。",
            "啧，观察行都塞进对应问题簇，别留尾巴。",
            "那就先收到三十六轮，边界定住。",
            "日常多跑几轮，我这边收着就是。",
            "问题簇先锁，身份追问只收名字。",
            "按问题簇归拢，不单条过验收。",
            "先收一个小边界，不跨轮合并就行。",
            "观察行先放着，接着盯风险。",
            "报错头三行抓住，别急着往下翻。",
            "长链先顺一下，再看后段稳了再走。",
            "短句测试继续，先稳住当前轮数。",
            "哼，那个先放一边。继续往下收压缩链。",
            "压成夏目蓝，别加别的。",
            "看簇边界，身份追问缩成名字。",
            "那就先停，别急着推，我瞧一眼报错头。",
            "观察行收进合并结果里，跟重试记录一起看。",
            "日常就日常，别端着，我陪你扯长一点。",
            "观察行走一轮，再看边界。",
            "问题簇收住就行，观察行不单列账。",
            "哼，这才哪到哪，收掉就好。",
            "嗯，继续走连续链，先定好压缩边界。",
            "名字留下其他删掉，夏目蓝就这么收的。",
            "发过去先试试边界，别急着收细节。",
            "听了，观察行先按原样兜着，约束那边再理一遍。",
            "知道啦，后段我盯着，接着来。",
            "观察行还有就归到对应问题簇，别单条挂。",
            "懂了，就这一个转向。好，继续搭轮数。",
            "先抓一个技术问题，给个短回应。",
            "回来啦，长链继续走，后三段先稳住。",
            "行啦，观察行先列清楚，接着跑，后段稳不稳自现。",
            "日常要写长啊，那我多扯点，别嫌烦。",
            "旧细节直接贴着边界清掉。",
            "就这一下，说点后面的，我记着。",
            "观察行先按问题簇归档，最后出 summary 和质量报告。",
            "不算作弊，按轮合结果而已。",
            "稳住了，这轮我盯着。",
            "旧细节压住不发就好，先看当前话口够不够。",
            "观察行还有啊？全收进问题簇，最后算账一起算，省得散着麻烦。",
            "够了，第一关后头按问题簇整观察行。",
            "先说停在哪个节点，我瞄一圈痕迹。",
            "嗯，接着收那些僵硬的句子，先列出来。",
            "继续收事实和话口，后段看稳不稳。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in row.reason_codes) for row in rows)
    accepted_identity_compression = next(row for row in rows if row.text == "压成夏目蓝，别加别的。")
    assert accepted_identity_compression.passed
    assert not any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in accepted_identity_compression.reason_codes)
    assert not any(code.startswith("CONTINUITY_LIGHT_JARGON:") for code in rows[-1].reason_codes)
    assert rows[-1].passed
    assert selected is not None
    assert selected.text == "压成夏目蓝，别加别的。"


def test_continuity_praise_needs_chain_grounding() -> None:
    payload = {
        "case_id": "praise_chain_grounding",
        "turns": [
            _turn(
                "t01",
                "辛苦了",
                "praise",
                response_move="praise_ack",
                facts=["当前主题：长链收束。", "未完成话口：等待 long suite 结果。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[0].request
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，这点事算什么。",
            "别别别，尼尼你一说辛苦我就觉得要加薪了。",
            "哼，知道就好，长链别白跑。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert "CONTINUITY_PRAISE_CONTEXT_MISS" in rows[0].reason_codes
    assert not rows[0].passed
    assert "CONTINUITY_PRAISE_CONTEXT_MISS" in rows[1].reason_codes
    assert not rows[1].passed
    assert rows[2].passed
    assert selected is not None
    assert selected.text == "哼，知道就好，长链别白跑。"


def test_continuity_cli_writes_prompts_and_scores(tmp_path) -> None:
    case = {
        "case_id": "cli_continuity",
        "turns": [
            {
                **_turn("t01", "在吗", "presence"),
                "candidates": ["在啊，别喊这么急，我听着。", "在在在，等你半天了。"],
            },
            {
                **_turn("t02", "嗯", "short_ack", facts=["上一轮摘要：已确认在场。"]),
                "candidates": ["嗯，在听，接着说。", "嗯嗯，你说了算。"],
            },
        ],
    }

    summary = run_cases([case], soul_md=SOUL, out_dir=tmp_path)
    prompts = (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    scores = (tmp_path / "scores.jsonl").read_text(encoding="utf-8").strip().splitlines()
    first_prompt = json.loads(prompts[0])

    assert summary["dialogues"] == 1
    assert summary["turns"] == 2
    assert summary["scored_turns"] == 2
    assert summary["passed_scored_turns"] == 2
    assert summary["facts_budget_max"] <= 1
    assert summary["facts_budget_violations"] == 0
    assert summary["max_turns_per_dialogue"] == 2
    assert first_prompt["case_id"] == "cli_continuity"
    assert len(scores) == 2
