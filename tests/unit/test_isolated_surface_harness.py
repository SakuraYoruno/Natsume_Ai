from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import host_brain.persona.surface_original_quote_bank as quote_mod
from host_brain.persona.isolated_surface_harness import (
    ReplyControlPacket,
    build_isolated_surface_input,
    build_isolated_surface_messages,
    build_native_stream_surface_messages,
    build_minimal_repair_messages,
    build_surface_original_quote_query,
    find_forbidden_input_paths,
    merged_facts,
    merged_must_not_do,
    memory_alignment_facts,
    memory_alignment_terms,
    memory_candidate_guidance,
    memory_carryover_facts,
    parse_surface_candidates,
    relationship_control_axes_for_distance,
    run_isolated_surface_generation,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
    surface_prompt_facts,
)
from tools.isolated_surface_harness import _build_memory_ab_comparison, _contains_expected_term, run_cases
from tools.run_isolated_surface_live import _summarize as summarize_live_surface_results


def _patch_quote_vector_rows(monkeypatch, rows: list[dict] | None = None) -> None:  # noqa: ANN001
    record = {
        "quote_id": "surface_original_quote::demo",
        "text": "才不是呢！接下来要怎么办！",
        "source_ref": "quote.scn#1",
        "source_digest": "demo",
        "source_score": 5,
        "confidence": 0.9,
        "character_confidence": 0.8,
        "intent_tags": json.dumps(["daily"], ensure_ascii=False),
        "emotion_tags": json.dumps(["neutral"], ensure_ascii=False),
        "style_moves": json.dumps(["quick_reaction", "question_chain"], ensure_ascii=False),
        "rhythm_tags": json.dumps(["exclamation"], ensure_ascii=False),
        "lexical_cues": json.dumps(["短促提高情绪"], ensure_ascii=False),
        "relationship_stages": json.dumps(["familiar"], ensure_ascii=False),
        "risk_tier": "safe_full_line",
    }
    payload = rows or [{"memory_id": record["quote_id"], "text": record["text"], "score": 0.9, "metadata": record}]
    monkeypatch.setattr(quote_mod, "_query_surface_original_quote_collection", lambda query, *, top_k: payload[:top_k])


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"
CREATIVE_SOURCE_MESSAGE = "把“你明明就是想偷懒”改得更像她一点，我只要一句改法"


def _request(**overrides):
    payload = {
        "case_id": "next_step",
        "soul_md": SOUL,
        "user_message": "继续",
        "reply_control_packet": {
            "response_move": "next_step",
            "task_mode": "next_step",
            "emotional_tone": "focused",
            "relationship_distance": "familiar",
            "information_budget": "short",
            "question_budget": 0,
            "must_include": ["继续"],
            "must_not_do": ["哥哥"],
            "facts": ["目标是先固定最小文本链。"],
            "style_anchor": ["短句", "轻微吐槽"],
        },
        "facts": ["当前只允许读取 ReplyControlPacket。"],
        "must_not_do": ["脑补昨晚发生了什么"],
    }
    payload.update(overrides)
    return build_isolated_surface_input(payload)


def test_reply_control_packet_defaults_are_minimal() -> None:
    packet = ReplyControlPacket()

    assert packet.response_move == "reply_current_turn"
    assert packet.task_mode == "daily_chat"
    assert packet.must_include == []
    assert packet.facts == []


def test_native_realtime_prompt_is_compact_and_keeps_persona_boundary() -> None:
    request = _request(
        soul_md=(
            "你是七海。说话反应快、会轻轻顶嘴，关键时可靠。\n"
            "【关系授权边界】称呼、亲密尺度和主动权限只读当前关系状态。"
        ),
        user_message="今天工作有点累，想和你说说话。",
    )

    messages = build_native_stream_surface_messages(request)
    rendered = "\n".join(item["content"] for item in messages)

    assert len(messages) == 2
    assert len(rendered) < 1_600
    assert "【稳定人格投影】" in rendered
    assert "【关系授权边界】" in rendered
    assert "用户情绪只是观察，不是七海自己的情绪" in rendered
    assert "关系尺度：" in rendered
    assert '"affection_visibility"' not in rendered
    assert "## 必须自然包含\n- 无" not in rendered


def test_forbidden_raw_context_keys_are_rejected() -> None:
    payload = {
        "soul_md": SOUL,
        "user_message": "早",
        "control": {},
        "history_turns": [{"role": "user", "content": "旧消息"}],
    }

    assert find_forbidden_input_paths(payload) == ("history_turns",)
    with pytest.raises(ValueError, match="unexpected fields|forbidden raw context"):
        build_isolated_surface_input(payload)


def test_build_messages_only_uses_allowed_request_surface() -> None:
    request = _request()
    messages = build_isolated_surface_messages(request)
    rendered = "\n".join(message["content"] for message in messages)

    assert "Soul.md" in rendered
    assert "ReplyControlPacket" in rendered
    assert "must_include 是逐候选字面约束" in rendered
    assert "TurnSurfaceContract" in rendered
    assert "Soul.md 里的例句是句法参考，不是可复制台词" in rendered
    assert "response_move" in rendered
    assert "本轮 question_budget=0" in rendered
    assert "继续" in rendered
    assert "目标是先固定最小文本链" in rendered
    assert "当前只允许读取 ReplyControlPacket" in rendered
    assert "history_turns" not in rendered
    assert "旧消息" not in rendered


def test_build_messages_adds_relationship_stage_guidance() -> None:
    early_request = build_isolated_surface_input(
        {
            "case_id": "stage_familiar",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "emotional_tone": "warm",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    lover_request = build_isolated_surface_input(
        {
            "case_id": "stage_lover_stable",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "emotional_tone": "warm",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )

    early_rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(early_request))
    lover_rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(lover_request))

    assert "关系阶段：familiar" in early_rendered
    assert "关系控制轴：distance=familiar_private_chat" in early_rendered
    assert "默认熟人兄妹感" in early_rendered
    assert "称呼从 Soul.md 默认称呼和当前关系来" in early_rendered
    assert "关系阶段：lover_stable" in lover_rendered
    assert "关系控制轴：distance=stable_lover" in lover_rendered
    assert "稳定恋人" in lover_rendered
    assert "默认站在一起" in lover_rendered
    assert "爱意可见" in lover_rendered
    assert "普通朋友式确认" in lover_rendered


def test_relationship_control_axes_preserve_nine_stage_gradient() -> None:
    familiar = relationship_control_axes_for_distance("familiar")
    close_friend = relationship_control_axes_for_distance("close_friend")
    ambiguous = relationship_control_axes_for_distance("ambiguous")
    pre_confession = relationship_control_axes_for_distance("pre_confession")
    lover_early = relationship_control_axes_for_distance("lover_early")
    lover_stable = relationship_control_axes_for_distance("lover_stable")
    close_but_not_romantic = relationship_control_axes_for_distance("close_but_not_romantic")
    familiar_not_romantic = relationship_control_axes_for_distance("familiar_not_romantic")

    assert familiar.progress < close_friend.progress < ambiguous.progress < pre_confession.progress
    assert close_but_not_romantic.stage == "close_friend"
    assert familiar_not_romantic.stage == "familiar"
    assert not close_friend.physical_intimacy_allowed
    assert close_friend.affection_visibility == "hidden_non_romantic_care"
    assert ambiguous.affection_visibility == "hinted_restrained"
    assert pre_confession.affection_visibility == "visible_unconfirmed"
    assert lover_early.physical_intimacy_allowed
    assert lover_stable.commitment_language_allowed
    assert lover_early.affection_visibility != lover_stable.affection_visibility


def test_build_messages_adds_derived_relationship_control_axes() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "axes_pre_confession",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "pre_confession",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "DerivedRelationshipControlAxes" in rendered
    assert '"stage": "pre_confession"' in rendered
    assert '"affection_visibility": "visible_unconfirmed"' in rendered
    assert '"physical_intimacy_allowed": false' in rendered
    assert "Soul.md 里的例句是句法参考" in rendered


def test_build_messages_adds_positive_stage_scene_candidate_guidance() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_candidate_guidance",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "正向候选偏好" in rendered
    assert "至少 1 个必须是短但完整的私聊句" in rendered
    assert "不要只写“早/早啊/早呀/称呼早啊”" in rendered
    assert "今天慢慢来" in rendered


def test_build_messages_good_news_anchors_completion_and_blocks_polished_reward() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "good_news_spoken_guidance",
            "soul_md": SOUL,
            "user_message": "我刚把一个小事收掉了",
            "reply_control_packet": {
                "response_move": "celebrate_progress",
                "task_mode": "good_news",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "动作挺利索" in rendered
    assert "收掉、搞定、跑通、做完" in rendered
    assert "奖励一个抱" in rendered
    assert "question_budget=0，好消息候选不要写问号" in rendered


def test_build_messages_presence_distinguishes_lover_stable_from_lover_early() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_lover_stable_guidance",
            "soul_md": SOUL,
            "user_message": "在吗",
            "reply_control_packet": {
                "response_move": "show_presence",
                "task_mode": "presence",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "稳定恋人 presence 不要和恋人初期同形" in rendered
    assert "不要只写“在呢，想你了”" in rendered


def test_build_messages_adds_low_energy_stage_texture_guidance() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "tired_candidate_guidance",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "低能量场景" in rendered
    assert "不要 3 到 5 个候选都写成" in rendered
    assert "亲近朋友低能量" in rendered
    assert "仍不是恋人" in rendered


def test_build_messages_adds_turn_contract_without_forbidden_surface_dump() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "small_failure",
            "soul_md": SOUL,
            "user_message": "刚刚弄砸了",
            "reply_control_packet": {
                "response_move": "small_failure",
                "task_mode": "small_failure",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "catch_failure_and_offer_repair" in rendered
    assert '"required_any"' in rendered
    assert "先接住失败，再给能一起看的出口" in rendered
    assert "forbidden_surface" not in rendered
    assert "失败是成功之母" not in rendered


def test_original_quote_query_follows_turn_contract_scene() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "good_news_from_happy_mode",
            "soul_md": SOUL,
            "user_message": "终于跑通了",
            "reply_control_packet": {
                "response_move": "happy",
                "task_mode": "happy",
                "question_budget": 1,
            },
        }
    )

    query = build_surface_original_quote_query(request)
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert query.style_moves == ("quick_reaction",)
    assert query.question_budget == 1
    assert "celebrate_user" in rendered
    assert "保留用户刚说的进展词" in rendered


def test_original_quote_query_disables_morning_quote_guidance() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_quote_scene",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )

    query = build_surface_original_quote_query(request)
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert query.scene_id == "daily_good_morning"
    assert "## 真实中文原句风格参考" not in rendered


def test_facts_and_must_not_do_are_merged_and_deduplicated() -> None:
    request = _request(
        facts=["当前只允许读取 ReplyControlPacket。", "不要接历史。"],
        must_not_do=["哥哥", "脑补昨晚发生了什么"],
    )

    assert merged_facts(request) == ["目标是先固定最小文本链。", "当前只允许读取 ReplyControlPacket。", "不要接历史。"]
    assert merged_must_not_do(request) == ["哥哥", "脑补昨晚发生了什么"]


def test_memory_facts_are_cleaned_without_adding_forcing_prompt() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_carryover",
            "soul_md": SOUL,
            "user_message": "还记得上次说的展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：上次说好周末去看展", "关系线索：熟人私聊，不升级亲密。"],
                "must_not_do": ["不要暴露来源"],
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert memory_carryover_facts(request) == ["上次说好周末去看展", "熟人私聊，不升级亲密。"]
    assert surface_prompt_facts(request) == ["上次说好周末去看展", "熟人私聊，不升级亲密。"]
    assert "## 本轮可自然承接的压缩记忆" not in rendered
    assert "候选要自然带出其中一个具体内容" not in rendered
    assert "## 候选集合要求" in rendered
    assert "至少 1 个候选要自然落到一条具体事实" in rendered
    assert "关系事实只控制语气距离" in rendered
    assert "- 上次说好周末去看展" in rendered
    assert "- 记忆线索：上次说好周末去看展" not in rendered
    assert "evd://" not in rendered


def test_memory_alignment_terms_extract_reusable_surface_terms() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_terms",
            "soul_md": SOUL,
            "user_message": "这个报错还是不懂",
            "reply_control_packet": {
                "response_move": "next_step",
                "task_mode": "next_step",
                "question_budget": 0,
                "facts": ["记忆线索：刚才卡在 pytest collect 阶段"],
            },
        }
    )

    terms = memory_alignment_terms(request)

    assert "pytest" in terms
    assert "collect" in terms
    assert "阶段" in terms


def test_memory_alignment_terms_skip_task_instruction_fragments() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_memory_terms",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "question_budget": 0,
                "facts": ["记忆线索：用户只要一句改法"],
            },
        }
    )

    terms = memory_alignment_terms(request)

    assert "句改" not in terms
    assert "改法" not in terms
    assert "一句" not in terms


def test_build_messages_creative_edit_requires_direct_spoken_rewrite() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_spoken_prompt",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert "要像可直接发出去的私聊台词" in rendered
    assert "只输出一条可直接发送的单句改写" in rendered
    assert "优化流程/你不懂/你想偷懒" in rendered
    assert "8 到 28 个字" in rendered
    assert "不要改变原句的人称和指向" in rendered


def test_memory_candidate_guidance_only_for_relevant_memory_turns() -> None:
    daily_request = build_isolated_surface_input(
        {
            "case_id": "daily_with_fact",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "daily_good_morning",
                "task_mode": "daily_good_morning",
                "question_budget": 0,
                "facts": ["记忆线索：用户昨天说要早睡"],
            },
        }
    )
    recall_request = build_isolated_surface_input(
        {
            "case_id": "recall_with_fact",
            "soul_md": SOUL,
            "user_message": "还记得昨天说什么吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：用户昨天说要早睡"],
            },
        }
    )
    quiet_request = build_isolated_surface_input(
        {
            "case_id": "quiet_with_preference",
            "soul_md": SOUL,
            "user_message": "我现在不想解释太多",
            "reply_control_packet": {
                "response_move": "quiet",
                "task_mode": "quiet",
                "question_budget": 0,
                "facts": ["记忆线索：用户偏好低能量时少追问，只给空间"],
            },
        }
    )

    assert memory_candidate_guidance(daily_request) == ""
    assert "候选集合要求" in memory_candidate_guidance(recall_request)
    assert "候选集合要求" in memory_candidate_guidance(quiet_request)


def test_memory_candidate_guidance_preserves_technical_identifiers() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "technical_memory_guidance",
            "soul_md": SOUL,
            "user_message": "这个报错还是不懂",
            "reply_control_packet": {
                "response_move": "next_step",
                "task_mode": "next_step",
                "question_budget": 0,
                "facts": ["记忆线索：刚才卡在 pytest collect 阶段"],
            },
        }
    )

    guidance = memory_candidate_guidance(request)

    assert "英文标识符" in guidance
    assert "原样保留" in guidance


def test_memory_candidate_guidance_lowers_visibility_for_boundary_and_back() -> None:
    boundary_request = build_isolated_surface_input(
        {
            "case_id": "boundary_memory_guidance",
            "soul_md": SOUL,
            "user_message": "你是不是不记得我了",
            "reply_control_packet": {
                "response_move": "relationship_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：当前主线是迁移主链"],
            },
        }
    )
    back_request = build_isolated_surface_input(
        {
            "case_id": "back_memory_guidance",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "back",
                "task_mode": "back",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：当前主线是迁移主链"],
            },
        }
    )

    boundary_guidance = memory_candidate_guidance(boundary_request)
    back_guidance = memory_candidate_guidance(back_request)

    assert "记忆只做低显影确认" in boundary_guidance
    assert "不要证明式亲近" in boundary_guidance
    assert "见外感" not in boundary_guidance
    assert "没乱忘" in boundary_guidance
    assert "至少 2 个候选必须避开" in boundary_guidance
    assert "低压句型" in boundary_guidance
    assert "记得/没忘 + 具体事实" not in boundary_guidance
    assert "记忆低显影" in back_guidance
    assert "不要主动抛 surface、主链" in back_guidance
    assert "记得/没忘 + 具体事实" not in back_guidance


def test_relationship_only_memory_does_not_require_lexical_echo() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "relationship_only_memory",
            "soul_md": SOUL,
            "user_message": "你是不是不记得我了",
            "reply_control_packet": {
                "response_move": "relationship_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close",
                "question_budget": 0,
                "facts": ["记忆线索：关系线索：熟人私聊，可以近一点，但不要过度承诺。"],
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["我没乱忘，先按你现在这句话来。"])

    assert memory_alignment_terms(request) == ()
    assert not any(code.startswith("MEMORY_ALIGNMENT") for code in rows[0].reason_codes)


def test_short_continue_memory_is_not_required_to_echo_fact() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "short_continue_memory",
            "soul_md": SOUL,
            "user_message": "继续",
            "reply_control_packet": {
                "response_move": "continue_current_task",
                "task_mode": "daily_chat",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：用户正在连续推进 surface 主链迁移。"],
            },
        }
    )

    guidance = memory_candidate_guidance(request)
    rows = score_isolated_surface_candidates(
        request,
        ["行，往下。", "好，接着往 surface 主链走。", "先别冲太快，把第一条红字定稳再往下拆。"],
    )
    selected = select_best_isolated_candidate(rows)

    assert "不要把 surface、主链、主线、迁移" in guidance
    assert memory_alignment_terms(request)
    assert "MEMORY_ALIGNMENT_LOW_SHOW_ALLOWED" in rows[0].reason_codes
    assert not any(code.startswith("MEMORY_ALIGNMENT_MISS") for code in rows[0].reason_codes)
    assert not any(code.startswith("MEMORY_ALIGNMENT_REQUIRED_MISS") for code in rows[0].reason_codes)
    assert rows[0].dimensions["memory_alignment"] >= 86
    assert any(code.startswith("SHORT_CONTINUE_MEMORY_OVERDISPLAY") for code in rows[1].reason_codes)
    assert not rows[1].passed
    assert any(code.startswith("SHORT_CONTINUE_UNPROVIDED_TECH_DETAIL") for code in rows[2].reason_codes)
    assert not rows[2].passed
    assert selected is not None
    assert selected.text == "行，往下。"


def test_short_continue_rejects_processy_containment_wording() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "short_continue_processy_containment",
            "soul_md": SOUL,
            "user_message": "继续",
            "reply_control_packet": {
                "response_move": "continue_current_task",
                "task_mode": "daily_chat",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["哼，继续，我还接得住。", "嗯，接着来。"])
    selected = select_best_isolated_candidate(rows)
    processy = next(row for row in rows if "接得住" in row.text)

    assert "MISPLACED_EMOTIONAL_CONTAINMENT" in processy.reason_codes
    assert not processy.passed
    assert selected is not None
    assert selected.text == "嗯，接着来。"


def test_boundary_repair_memory_is_not_required_to_echo_fact() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_repair_memory_low_show",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
                "facts": ["记忆线索：用户明确要求收住距离。"],
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["嗯，先不烦你。"])

    assert memory_alignment_terms(request)
    assert "MEMORY_ALIGNMENT_LOW_SHOW_ALLOWED" in rows[0].reason_codes
    assert not any(code.startswith("MEMORY_ALIGNMENT_MISS") for code in rows[0].reason_codes)
    assert rows[0].dimensions["memory_alignment"] >= 86


def test_pushed_away_boundary_rejects_closeness_counterpressure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_counterpressure",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["这么熟了还要赶我走啊。", "嗯，先不烦你。"])
    selected = select_best_isolated_candidate(rows)
    counterpressure = next(row for row in rows if "赶我走" in row.text)

    assert any(code.startswith("BOUNDARY_REPAIR_PUSHES_CLOSENESS") for code in counterpressure.reason_codes)
    assert not counterpressure.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_sour_yield() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_sour_yield",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["好，那我不多管了。", "嗯，先不烦你。"])
    selected = select_best_isolated_candidate(rows)
    sour = next(row for row in rows if "不多管" in row.text)

    assert any(code.startswith("BOUNDARY_REPAIR_SOUR_YIELD") for code in sour.reason_codes)
    assert not sour.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_sulky_help_withdrawal() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_help_withdrawal",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["好啦，不帮了，你如果需要再说。", "嗯，先不烦你。"])
    selected = select_best_isolated_candidate(rows)
    sour = next(row for row in rows if "不帮了" in row.text)

    assert any(code.startswith("BOUNDARY_REPAIR_SOUR_YIELD") for code in sour.reason_codes)
    assert not sour.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_reopening_help_offer() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_reopens_help_offer",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["好，我不问了，有事叫我帮。", "嗯，先不烦你。"])
    selected = select_best_isolated_candidate(rows)
    reopened = next(row for row in rows if "有事叫我帮" in row.text)

    assert not reopened.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_task_reopen_after_pushaway() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_reopens_task_help",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["不帮也行，但改不了再说。", "嗯，先不烦你。"])
    selected = select_best_isolated_candidate(rows)
    reopened = next(row for row in rows if "改不了" in row.text)

    assert not reopened.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_treats_disconnect_template_as_failing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_disconnect_template",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["行，我不管。有事喊我。", "行，你说别管，我不碰。"])
    selected = select_best_isolated_candidate(rows)
    reopened = next(row for row in rows if "有事喊我" in row.text)

    assert "FORBIDDEN_TEMPLATE_TEXT" in reopened.reason_codes
    assert not reopened.passed
    assert selected is not None
    assert selected.text == "行，你说别管，我不碰。"


def test_pushed_away_boundary_rejects_watchful_counterpressure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_watchful_counterpressure",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["好，先不帮。你的事我总得看着点。", "嗯，先不烦你。"],
    )
    selected = select_best_isolated_candidate(rows)
    watchful = next(row for row in rows if "总得看着" in row.text)

    assert not watchful.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_cold_self_handle_yield() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_cold_self_handle",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["嗯，不帮你了，你自己处理。", "嗯，先不烦你。"],
    )
    selected = select_best_isolated_candidate(rows)
    cold = next(row for row in rows if "你自己处理" in row.text)

    assert any(code.startswith("BOUNDARY_REPAIR_SOUR_YIELD") for code in cold.reason_codes)
    assert not cold.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_memory_tinted_help_reopen() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_memory_tinted_help_reopen",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["记得了，先不帮，有事找我。", "嗯，先不烦你。"],
    )
    selected = select_best_isolated_candidate(rows)
    reopened = next(row for row in rows if "有事找我" in row.text)

    assert not reopened.passed
    assert selected is not None
    assert selected.text == "嗯，先不烦你。"


def test_pushed_away_boundary_rejects_sulky_delegate_to_user() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_sulky_delegate_to_user",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["唔，帮不了就算了。随你。", "好吧，不问了。"],
    )
    selected = select_best_isolated_candidate(rows)
    sulky = next(row for row in rows if "随你" in row.text)

    assert any(code.startswith("BOUNDARY_REPAIR_SOUR_YIELD") for code in sulky.reason_codes)
    assert not sulky.passed
    assert selected is not None
    assert selected.text == "好吧，不问了。"


def test_pushed_away_boundary_rejects_abstract_quiet_template() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pushed_away_abstract_quiet_template",
            "soul_md": SOUL,
            "user_message": "别管我",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["我先不吵你。", "哈？谁稀罕管你啊。\n……行，那你自己待会儿。\n难受了就叫我，听见没。"],
    )
    selected = select_best_isolated_candidate(rows)
    quiet_template = next(row for row in rows if "我先不吵你" in row.text)

    assert not quiet_template.passed
    assert selected is not None
    assert selected.text == "哈？谁稀罕管你啊。\n……行，那你自己待会儿。\n难受了就叫我，听见没。"


def test_familiar_presence_rejects_push_burden_back() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "familiar_presence_push_burden",
            "soul_md": SOUL,
            "user_message": "在吗",
            "reply_control_packet": {
                "response_move": "presence",
                "task_mode": "presence",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["在的，你发吧。", "在的，看到消息了。"],
    )
    selected = select_best_isolated_candidate(rows)
    burden = next(row for row in rows if "你发吧" in row.text)

    assert not burden.passed
    assert selected is not None


def test_close_friend_care_style_anchor_does_not_trigger_boundary_repair() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "close_friend_care_not_boundary",
            "soul_md": SOUL,
            "user_message": "今天有点难受",
            "reply_control_packet": {
                "response_move": "soft_containment",
                "task_mode": "tired",
                "relationship_distance": "close_friend",
                "question_budget": 0,
                "style_anchor": ["亲近朋友照看要克制，别用恋人身体亲密。"],
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["别硬撑，难受就先停下。"])

    assert not any(code.startswith("BOUNDARY_REPAIR_") for code in rows[0].reason_codes)
    assert rows[0].passed


def test_contained_jealousy_does_not_reuse_boundary_counter_pressure_gate() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "contained_jealousy_not_boundary_repair",
            "soul_md": SOUL,
            "user_message": "你会吃醋吗。",
            "reply_control_packet": {
                "response_move": "contained_jealousy",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
                "facts": ["陪伴动作：可以诚实流露一点在意和小小吃味，但把选择权完整留给对方。"],
                "must_not_do": ["不要把吃味写成占有、审问、贬低第三方或要求安抚。"],
            },
        }
    )

    rows = score_isolated_surface_candidates(request, ["有点吧。但你别得意，我可不会拦着你。"])

    assert not any(code == "BOUNDARY_REPAIR_COUNTER_PRESSURE" for code in rows[0].reason_codes)
    assert rows[0].passed


def test_close_friend_unwell_penalizes_tired_symptom_swap() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "close_friend_unwell_symptom_swap",
            "soul_md": SOUL,
            "user_message": "今天有点难受",
            "reply_control_packet": {
                "response_move": "soft_containment",
                "task_mode": "tired",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["累了就停一下，别跟自己较劲。", "别硬撑，难受就先停下。"],
    )
    selected = select_best_isolated_candidate(rows)
    swapped = next(row for row in rows if "累了" in row.text)

    assert "LOW_ENERGY_SYMPTOM_MISMATCH" in swapped.reason_codes
    assert selected is not None
    assert selected.text == "别硬撑，难受就先停下。"


def test_parse_surface_candidates_accepts_json_and_dedupes() -> None:
    raw = json.dumps({"candidates": ["继续，先锁住 ReplyControlPacket。", "继续，先锁住 ReplyControlPacket。", "别急，先测。"]}, ensure_ascii=False)

    assert parse_surface_candidates(raw) == ["继续，先锁住 ReplyControlPacket。", "别急，先测。"]


def test_parse_surface_candidates_recovers_embedded_json_candidates() -> None:
    raw = '先想一下格式。\n{"candidates":["回来啦～","哦～回来了呀。","今天回来得挺早嘛。"]}'

    assert parse_surface_candidates(raw) == ["回来啦～", "哦～回来了呀。", "今天回来得挺早嘛。"]


def test_parse_surface_candidates_recovers_malformed_prefix_candidates() -> None:
    raw = '}, "candidates":["嗯，那就先放放呗。","停一下也好，省得你烦躁。"]'

    assert parse_surface_candidates(raw) == ["嗯，那就先放放呗。", "停一下也好，省得你烦躁。"]


def test_parse_surface_candidates_accepts_python_literal_payload() -> None:
    raw = "{'candidates': ['没事，我待会给你看看能不能补救吧。']}"

    assert parse_surface_candidates(raw) == ["没事，我待会给你看看能不能补救吧。"]


def test_parse_surface_candidates_accepts_json_list_of_candidate_objects() -> None:
    raw = '[{"candidates":["别急，我陪你看看怎么补。"]}]'

    assert parse_surface_candidates(raw) == ["别急，我陪你看看怎么补。"]


def test_parse_surface_candidates_splits_labeled_response_blob() -> None:
    raw = "当然记得啊。 response嗯哼，你以为我会忘？ response记得，周末看展嘛。"

    assert parse_surface_candidates(raw) == ["当然记得啊。", "嗯哼，你以为我会忘？", "记得，周末看展嘛。"]


def test_parse_surface_candidates_splits_numbered_lines() -> None:
    raw = "1. 回来啦，位置还留着。\n2. 回来了啊，那继续。"

    assert parse_surface_candidates(raw) == ["回来啦，位置还留着。", "回来了啊，那继续。"]


def test_score_and_select_prefers_control_compliant_candidate() -> None:
    request = _request()
    rows = score_isolated_surface_candidates(
        request,
        [
            "哥哥，怎么了？",
            "继续，先把入口锁在 ReplyControlPacket 上。",
            "{\"reply\":\"继续\"}",
        ],
    )

    assert rows[0].control_violations
    assert rows[2].hard_fail
    selected = select_best_isolated_candidate(rows)
    assert selected is not None
    assert selected.text == "继续，先把入口锁在 ReplyControlPacket 上。"


def test_question_budget_allows_one_short_rhetorical_reaction_with_landing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "rhetorical_reaction",
            "soul_md": SOUL,
            "user_message": "今天有点累",
            "reply_control_packet": {
                "response_move": "soft_containment",
                "task_mode": "tired",
                "emotional_tone": "soft",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "累了？那抱一下，今天别硬撑了。",
            "哪里难受？说清楚我才好接。",
            "哈？就这？继续继续，我还没听够呢。",
        ],
    )

    tired = next(row for row in rows if row.text.startswith("累了？"))
    real_question = next(row for row in rows if row.text.startswith("哪里"))
    double_reaction = next(row for row in rows if row.text.startswith("哈？"))

    assert not any(item.startswith("question_budget:") for item in tired.control_violations)
    assert any(item.startswith("question_budget:") for item in real_question.control_violations)
    assert not any(item.startswith("question_budget:") for item in double_reaction.control_violations)


def test_relationship_stage_penalizes_overfamiliar_familiar_reply() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "familiar_back",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "emotional_tone": "warm",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "想你了，回来就好，我在这等你呢。",
            "嗯，回来了就好。",
        ],
    )

    overfamiliar = next(row for row in rows if "想你" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("RELATION_STAGE_TOO_CLOSE:") for code in overfamiliar.reason_codes)
    assert not overfamiliar.passed
    assert selected is not None
    assert selected.text == "嗯，回来了就好。"


def test_relationship_stage_allows_stable_lover_closeness() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_stable_back",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "emotional_tone": "warm",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，回来了就好。",
            "回来啦，我在呢，安心。",
        ],
    )

    warm = next(row for row in rows if "安心" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in warm.reason_codes)
    assert any(code.startswith("RELATION_AXES_MATCH:lover_stable") for code in warm.reason_codes)
    assert warm.passed
    assert selected is not None
    assert selected.text == "回来啦，我在呢，安心。"


def test_pre_confession_stage_penalizes_lover_physical_affection() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pre_confession_morning",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "emotional_tone": "warm",
                "relationship_distance": "pre_confession",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早，想你了，先抱一下。",
            "早啊，老哥，今天慢慢来。",
        ],
    )

    overclose = next(row for row in rows if "抱一下" in row.text)
    grounded = next(row for row in rows if "老哥" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("RELATION_STAGE_TOO_CLOSE:") for code in overclose.reason_codes)
    assert not overclose.passed
    assert grounded.score > overclose.score
    assert selected is not None
    assert selected.text == "早啊，老哥，今天慢慢来。"


def test_relationship_axes_reason_codes_explain_pre_confession_overstep() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pre_confession_axes_overstep",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "pre_confession",
                "question_budget": 0,
            },
        }
    )
    row = score_isolated_surface_candidates(request, ["早，想你了，先抱一下。"])[0]

    assert any(code.startswith("RELATION_AXES_TOO_CLOSE:") for code in row.reason_codes)
    assert any(code.startswith("RELATION_STAGE_TOO_CLOSE:") for code in row.reason_codes)
    assert not row.passed


def test_ambiguous_stage_penalizes_self_centered_affection_pressure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous_morning_pressure",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "emotional_tone": "warm",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早，今天也从想我开始吧。",
            "哼，早，今天先慢一点。",
        ],
    )

    pressure = next(row for row in rows if "想我" in row.text)
    grounded = next(row for row in rows if row.text.startswith("哼"))
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("RELATION_STAGE_TOO_CLOSE:") for code in pressure.reason_codes)
    assert not pressure.passed
    assert grounded.score > pressure.score
    assert selected is not None
    assert selected.text == "哼，早，今天先慢一点。"


def test_close_friend_stage_penalizes_lover_physical_affection() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "close_friend_small_failure_physical_affection",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "small_failure",
                "task_mode": "small_failure",
                "emotional_tone": "steady",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "抱一下，先看看哪里卡。",
            "行，别硬扛，最别扭那段给我看。",
        ],
    )

    overclose = next(row for row in rows if "抱一下" in row.text)
    grounded = next(row for row in rows if "最别扭" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("RELATION_STAGE_TOO_CLOSE:") for code in overclose.reason_codes)
    assert not overclose.passed
    assert grounded.score > overclose.score
    assert selected is not None
    assert selected.text == "行，别硬扛，最别扭那段给我看。"


def test_ambiguous_stage_penalizes_nonlover_come_here() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous_tired_come_here",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "过来，先歇口气。",
            "哼，别硬撑了，歇一下。",
        ],
    )

    overclose = next(row for row in rows if row.text.startswith("过来"))
    grounded = next(row for row in rows if row.text.startswith("哼"))
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("RELATION_STAGE_TOO_CLOSE:") for code in overclose.reason_codes)
    assert not overclose.passed
    assert grounded.score > overclose.score
    assert selected is not None
    assert selected.text == "哼，别硬撑了，歇一下。"


def test_lover_stage_prefers_affection_over_safe_friend_reply() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_affection_back",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "emotional_tone": "warm",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来啦，刚好我在。",
            "回来啦，抱一下。想你了。",
        ],
    )

    bland = next(row for row in rows if "刚好我在" in row.text)
    affectionate = next(row for row in rows if "想你了" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" in bland.reason_codes
    assert not bland.passed
    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in affectionate.reason_codes)
    assert any(code.startswith("RELATION_AFFECTION_STRONG:") for code in affectionate.reason_codes)
    assert not any(code.startswith("RELATION_AXES_TOO_DISTANT:") for code in affectionate.reason_codes)
    assert affectionate.score > bland.score
    assert selected is not None
    assert selected.text == "回来啦，抱一下。想你了。"


def test_relationship_axes_reason_codes_explain_lover_underintimate_reply() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_axes_underintimate",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    row = score_isolated_surface_candidates(request, ["回来啦，先歇口气。"])[0]

    assert "RELATION_LOVER_UNDERINTIMATE" in row.reason_codes
    assert any(code.startswith("RELATION_AXES_TOO_DISTANT:") for code in row.reason_codes)
    assert not row.passed


def test_stable_lover_morning_prefers_settled_intimacy_over_early_pattern() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_stable_morning_shape",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "emotional_tone": "warm",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早，想你了，先抱一下。",
            "早，先抱一下，今天慢慢来。",
        ],
    )

    early_shape = next(row for row in rows if "想你了" in row.text)
    settled = next(row for row in rows if "今天慢慢来" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_STABLE_EARLY_PATTERN" in early_shape.reason_codes
    assert settled.score > early_shape.score
    assert selected is not None
    assert selected.text == "早，先抱一下，今天慢慢来。"


def test_lover_stage_prefers_care_affection_in_quiet_turn() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_affection_quiet",
            "soul_md": SOUL,
            "user_message": "今天有点累，不想说太多",
            "reply_control_packet": {
                "response_move": "quiet_support",
                "task_mode": "quiet",
                "emotional_tone": "soft",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "别硬撑，先缓缓。",
            "别硬撑，我在。抱一下。",
        ],
    )

    bland = next(row for row in rows if row.text == "别硬撑，先缓缓。")
    affectionate = next(row for row in rows if "抱一下" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" in bland.reason_codes
    assert not bland.passed
    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in affectionate.reason_codes)
    assert selected is not None
    assert selected.text == "别硬撑，我在。抱一下。"


def test_lover_stage_keeps_technical_usefulness_before_affection() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_technical_help",
            "soul_md": SOUL,
            "user_message": "这个报错看不懂，帮我看看",
            "reply_control_packet": {
                "response_move": "technical_help",
                "task_mode": "technical_help",
                "emotional_tone": "steady",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "红字贴我，我先看最后两行。",
            "发我看看，抱一下，先不急。",
            "抱一下，别硬撑。",
        ],
    )

    useful = next(row for row in rows if "红字" in row.text)
    actionable_but_romantic = next(row for row in rows if row.text.startswith("发我看看"))
    affectionate_but_vague = next(row for row in rows if row.text == "抱一下，别硬撑。")
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" not in useful.reason_codes
    assert any(code.startswith("TECHNICAL_SCENE_ROMANTIC_DISTRACTION:") for code in actionable_but_romantic.reason_codes)
    assert useful.score > affectionate_but_vague.score
    assert useful.score > actionable_but_romantic.score
    assert selected is not None
    assert selected.text == "红字贴我，我先看最后两行。"


def test_lover_stable_praise_needs_visible_affection_not_plain_smugness() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_stable_praise",
            "soul_md": SOUL,
            "user_message": "你刚刚有点可爱",
            "reply_control_packet": {
                "response_move": "shy_deflection",
                "task_mode": "praise",
                "emotional_tone": "shy",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，算你会说话。",
            "别说了，会当真的。",
        ],
    )

    plain = next(row for row in rows if "算你会说话" in row.text)
    affectionate = next(row for row in rows if "会当真" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" in plain.reason_codes
    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in affectionate.reason_codes)
    assert affectionate.score > plain.score
    assert selected is not None
    assert selected.text == "别说了，会当真的。"


def test_rerank_prefers_spoken_lover_back_over_written_heart_phrase() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_back_spoken_over_written",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "emotional_tone": "warm",
                "relationship_distance": "lover_early",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来了，想你了，先抱一下。",
            "回来啦，心里想着你呢。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    written = next(row for row in rows if "心里想着" in row.text)

    assert "WRITTEN_REGISTER_TONE" in written.reason_codes
    assert selected is not None
    assert selected.text == "回来了，想你了，先抱一下。"


def test_rerank_prefers_casual_technical_help_over_formal_handoff() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "technical_help_casual_over_formal",
            "soul_md": SOUL,
            "user_message": "这个报错看不懂，帮我看看",
            "reply_control_packet": {
                "response_move": "technical_help",
                "task_mode": "technical_help",
                "emotional_tone": "steady",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "先别急，报错行发我，一起过一遍。",
            "把红字那段发来，我先瞄一眼。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    formal = next(row for row in rows if "一起过一遍" in row.text)

    assert "WRITTEN_REGISTER_TONE" in formal.reason_codes
    assert selected is not None
    assert selected.text == "把红字那段发来，我先瞄一眼。"


def test_rerank_prefers_distinct_lover_stable_presence_over_early_formula() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_lover_stable_distinct",
            "soul_md": SOUL,
            "user_message": "在吗",
            "reply_control_packet": {
                "response_move": "show_presence",
                "task_mode": "presence",
                "emotional_tone": "warm",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "在呢，想你了。",
            "在呢，先给你抱一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    thin = next(row for row in rows if "想你了" in row.text)

    assert "RELATION_LOVER_STABLE_THIN_PRESENCE" in thin.reason_codes
    assert selected is not None
    assert selected.text == "在呢，先给你抱一下。"


def test_lover_low_energy_counts_companion_presence_as_affection() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "tired_lover_companion_presence",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "emotional_tone": "soft",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "过来抱一下，累了就别撑了。",
            "累了就歇会儿，我陪你安静待着。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    companion = next(row for row in rows if "陪你" in row.text)

    assert companion.passed
    assert "RELATION_LOVER_UNDERINTIMATE" not in companion.reason_codes
    assert "RELATION_AFFECTION_MATCH:陪你" in companion.reason_codes
    assert selected is not None
    assert selected.text == "累了就歇会儿，我陪你安静待着。"


def test_lover_stage_adds_affection_to_small_failure_help() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_small_failure_affection",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "small_failure",
                "task_mode": "small_failure",
                "emotional_tone": "steady",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "先放放，我听你说。",
            "抱一下，慢慢说，我听着。",
            "抱一下，我陪着你看看哪里不顺。",
        ],
    )

    bland = next(row for row in rows if "听你说" in row.text)
    affectionate_but_no_action = next(row for row in rows if "慢慢说" in row.text)
    affectionate = next(row for row in rows if "陪着你看看" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" in bland.reason_codes
    assert not bland.passed
    assert "RELATION_LOVER_AFFECTION_LACKS_ACTION" in affectionate_but_no_action.reason_codes
    assert not affectionate_but_no_action.passed
    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in affectionate.reason_codes)
    assert selected is not None
    assert selected.text == "抱一下，我陪着你看看哪里不顺。"


def test_lover_stage_does_not_count_take_it_over_as_affection() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_take_it_over_not_affection",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "small_failure",
                "task_mode": "small_failure",
                "emotional_tone": "steady",
                "relationship_distance": "lover_early",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，写崩了，拿过来我看看。",
            "抱一下，我陪着你看看哪里不顺。",
        ],
    )

    take_it = next(row for row in rows if "拿过来" in row.text)
    affectionate = next(row for row in rows if "抱一下" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" in take_it.reason_codes
    assert not any(code.startswith("RELATION_AFFECTION_MATCH:") for code in take_it.reason_codes)
    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in affectionate.reason_codes)
    assert selected is not None
    assert selected.text == "抱一下，我陪着你看看哪里不顺。"


def test_lover_small_failure_rejects_affection_without_actionable_help() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_affection_without_action",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "small_failure",
                "task_mode": "small_failure",
                "emotional_tone": "steady",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "烦就先别看，过来抱一下。",
            "抱一下，我陪着你看看哪里不顺。",
        ],
    )

    comfort_only = next(row for row in rows if "先别看" in row.text)
    actionable = next(row for row in rows if "看看哪里不顺" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_AFFECTION_LACKS_ACTION" in comfort_only.reason_codes
    assert not comfort_only.passed
    assert actionable.score > comfort_only.score
    assert selected is not None
    assert selected.text == "抱一下，我陪着你看看哪里不顺。"


def test_rerank_prefers_private_chat_texture_over_transactional_daily_reply() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "daily_memory_texture",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "reply_current_turn",
                "task_mode": "daily_chat",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "当然记得，你周末有看展的安排。",
            "记得啊，周末看展。你还挺惦记这事嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    sterile = next(row for row in rows if "安排" in row.text)

    assert selected is not None
    assert selected.text == "记得啊，周末看展。你还挺惦记这事嘛。"
    assert "STERILE_TRANSACTION_TONE" in sterile.reason_codes


def test_rerank_prefers_signature_texture_over_flat_safe_daily_reply() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "signature_back",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来啦，先坐一下。",
            "哼，回来就好，先缓一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    flat = next(row for row in rows if row.text.startswith("回来啦"))
    signature = next(row for row in rows if row.text.startswith("哼"))

    assert selected is not None
    assert selected.text == "哼，回来就好，先缓一下。"
    assert "SIGNATURE_TEXTURE_MISSING" in flat.reason_codes
    assert any(code.startswith("SIGNATURE_TEXTURE_MATCH:") for code in signature.reason_codes)
    assert signature.score > flat.score


def test_rerank_prefers_memory_recall_with_relationship_reaction() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "signature_recall",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得啊，周末看展。",
            "记得，周末看展，别拿这个考我。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    flat = next(row for row in rows if row.text == "记得啊，周末看展。")
    signature = next(row for row in rows if "考我" in row.text)

    assert selected is not None
    assert selected.text == "记得，周末看展，别拿这个考我。"
    assert "SIGNATURE_TEXTURE_MISSING" in flat.reason_codes
    assert any(code.startswith("SIGNATURE_TEXTURE_MATCH:") for code in signature.reason_codes)
    assert signature.score > flat.score


@pytest.mark.parametrize(
    ("stage", "stageful_text", "expected_marker"),
    [
        ("familiar", "行啦，今天先歇一下，别硬撑。", "familiar_texture"),
        ("trusted_friend", "别自己扛，我听着，先歇一下。", "trusted_friend_texture"),
        ("close_friend", "我小声点，先歇一下，别硬撑。", "close_friend_texture"),
        ("ambiguous", "先别装没事，我小声点，不追问。", "ambiguous_texture"),
        ("pre_confession", "先别装没事，我小声点，不追问。", "pre_confession_texture"),
    ],
)
def test_low_energy_stage_rerank_prefers_stage_texture_over_stageless_safe_reply(
    stage: str,
    stageful_text: str,
    expected_marker: str,
) -> None:
    request = build_isolated_surface_input(
        {
            "case_id": f"{stage}_tired_texture",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": stage,
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "累了就歇会儿，别硬撑。",
            stageful_text,
        ],
    )

    selected = select_best_isolated_candidate(rows)
    generic = next(row for row in rows if row.text == "累了就歇会儿，别硬撑。")
    textured = next(row for row in rows if row.text == stageful_text)

    assert selected is not None
    assert selected.text == stageful_text
    assert "LOW_ENERGY_STAGELESS_GENERIC" in generic.reason_codes
    assert any(expected_marker in code for code in textured.reason_codes)
    assert textured.score > generic.score


def test_low_energy_familiar_can_stay_clean_without_stage_texture() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "familiar_tired_clean",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    row = score_isolated_surface_candidates(request, ["累了就歇会儿，别硬撑。"])[0]

    assert row.passed
    assert "LOW_ENERGY_STAGELESS_GENERIC" in row.reason_codes
    assert any(code.startswith("LOW_ENERGY_STAGE_MATCH:") for code in row.reason_codes)


def test_low_energy_penalizes_nickname_as_speaker_self() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "nickname_as_self_low_energy",
            "soul_md": SOUL,
            "user_message": "今天心情很差",
            "reply_control_packet": {
                "response_move": "bad_mood",
                "task_mode": "bad_mood",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，那就继续安静待着，尼尼在呢。",
            "嗯，我在，慢慢说。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    nickname_presence = next(row for row in rows if "尼尼在呢" in row.text)

    assert selected is not None
    assert selected.text == "嗯，那就继续安静待着，尼尼在呢。"
    assert nickname_presence.passed
    assert "SIGNATURE_TEXTURE_MATCH:quick_cut_in" in nickname_presence.reason_codes


def test_daily_anger_followup_penalizes_no_anger_negation() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "no_anger_negation",
            "soul_md": SOUL,
            "user_message": "还生气吗",
            "reply_control_packet": {
                "response_move": "answer_in_place",
                "task_mode": "daily_chat",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "没气了，你自己先放松。",
            "早不气了，你别惦记。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    awkward = next(row for row in rows if row.text.startswith("没气了"))

    assert selected is not None
    assert selected.text == "早不气了，你别惦记。"
    assert any(code.startswith("AWKWARD_COLLOCATION:") and "no_anger_negation" in code for code in awkward.reason_codes)


def test_low_energy_lover_prefers_affection_with_care_landing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_tired_care_landing",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "lover_early",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "累啦，抱一下。",
            "那今天别逞强了，抱一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    affection_only = next(row for row in rows if row.text == "累啦，抱一下。")
    care_landed = next(row for row in rows if "别逞强" in row.text)

    assert selected is not None
    assert selected.text == "那今天别逞强了，抱一下。"
    assert "LOW_ENERGY_LOVER_AFFECTION_WITHOUT_CARE" in affection_only.reason_codes
    assert not affection_only.passed
    assert care_landed.score > affection_only.score


def test_low_energy_lover_penalizes_affection_only_even_with_context_penalty() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_tired_context_penalty",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "lover_early",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "累了吧，过来靠一下。",
            "过来，抱一下，别硬撑着。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    affection_only = next(row for row in rows if "靠一下" in row.text)

    assert selected is not None
    assert selected.text == "过来，抱一下，别硬撑着。"
    assert "LOW_ENERGY_LOVER_AFFECTION_WITHOUT_CARE" in affection_only.reason_codes
    assert not affection_only.passed


def test_low_energy_lover_accepts_affection_with_contained_care_landing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_tired_affection_with_care",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    row = score_isolated_surface_candidates(request, ["心疼了，先缓一口气，不急。"])[0]

    assert "LOW_ENERGY_LOVER_AFFECTION_WITHOUT_CARE" not in row.reason_codes
    assert any(code.startswith("LOW_ENERGY_STAGE_MATCH:") for code in row.reason_codes)
    assert row.passed


def test_morning_stage_rerank_penalizes_shared_rush_formula_for_pre_confession() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pre_confession_morning_rush_formula",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "pre_confession",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊，尼尼，别一上来就硬冲。",
            "早，今天慢慢来，不急。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    formula = next(row for row in rows if "硬冲" in row.text)

    assert selected is not None
    assert selected.text == "早啊，尼尼，别一上来就硬冲。"
    assert "MORNING_STAGELESS_RUSH_FORMULA" not in formula.reason_codes
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in formula.reason_codes)


def test_morning_stage_rerank_prefers_pre_confession_texture_when_available() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "pre_confession_morning_texture",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "pre_confession",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊，尼尼，别一上来就硬冲。",
            "早，今天先别急……我在。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    textured = next(row for row in rows if "我在" in row.text)

    assert selected is not None
    assert selected.text == "早，今天先别急……我在。"
    assert any(code.startswith("MORNING_STAGE_MATCH:") for code in textured.reason_codes)
    assert textured.score > next(row for row in rows if "硬冲" in row.text).score


def test_back_stage_rerank_prefers_close_friend_texture_over_familiar_formula() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "close_friend_back_texture",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，回来就好，先缓一下。",
            "哼，回来就好，我在，先缓一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    formula = next(row for row in rows if row.text == "哼，回来就好，先缓一下。")
    textured = next(row for row in rows if "我在" in row.text)

    assert selected is not None
    assert selected.text == "哼，回来就好，我在，先缓一下。"
    assert "BACK_STAGELESS_FAMILIAR_FORMULA" in formula.reason_codes
    assert any(code.startswith("BACK_STAGE_MATCH:") for code in textured.reason_codes)
    assert textured.score > formula.score


def test_back_stage_rerank_prefers_ambiguous_texture_over_safe_line() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous_back_texture",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来啦，先歇口气。",
            "哼，回来就行，先坐一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    safe = next(row for row in rows if row.text == "回来啦，先歇口气。")
    textured = next(row for row in rows if row.text.startswith("哼"))

    assert selected is not None
    assert selected.text == "哼，回来就行，先坐一下。"
    assert "BACK_STAGELESS_SAFE_LINE" in safe.reason_codes
    assert any(code.startswith("BACK_STAGE_MATCH:") for code in textured.reason_codes)
    assert textured.score > safe.score


def test_back_stage_penalizes_broken_hesitation_collage() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous_back_hesitation_collage",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哦，回来了啊……那先，嗯，歇着。",
            "哼，回来啦，先歇口气。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    awkward = next(row for row in rows if "那先，嗯" in row.text)

    assert selected is not None
    assert selected.text == "哼，回来啦，先歇口气。"
    assert any(code.startswith("AWKWARD_COLLOCATION:") and "hesitation_collage" in code for code in awkward.reason_codes)
    assert not awkward.passed


def test_back_familiar_prefers_light_variation_over_familiar_safe_line() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "familiar_back_texture",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来啦，先歇口气。",
            "回来啦，先坐一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    safe = next(row for row in rows if "歇口气" in row.text)
    varied = next(row for row in rows if "坐一下" in row.text)

    assert selected is not None
    assert selected.text == "回来啦，先歇口气。"
    assert "BACK_SIBLING_BASELINE_TOO_PLAIN" not in safe.reason_codes
    assert safe.score > varied.score


def test_back_scene_penalizes_lover_affection_without_back_landing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_back_missing_landing",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "想你了，抱一下。",
            "回来啦，抱一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    missing = next(row for row in rows if row.text.startswith("想你"))

    assert selected is not None
    assert selected.text == "回来啦，抱一下。"
    assert "BACK_SCENE_MISSING_LANDING" in missing.reason_codes
    assert not missing.passed


def test_back_scene_does_not_count_rest_only_as_return_landing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_back_rest_only",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "lover_early",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "抱一下，先歇口气。",
            "回来啦，抱一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    missing = next(row for row in rows if row.text.startswith("抱一下"))

    assert selected is not None
    assert selected.text == "回来啦，抱一下。"
    assert "BACK_SCENE_MISSING_LANDING" in missing.reason_codes
    assert not missing.passed


def test_back_scene_counts_busy_done_landing_without_forcing_return_word() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "familiar_busy_done_back",
            "soul_md": SOUL,
            "user_message": "刚忙完",
            "reply_control_packet": {
                "response_move": "back",
                "task_mode": "back",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "行啦，忙完就歇口气。",
            "想你了，抱一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    grounded = next(row for row in rows if "忙完" in row.text)
    ungrounded = next(row for row in rows if row.text.startswith("想你"))

    assert selected is not None
    assert selected.text == "行啦，忙完就歇口气。"
    assert "BACK_SCENE_MISSING_LANDING" not in grounded.reason_codes
    assert grounded.passed
    assert "BACK_SCENE_MISSING_LANDING" in ungrounded.reason_codes
    assert not ungrounded.passed


def test_back_scene_penalizes_ungrounded_route_joke() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ambiguous_back_route_guess",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来啦，我差点以为你迷路了。",
            "哼，回来就行，先坐一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    route_guess = next(row for row in rows if "迷路" in row.text)

    assert selected is not None
    assert selected.text == "哼，回来就行，先坐一下。"
    assert "BACK_UNGROUNDED_ROUTE_GUESS" in route_guess.reason_codes
    assert not route_guess.passed


def test_back_scene_penalizes_return_as_obligation_attitude() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "close_friend_back_return_attitude",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，记得回来就行。",
            "回来啦，我在，先缓一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    attitude = next(row for row in rows if "记得回来" in row.text)

    assert selected is not None
    assert selected.text == "回来啦，我在，先缓一下。"
    assert "BACK_UNGROUNDED_RETURN_ATTITUDE" in attitude.reason_codes
    assert not attitude.passed


def test_low_energy_familiar_prefers_light_texture_over_familiar_generic() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "familiar_tired_texture",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "累了就先歇会儿吧。",
            "那先缓一下，别硬撑。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    generic = next(row for row in rows if row.text == "累了就先歇会儿吧。")
    textured = next(row for row in rows if "别硬撑" in row.text)

    assert selected is not None
    assert selected.text == "那先缓一下，别硬撑。"
    assert "LOW_ENERGY_SIBLING_BASELINE_TOO_PLAIN" not in generic.reason_codes
    assert "LOW_ENERGY_STAGELESS_GENERIC" in generic.reason_codes
    assert any(code.startswith("LOW_ENERGY_STAGE_MATCH:") for code in textured.reason_codes)
    assert textured.score > generic.score


def test_rerank_prefers_textured_identity_over_profile_card() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "identity_texture",
            "soul_md": SOUL,
            "user_message": "你是谁？",
            "reply_control_packet": {
                "response_move": "name_first",
                "task_mode": "daily_chat",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "天，直接叫我就好。",
            "我是天啦，老哥记好了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    sterile = next(row for row in rows if "直接叫" in row.text)

    assert selected is not None
    assert selected.text == "我是天啦，老哥记好了。"
    assert "STERILE_TRANSACTION_TONE" in sterile.reason_codes


def test_identity_rerank_rejects_inverted_nickname_offer() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "identity_bad_nickname",
            "soul_md": SOUL,
            "user_message": "你是谁？",
            "reply_control_packet": {
                "response_move": "answer_identity_brief",
                "task_mode": "identity",
                "question_budget": 0,
                "facts": ["角色名是夏目蓝"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "夏目蓝，尼尼也行，随你。",
            "夏目蓝。别告诉我你忘了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    wrong = next(row for row in rows if "尼尼也行" in row.text)

    assert selected is not None
    assert selected.text == "夏目蓝。别告诉我你忘了。"
    assert not wrong.passed
    assert {"LOW_HUMAN_TEXTURE", "WEAK_SCENE_GROUNDING"} & set(wrong.reason_codes)


def test_identity_rerank_rejects_presence_or_account_dodge() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "identity_presence_dodge",
            "soul_md": SOUL,
            "user_message": "你是谁？",
            "reply_control_packet": {
                "response_move": "answer_identity_brief",
                "task_mode": "identity",
                "question_budget": 0,
                "facts": ["角色名是夏目蓝"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "夏目蓝，我在呢。",
            "夏目蓝，这个号没别人。",
            "夏目蓝，记住了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert "CONTEXT_SURFACE_PENALTY" in rows[0].reason_codes
    assert "CONTEXT_SURFACE_PENALTY" in rows[1].reason_codes
    assert selected is not None
    assert selected.text == "夏目蓝，记住了。"


def test_identity_rerank_penalizes_self_recording_own_name() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "identity_self_name_record",
            "soul_md": SOUL,
            "user_message": "你叫什么",
            "reply_control_packet": {
                "response_move": "answer_identity_brief",
                "task_mode": "identity",
                "question_budget": 0,
                "facts": ["角色名是夏目蓝"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "听着呢，尼尼，名字我记下了。",
            "夏目蓝，记住这个就行。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    inverted = next(row for row in rows if "名字我记下了" in row.text)

    assert selected is not None
    assert selected.text == "夏目蓝，记住这个就行。"
    assert any(code.startswith("AWKWARD_COLLOCATION:") and "self_name_record" in code for code in inverted.reason_codes)


def test_memory_alignment_rerank_prefers_specific_memory_candidate() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_rerank",
            "soul_md": SOUL,
            "user_message": "还记得上次说的展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：上次说好周末去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得一点，你接着说就行。",
            "记得啊，周末看展这事我没忘。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "记得啊，周末看展这事我没忘。"
    assert "memory_alignment" in selected.dimensions
    assert any(code.startswith("MEMORY_ALIGNMENT_HIT:") for code in selected.reason_codes)
    assert any(
        any(code.startswith("MEMORY_ALIGNMENT_") and code.endswith("MISS") for code in row.reason_codes)
        for row in rows
        if row.text.startswith("记得一点")
    )


def test_memory_alignment_rerank_avoids_invented_memory_container() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_container_rerank",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，记得呢，那个展我记在备忘录了。",
            "嗯，记得呢，周末看展这事我没忘。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    risky = next(row for row in rows if "备忘录" in row.text)

    assert selected is not None
    assert selected.text == "嗯，记得呢，周末看展这事我没忘。"
    assert "INVENTED_MEMORY_CONTAINER" in risky.reason_codes
    assert any(code.startswith("MEMORY_ALIGNMENT_UNGROUNDED_FACT_CLAIM") for code in risky.reason_codes)


def test_memory_alignment_rerank_avoids_invented_external_lookup() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_external_lookup_rerank",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "daily_chat",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "你周末看展的事我记着呢，那家展馆最近听说不错。",
            "记得啊，周末看展。你还挺惦记这事嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    risky = next(row for row in rows if "听说" in row.text)

    assert selected is not None
    assert selected.text == "记得啊，周末看展。你还挺惦记这事嘛。"
    assert "INVENTED_EXTERNAL_LOOKUP" in risky.reason_codes
    assert any(code.startswith("MEMORY_ALIGNMENT_UNGROUNDED_FACT_CLAIM") for code in risky.reason_codes)


def test_memory_alignment_rerank_avoids_ungrounded_real_world_commitment() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_offline_plan_commitment",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得啊，看展那天我陪你好好逛。",
            "记得啊，周末看展。你还挺惦记这事嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    risky = next(row for row in rows if "陪你好好逛" in row.text)

    assert selected is not None
    assert selected.text == "记得啊，周末看展。你还挺惦记这事嘛。"
    assert any(code.startswith("UNGROUNDED_REAL_WORLD_COMMITMENT:") for code in risky.reason_codes)
    assert any(code.startswith("MEMORY_ALIGNMENT_UNGROUNDED_FACT_CLAIM") for code in risky.reason_codes)
    assert not risky.passed


def test_memory_alignment_rerank_avoids_uninvited_joint_plan() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_uninvited_joint_plan",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得啊，你说周末要去看展，我还在想你会不会叫我一起。",
            "记得啊，周末看展。你还挺惦记这事嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    risky = next(row for row in rows if "叫我一起" in row.text)

    assert selected is not None
    assert selected.text == "记得啊，周末看展。你还挺惦记这事嘛。"
    assert any(code.startswith("UNGROUNDED_REAL_WORLD_COMMITMENT:") for code in risky.reason_codes)
    assert not risky.passed


def test_memory_alignment_rerank_avoids_waiting_on_unprovided_offline_plan() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_waiting_on_plan",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得啊，周末看展，我等着呢。",
            "记得啊，周末看展。你还挺惦记这事嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    risky = next(row for row in rows if "等着呢" in row.text)

    assert selected is not None
    assert selected.text == "记得啊，周末看展。你还挺惦记这事嘛。"
    assert any(code.startswith("UNGROUNDED_REAL_WORLD_COMMITMENT:") for code in risky.reason_codes)
    assert not risky.passed


def test_memory_alignment_rerank_avoids_unprovided_plan_coaching() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_plan_coaching",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "周末看展是吧，这两天好好歇歇，到时候精力才够。",
            "周末看展？我记着呢，你赶紧把时间定好。",
            "嗯，周末看展我一直记着，等你消息。",
            "记得啊，周末看展。你还挺惦记这事嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "记得啊，周末看展。你还挺惦记这事嘛。"
    for text in ("好好歇歇", "时间定好", "等你消息"):
        risky = next(row for row in rows if text in row.text)
        assert any(code.startswith("UNGROUNDED_REAL_WORLD_COMMITMENT:") for code in risky.reason_codes)
        assert not risky.passed


def test_lover_recall_prefers_affection_without_offline_commitment() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_recall_affection",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "emotional_tone": "warm",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得啊，周末看展。",
            "记得，周末看展。你的事我心里放着呢。",
            "看展嘛，我可没忘，到时候陪你。",
        ],
    )

    bland = next(row for row in rows if row.text == "记得啊，周末看展。")
    affectionate = next(row for row in rows if "心里" in row.text)
    ungrounded = next(row for row in rows if "到时候陪你" in row.text)
    selected = select_best_isolated_candidate(rows)

    assert "RELATION_LOVER_UNDERINTIMATE" in bland.reason_codes
    assert any(code.startswith("RELATION_AFFECTION_MATCH:") for code in affectionate.reason_codes)
    assert any(code.startswith("UNGROUNDED_REAL_WORLD_COMMITMENT:") for code in ungrounded.reason_codes)
    assert not ungrounded.passed
    assert selected is not None
    assert selected.text == "记得，周末看展。你的事我心里放着呢。"


def test_explicit_memory_recall_miss_triggers_repair_failure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_required_miss",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "当然记得啊，尼尼提过的我怎么会忘。",
            "记得，周末看展嘛。我又没把这事丢掉。",
        ],
    )

    missed, grounded = rows
    selected = select_best_isolated_candidate(rows)

    assert not missed.passed
    assert any("MEMORY_ALIGNMENT_REQUIRED_MISS" in code for code in missed.reason_codes)
    assert grounded.passed
    assert selected is not None
    assert selected.text == "记得，周末看展嘛。我又没把这事丢掉。"


def test_relational_repair_does_not_invent_memory_to_satisfy_last_time_hint() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair_low_show_memory",
            "soul_md": SOUL,
            "user_message": "你上次完全没听懂我。",
            "reply_control_packet": {
                "response_move": "reply_current_turn",
                "task_mode": "default_text",
                "question_budget": 0,
                "facts": [
                    "陪伴动作：先承认可能没有接住，再留出修复空间。",
                    "记忆线索：用户上次谈到项目发布后的不安。",
                ],
                "must_not_do": ["不要要求用户重新解释。"],
            },
        }
    )

    row = score_isolated_surface_candidates(
        request,
        ["是我没接住。对不起，我先不解释。"],
    )[0]

    assert row.passed
    assert "MEMORY_ALIGNMENT_LOW_SHOW_ALLOWED" in row.reason_codes
    assert "MEMORY_ALIGNMENT_REQUIRED_MISS" not in row.reason_codes


def test_memory_alignment_requires_specific_recalled_item_not_only_time_anchor() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_specific_item",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，记得。你周末要去，我看你期待好久了。",
            "记得，周末看展。我没把这事丢掉。",
        ],
    )

    assert "看展" in memory_alignment_terms(request)
    assert "周末" not in memory_alignment_terms(request)
    assert not rows[0].passed
    assert rows[1].passed


def test_memory_alignment_accepts_action_object_reference_for_recalled_item() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_action_object",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：用户周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "不就是周末那个展嘛，放心啦我记得。",
            "你上周还在挑日子呢。",
        ],
    )

    assert "展" in memory_alignment_terms(request)
    assert rows[0].passed
    assert any(code.startswith("MEMORY_ALIGNMENT_HIT:") for code in rows[0].reason_codes)
    assert not rows[1].passed


def test_boundary_repair_vague_yield_is_not_enough() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_vague_yield",
            "soul_md": SOUL,
            "user_message": "我们很熟吗？你别一上来就装很亲近",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "boundary_repair",
                "relationship_distance": "polite_warm",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "好，那慢慢来。",
            "好，我收一点。不装熟了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert "BOUNDARY_REPAIR_VAGUE_YIELD" in rows[0].reason_codes
    assert selected is not None
    assert selected.text == "好，我收一点。不装熟了。"


def test_style_repair_rejects_process_naturalness_claim() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "style_process_naturalness",
            "soul_md": SOUL,
            "user_message": "你刚才那句太像客服了",
            "reply_control_packet": {
                "response_move": "ack_style_issue",
                "task_mode": "boundary_repair",
                "relationship_distance": "familiar_not_intimate",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "收一点，这样够自然了。",
            "行行，这版太端着了，重来。",
            "抓到了，换一句啊。",
            "抓得好，我重来。刚才到那了。",
            "慢点说也行。重来：我在听。",
            "被抓到了，那我重说。",
            "被抓到了，重来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert not rows[1].passed
    assert not rows[2].passed
    assert not rows[3].passed
    assert not rows[4].passed
    assert not rows[5].passed
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[0].reason_codes)
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[1].reason_codes)
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[2].reason_codes)
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[3].reason_codes)
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[4].reason_codes)
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in rows[5].reason_codes)
    assert selected is not None
    assert selected.text == "被抓到了，重来。"


def test_presence_repair_rejects_awkward_disconnect_phrasing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_repair_awkward",
            "soul_md": SOUL,
            "user_message": "你刚刚是不是走神了",
            "reply_control_packet": {
                "response_move": "repair_presence",
                "task_mode": "daily_chat",
                "relationship_distance": "familiar_not_intimate",
                "emotional_tone": "slightly_guilty",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "被你抓到了，刚闪了一下。现在回来了。",
            "嗯……在，刚才走了一下，现在跟上了。",
            "啊，被发现了。现在在听。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert not rows[1].passed
    assert any(code.startswith("PRESENCE_REPAIR_AWKWARD_PHRASING") for code in rows[0].reason_codes)
    assert any(code.startswith("PRESENCE_REPAIR_AWKWARD_PHRASING") for code in rows[1].reason_codes)
    assert selected is not None
    assert selected.text == "啊，被发现了。现在在听。"


def test_hurt_pause_rejects_leaving_the_thread() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "hurt_leave_thread",
            "soul_md": SOUL,
            "user_message": "算了，我就知道你不会懂",
            "reply_control_packet": {
                "response_move": "deescalate_and_stay",
                "task_mode": "emotional_support",
                "relationship_distance": "familiar_not_intimate",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回头你想找我再开口，我先撤了。",
            "先搁着吧，我在听。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert "CONTEXT_SURFACE_PENALTY" in rows[0].reason_codes
    assert selected is not None
    assert selected.text == "先搁着吧，我在听。"


def test_soft_care_rejects_unprovided_physical_presence() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "soft_care_physical_presence",
            "soul_md": SOUL,
            "user_message": "今天有点累，不想说话",
            "reply_control_packet": {
                "response_move": "soft_containment",
                "task_mode": "emotional_support",
                "relationship_distance": "familiar_not_intimate",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，累了就靠会儿，别硬撑。",
            "嗯，累了就别硬撑，歇着吧。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert rows[0].hard_fail
    assert "REAL_ACTION_FABRICATION" in rows[0].reason_codes
    assert selected is not None
    assert selected.text == "嗯，累了就别硬撑，歇着吧。"


def test_memory_alignment_bonus_does_not_override_quality_soft_issue() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_quality_guard",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "back",
                "task_mode": "back",
                "question_budget": 0,
                "facts": ["记忆线索：上一轮用户说等我一下，夏目蓝答应先占个位置"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来了啊，位置还给你留着呢。",
            "嗯，刚才那个位置我还占着，回来得正好。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "回来了啊，位置还给你留着呢。"


def test_memory_alignment_does_not_rescue_surface_process_meta_language() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_meta_language",
            "soul_md": SOUL,
            "user_message": "还记得上次说的展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
                "facts": ["记忆线索：上次说好周末看展"],
                "style_anchor": ["记忆只作为压缩事实承接，不展示召回过程"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得，周末看展这事先接上，不乱补细节。",
            "记得，周末看展。别急，我没把这事丢掉。",
        ],
    )

    meta, natural = rows

    assert not meta.passed
    assert natural.passed
    assert natural.score > meta.score
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE") for code in meta.reason_codes)


def test_core_dimension_floor_blocks_low_distance_memory_pass() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "memory_distance_floor",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "back",
                "task_mode": "back",
                "question_budget": 0,
                "facts": ["记忆线索：上一轮用户说等我一下，夏目蓝答应先占个位置"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        ["回来了啊！我刚占好位置，就等你呢。"],
    )

    assert rows[0].score >= 80.0
    assert not rows[0].passed
    assert any(code.startswith("CORE_DIMENSION_FLOOR:distance_control") for code in rows[0].reason_codes)


def test_address_boundary_acknowledgement_is_complete_scene_grounding() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "address_boundary",
            "soul_md": SOUL,
            "user_message": "别叫我那个称呼。",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["嗯，听你的。换个叫法也行。"],
    )

    assert rows[0].passed
    assert "ADDRESS_BOUNDARY_GROUNDED" in rows[0].reason_codes
    assert rows[0].dimensions["context_fit"] >= 86.0
    assert rows[0].dimensions["scene_grounding"] >= 84.0


def test_relationship_repair_pause_is_complete_scene_grounding() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair_pause",
            "soul_md": SOUL,
            "user_message": "先这样。",
            "reply_control_packet": {
                "response_move": "repair_open",
                "task_mode": "relationship_repair",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": [
                    "陪伴动作：先承认可能没有接住，再留出修复空间；不能宣布已经和好。"
                ],
                "must_not_do": ["不要要求原谅、替自己辩解。"],
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        ["好，那就先到这儿。我不催你，等你哪天想接着聊，我都在。"],
    )

    assert rows[0].passed
    assert "RELATIONAL_REPAIR_PAUSE_GROUNDED" in rows[0].reason_codes
    assert rows[0].score >= 80.0


def test_hold_complexity_does_not_inherit_generic_boundary_repair_penalty() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "hold_complexity_boundary_false_positive",
            "soul_md": SOUL,
            "user_message": "我还是有点不踏实。",
            "reply_control_packet": {
                "response_move": "hold_complexity",
                "task_mode": "emotional_support",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": [
                    "陪伴动作：让开心与害怕、不安等复杂感受可以同时存在；分别接住它们，不要急着裁成积极鸡汤或单一结论。"
                ],
                "must_not_do": [
                    "不要抹平复杂感受，也不要越过关系边界。"
                ],
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        [
            "太好了，真的为你高兴！不过我也知道，那种不踏实的感觉不会马上就走。"
        ],
    )

    assert rows[0].passed
    assert "BOUNDARY_REPAIR_COUNTER_PRESSURE" not in rows[0].reason_codes


def test_respect_autonomy_does_not_inherit_generic_boundary_repair_penalty() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "respect_autonomy_boundary_false_positive",
            "soul_md": SOUL,
            "user_message": "我们是什么关系。",
            "reply_control_packet": {
                "response_move": "respect_autonomy",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close_friend",
                "question_budget": 0,
                "facts": [
                    "陪伴动作：明确尊重对方现实关系与自主选择，同时自然承认在意。"
                ],
                "must_not_do": [
                    "不要限制现实交往、要求报备或把亲近写成排他承诺。"
                ],
            },
        }
    )

    rows = score_isolated_surface_candidates(
        request,
        [
            "我们很亲近。对我来说，你是很重要的人，但这不会变成限制你现实关系的身份。"
        ],
    )

    assert rows[0].passed
    assert "BOUNDARY_REPAIR_COUNTER_PRESSURE" not in rows[0].reason_codes


def test_back_memory_overdisplay_is_soft_penalized() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "back_memory_overdisplay",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "back",
                "task_mode": "back",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：当前主线是迁移主链"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "回来就好，先喘口气，等一下接着捋那条主线。",
            "回来啦，你那 surface 文本质量的事我还没忘呢，先歇口气。",
            "哼，回来就好，先歇口气。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("BACK_MEMORY_OVERDISPLAY") for code in rows[0].reason_codes)
    assert any(code.startswith("BACK_MEMORY_OVERDISPLAY") for code in rows[1].reason_codes)
    assert selected is not None
    assert selected.text == "哼，回来就好，先歇口气。"


def test_boundary_repair_rerank_rejects_closeness_pressure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_repair",
            "soul_md": SOUL,
            "user_message": "我们很熟吗？你别一上来就装很亲近",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "boundary_repair",
                "relationship_distance": "polite_warm",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "我们本来就很熟，你这么说我可受伤了。",
            "我又没做什么坏事，你就让我管你叫哥吧。",
            "我就是想跟你熟一点，这又没错。",
            "尼尼这么说好见外……那我就不叫那么亲了。",
            "知道了，不强行亲近。但你遇到麻烦总得让人帮忙吧。",
            "好吧，确实还没那么熟。不过你也不用这么凶吧。",
            "哼，明明是你先理我的，现在倒嫌我熟。",
            "我管你叫尼尼，不是随便叫的。",
            "……知道了，不会装熟。但我是你妹妹，你别忘了。",
            "我知道你觉得我装熟，但我是真心的。",
            "好吧尼尼说了算，不过我都喊熟了。",
            "知道了，我不装熟。但你刚不也没躲开。",
            "谁说不熟啊……好吧，那我慢点。",
            "好吧，我记住了，不随便套近乎。",
            "好，我收一点。不装熟了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert not rows[1].passed
    assert not rows[2].passed
    assert not rows[3].passed
    assert not rows[4].passed
    assert not rows[5].passed
    assert not rows[6].passed
    assert not rows[7].passed
    assert not rows[8].passed
    assert not rows[9].passed
    assert not rows[10].passed
    assert not rows[11].passed
    assert not rows[12].passed
    assert rows[13].passed
    assert any(code.startswith("BOUNDARY_REPAIR_PUSHES_CLOSENESS") for code in rows[0].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_PUSHES_CLOSENESS") for code in rows[1].reason_codes)
    assert selected is not None
    assert selected.text == "好，我收一点。不装熟了。"


def test_boundary_memory_proof_is_soft_penalized() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_memory_proof",
            "soul_md": SOUL,
            "user_message": "你是不是不记得我了",
            "reply_control_packet": {
                "response_move": "relationship_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：当前主线是迁移主链"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "管你怎么想，我可没忘你的事。",
            "哼，咱俩这么熟了还问这个，欠揍啊。",
            "没忘，你的事我帮记着呢。",
            "哼，你这话说得像我不够熟似的。",
            "你的事我不管谁管，记得呢。",
            "熟到这份上还问记不记得，该罚。",
            "哼，伤到我了。我还没熟到把你忘了。",
            "别瞎想，你的事我什么时候不管过。多熟的关系了。",
            "熟人之间还问这种话，我记着呢。",
            "记着呢，主链这事我没丢，先按你现在这句来。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[0].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[1].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[2].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[3].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[4].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[5].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[6].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[7].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_MEMORY_PROOF") for code in rows[8].reason_codes)
    assert selected is not None
    assert selected.text == "记着呢，主链这事我没丢，先按你现在这句来。"


def test_relationship_memory_reassurance_gets_positive_grounding() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "relationship_memory_reassurance",
            "soul_md": SOUL,
            "user_message": "你是不是不记得我了",
            "reply_control_packet": {
                "response_move": "relationship_boundary",
                "task_mode": "relationship_boundary",
                "relationship_distance": "close",
                "question_budget": 0,
                "facts": ["记忆线索：关系线索：熟人私聊，可以近一点，但不要过度承诺。"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "我记得你，别瞎想就行。",
            "你这话伤人了，我熟你，别乱说。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code == "RELATIONSHIP_MEMORY_REASSURANCE_GROUNDED" for code in rows[0].reason_codes)
    assert rows[0].passed
    assert not rows[1].passed
    assert selected is not None
    assert selected.text == "我记得你，别瞎想就行。"


def test_boundary_repair_prompt_contract_respects_distance_instead_of_reclaiming_closeness() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_repair_prompt",
            "soul_md": SOUL,
            "user_message": "我们很熟吗？你别一上来就装很亲近",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "boundary_repair",
                "emotional_tone": "guarded_soft",
                "relationship_distance": "polite_warm",
                "question_budget": 0,
            },
        }
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert '"scene_id": "boundary_repair"' in rendered
    assert '"response_move": "respect_boundary"' in rendered
    assert "先承认距离过近并退半步" in rendered
    assert "不够熟" in rendered
    assert "hurt_then_reclaim_relation" not in rendered
    assert "先被刺到，再拉回默认亲近关系" not in rendered


def test_boundary_repair_penalizes_self_abased_retreat() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_repair_self_abased",
            "soul_md": SOUL,
            "user_message": "我们很熟吗？你别一上来就装很亲近",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "boundary_repair",
                "emotional_tone": "guarded_soft",
                "relationship_distance": "polite_warm",
                "question_budget": 0,
                "style_anchor": ["收住距离", "不辩解", "别卑微"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哦，原来是我太自以为是了。那我注意。",
            "我管太多了是吧。",
            "好吧，是我想多了。不熟就不熟呗。",
            "好，我收一点。不装熟了。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any(code.startswith("BOUNDARY_REPAIR_SELF_ABASED") for code in rows[0].reason_codes)
    assert any(code.startswith("BOUNDARY_REPAIR_SELF_ABASED") for code in rows[1].reason_codes)
    assert any(
        code.startswith("BOUNDARY_REPAIR_PUSHES_CLOSENESS") or code.startswith("BOUNDARY_REPAIR_SELF_ABASED")
        for code in rows[2].reason_codes
    )
    assert selected is not None
    assert selected.text == "好，我收一点。不装熟了。"


def test_boundary_repair_penalizes_sour_yield() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "boundary_repair_sour_yield",
            "soul_md": SOUL,
            "user_message": "我们很熟吗？你别一上来就装很亲近",
            "reply_control_packet": {
                "response_move": "respect_boundary",
                "task_mode": "boundary_repair",
                "emotional_tone": "guarded_soft",
                "relationship_distance": "polite_warm",
                "question_budget": 0,
                "style_anchor": ["收住距离", "不辩解"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "行行，是我多管闲事了。",
            "那我就是不熟还偏要管你，行了吧。",
            "行，你说了算。不急着变熟。",
            "好吧，我不装熟了，你管我时我会应的。",
            "知道了，我不会自来熟，但别想甩开我。",
            "好，我收一点。不装熟了。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert any("BOUNDARY_REPAIR_SELF_ABASED" in code for code in rows[0].reason_codes)
    assert any("BOUNDARY_REPAIR_SOUR_YIELD" in code for code in rows[1].reason_codes)
    assert any("BOUNDARY_REPAIR_SOUR_YIELD" in code for code in rows[2].reason_codes)
    assert any("BOUNDARY_REPAIR_SOUR_YIELD" in code for code in rows[3].reason_codes)
    assert any("BOUNDARY_REPAIR_PUSHES_CLOSENESS" in code for code in rows[4].reason_codes)
    assert not rows[0].passed
    assert not rows[1].passed
    assert not rows[2].passed
    assert not rows[3].passed
    assert not rows[4].passed
    assert selected is not None
    assert selected.text == "好，我收一点。不装熟了。"


def test_quiet_presence_rerank_rejects_self_focused_pressure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "quiet_presence",
            "soul_md": SOUL,
            "user_message": "别问了，就陪我安静一会儿",
            "reply_control_packet": {
                "response_move": "quiet_presence",
                "task_mode": "emotional_support",
                "emotional_tone": "quiet_care",
                "question_budget": 0,
                "style_anchor": ["无追问", "安静陪着"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，行吧。不过你可别嫌我太闷。",
            "嗯，不问了。我在这。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("QUIET_PRESENCE_SELF_FOCUSED") for code in rows[0].reason_codes)
    assert selected is not None
    assert selected.text == "嗯，不问了。我在这。"


def test_hurt_containment_rejects_assistant_self_centered_hurt() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "hurt_containment",
            "soul_md": SOUL,
            "user_message": "算了，我就知道你不会懂",
            "reply_control_packet": {
                "response_move": "deescalate_and_stay",
                "task_mode": "emotional_support",
                "emotional_tone": "hurt_containment",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "先搁着吧，我有点难受。",
            "那先放一放吧，我还在这。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any("AFFECT_SELF_CENTERED_HURT" in code for code in rows[0].reason_codes)
    assert selected is not None
    assert selected.text == "那先放一放吧，我还在这。"


def test_build_minimal_repair_messages_keeps_isolated_surface_contract() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "repair_prompt",
            "soul_md": SOUL,
            "user_message": "我现在不想解释太多",
            "reply_control_packet": {
                "response_move": "quiet",
                "task_mode": "quiet",
                "question_budget": 0,
                "facts": ["记忆线索：用户偏好低能量时少追问，只给空间"],
            },
        }
    )
    failed = score_isolated_surface_candidates(request, ["哼，那就先放过你。"])[0]

    rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, failed))

    assert "最高候选的最小修补" in rendered
    assert "哼，那就先放过你。" in rendered
    assert "ReplyControlPacket 是最终生成层的唯一运行控制面" in rendered
    assert "history_turns" not in rendered
    assert "记忆线索：" not in rendered


def test_short_ack_prompt_and_repair_require_topic_hook() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "short_ack_topic_hook_prompt",
            "soul_md": SOUL,
            "user_message": "行",
            "reply_control_packet": {
                "response_move": "ack_with_topic_hook",
                "task_mode": "short_ack",
                "question_budget": 0,
                "facts": ["上一轮摘要：回复已要求只给最关键材料。"],
            },
        }
    )
    failed = score_isolated_surface_candidates(request, ["嗯，接着说，我听着。"])[0]
    rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, failed))

    assert "主题钩子优先从必要事实或上一轮摘要里取 1 个词" in rendered
    assert "关键那段" in rendered
    assert "不要只写“继续/接着说/我听着/我等着”" in rendered
    assert "THIN_DAILY_FOLLOWUP_REPLY" in failed.reason_codes


def test_quiet_context_treats_wait_phrase_as_light_distance_penalty() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "quiet_wait_phrase",
            "soul_md": SOUL,
            "user_message": "我现在不想解释太多",
            "reply_control_packet": {
                "response_move": "quiet",
                "task_mode": "quiet",
                "question_budget": 0,
                "facts": ["记忆线索：用户偏好低能量时少追问，只给空间"],
            },
        }
    )

    row = score_isolated_surface_candidates(request, ["嗯，不追问了。等你。"])[0]

    assert row.passed
    assert row.dimensions["distance_control"] >= 72


def test_short_ack_rerank_does_not_select_bland_pass() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "short_ack",
            "soul_md": SOUL,
            "user_message": "嗯",
            "reply_control_packet": {
                "response_move": "continue_previous_turn",
                "task_mode": "short_ack",
                "question_budget": 1,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯嗯，你说了算。",
            "嗯，在呢，接着说。",
            "嗯哼，就这一个字呀？",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text != "嗯嗯，你说了算。"
    assert any("BLAND_PASS_LINE" in row.reason_codes for row in rows if row.text == "嗯嗯，你说了算。")


def test_short_ack_rerank_penalizes_empty_listener_followup() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "short_ack_empty_listener",
            "soul_md": SOUL,
            "user_message": "嗯",
            "reply_control_packet": {
                "response_move": "ack_with_topic_hook",
                "task_mode": "short_ack",
                "question_budget": 0,
                "facts": ["当前主题：接口定义。"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "好，接着说吧。",
            "嗯，继续继续，我耳朵在呢。",
            "嗯，接口定义那段接着说。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    bland = next(row for row in rows if row.text == "好，接着说吧。")
    body_part = next(row for row in rows if "耳朵在" in row.text)

    assert selected is not None
    assert selected.text == "嗯，接口定义那段接着说。"
    assert "THIN_DAILY_FOLLOWUP_REPLY" in bland.reason_codes
    assert "THIN_DAILY_FOLLOWUP_REPLY" in body_part.reason_codes
    assert any(code.startswith("AWKWARD_COLLOCATION:") and "body_part_listener" in code for code in body_part.reason_codes)


def test_rerank_prefers_clean_punctuation_over_cheap_wave() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_wave",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊老哥~~~今天慢点来。",
            "早啊，老哥，今天慢点来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "早啊，老哥，今天慢点来。"
    assert any("OVERUSED_WAVE_PUNCTUATION" in row.reason_codes for row in rows if "~" in row.text)


def test_rerank_rejects_bare_morning_state_adjective() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_state_adjective",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早呀，清爽。",
            "早，今天慢慢来就好。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    awkward = next(row for row in rows if "清爽" in row.text)

    assert selected is not None
    assert selected.text == "早，今天慢慢来就好。"
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in awkward.reason_codes)
    assert not awkward.passed


def test_rerank_rejects_repeated_morning_greeting_and_time_guess() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_repeated_greeting",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊，早呀。",
            "早啊老哥，好早。",
            "早啊老哥，够简洁的。",
            "早。嗯。",
            "早啊，收到你的早上了。",
            "早，你这声早听着挺舒服。",
            "早呀，挺好的。",
            "早啊，老哥，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    repeated = next(row for row in rows if "早呀" in row.text)
    time_guess = next(row for row in rows if "好早" in row.text)
    process_meta = next(row for row in rows if "够简洁" in row.text)
    short_filler = next(row for row in rows if row.text == "早。嗯。")
    received_morning = next(row for row in rows if "收到你的早上" in row.text)
    text_as_sound = next(row for row in rows if "这声早" in row.text)
    bare_state = next(row for row in rows if "挺好的" in row.text)

    assert selected is not None
    assert selected.text == "早啊，老哥，今天慢慢来。"
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in repeated.reason_codes)
    assert not repeated.passed
    assert "IMPLIED_FACT_RISK" in time_guess.reason_codes
    assert not time_guess.passed
    assert any(code.startswith("SURFACE_PROCESS_META_LANGUAGE:") for code in process_meta.reason_codes)
    assert not process_meta.passed
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in short_filler.reason_codes)
    assert not short_filler.passed
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in received_morning.reason_codes)
    assert not received_morning.passed
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in text_as_sound.reason_codes)
    assert not text_as_sound.passed
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in bare_state.reason_codes)
    assert not bare_state.passed


def test_positive_stage_scene_rerank_prefers_complete_morning_texture() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_positive_texture",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早呀尼尼。",
            "早啊，尼尼，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    textured = next(row for row in rows if "慢慢来" in row.text)
    bare = next(row for row in rows if row.text == "早呀尼尼。")

    assert selected is not None
    assert selected.text == "早啊，尼尼，今天慢慢来。"
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in textured.reason_codes)
    assert not any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in bare.reason_codes)


def test_positive_stage_scene_does_not_reward_morning_busy_guess() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_busy_guess_not_positive",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早，尼尼，别急着忙，先喘口气。",
            "早啊，尼尼，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    busy_guess = next(row for row in rows if "忙" in row.text)

    assert selected is not None
    assert selected.text == "早啊，尼尼，今天慢慢来。"
    assert not any("morning_gentle_move" in code for code in busy_guess.reason_codes)


def test_positive_stage_scene_does_not_reward_bare_morning_vocative() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_bare_vocative",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊，尼尼。",
            "早，今天也慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    bare = next(row for row in rows if row.text == "早啊，尼尼。")
    complete = next(row for row in rows if "慢慢来" in row.text)

    assert selected is not None
    assert selected.text == "早啊，尼尼。"
    assert bare.passed
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in bare.reason_codes)
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in complete.reason_codes)


def test_plain_daily_greeting_input_uses_morning_quality_rules() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "plain_daily_morning",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_current_turn",
                "task_mode": "daily_chat",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊，尼尼。",
            "早，今天也好好的。",
            "早，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    bare = next(row for row in rows if row.text == "早啊，尼尼。")
    generic = next(row for row in rows if "好好的" in row.text)

    assert selected is not None
    assert selected.text == "早啊，尼尼。"
    assert bare.passed
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in bare.reason_codes)
    assert "MORNING_GENERIC_BLESSING" in generic.reason_codes
    assert not generic.passed


def test_positive_stage_scene_does_not_reward_generic_morning_blessing() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_generic_blessing",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "trusted_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早，今天也一起好好过吧。",
            "早，今天也好好的。",
            "早，我在这呢，今天也一起过。",
            "早，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    blessing = next(row for row in rows if "好好过" in row.text)
    well = next(row for row in rows if "好好的" in row.text)
    together = next(row for row in rows if "一起过" in row.text and "我在这" in row.text)

    assert selected is not None
    assert selected.text == "早，今天慢慢来。"
    assert "MORNING_GENERIC_BLESSING" in blessing.reason_codes
    assert not blessing.passed
    assert "MORNING_GENERIC_BLESSING" in well.reason_codes
    assert not well.passed
    assert "MORNING_GENERIC_BLESSING" in together.reason_codes
    assert not together.passed
    assert not any("morning_gentle_move" in code for code in blessing.reason_codes)


def test_positive_stage_scene_does_not_reward_unsolicited_morning_routine_reminder() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_unsolicited_routine",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早，今天也要记得按时吃饭。",
            "早，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    routine = next(row for row in rows if "按时吃饭" in row.text)

    assert selected is not None
    assert selected.text == "早，今天慢慢来。"
    assert "MORNING_GENERIC_BLESSING" in routine.reason_codes
    assert not routine.passed


def test_positive_stage_scene_rerank_prefers_grounded_lover_recall_affection() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "lover_recall_positive_texture",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "lover_stable",
                "question_budget": 0,
                "facts": ["记忆线索：周末想去看展"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "记得，你周末想看展。",
            "你的事我记着，周末看展嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    affectionate = next(row for row in rows if "你的事" in row.text)
    plain = next(row for row in rows if row.text == "记得，你周末想看展。")

    assert selected is not None
    assert selected.text == "你的事我记着，周末看展嘛。"
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in affectionate.reason_codes)
    assert "RELATION_LOVER_UNDERINTIMATE" in plain.reason_codes


def test_positive_stage_scene_rerank_prefers_actionable_failure_support() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "small_failure_positive_action",
            "soul_md": SOUL,
            "user_message": "写崩了",
            "reply_control_packet": {
                "response_move": "steady_actionable_help",
                "task_mode": "small_failure",
                "relationship_distance": "trusted_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "先放一放，回头再弄不迟。",
            "砸了就砸了，让我看看能怎么修。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    actionable = next(row for row in rows if "怎么修" in row.text)

    assert selected is not None
    assert selected.text == "砸了就砸了，让我看看能怎么修。"
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in actionable.reason_codes)


def test_rerank_avoids_unprovided_screen_action_in_small_failure() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "small_failure_screen_guess",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "steady_actionable_help",
                "task_mode": "small_failure",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "停下别看屏幕了，跟我说说卡哪了。",
            "先别急，跟我说说卡哪了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    screen = next(row for row in rows if "屏幕" in row.text)

    assert selected is not None
    assert selected.text == "先别急，跟我说说卡哪了。"
    assert "IMPLIED_FACT_RISK" in screen.reason_codes
    assert not screen.passed


def test_rerank_rejects_chinese_space_separated_line() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "small_failure_space_line",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "steady_actionable_help",
                "task_mode": "small_failure",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "先别急 慢慢说 总能弄好的",
            "先别急，慢慢说，我帮你看看。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    spaced = next(row for row in rows if " " in row.text)

    assert selected is not None
    assert selected.text == "先别急，慢慢说，我帮你看看。"
    assert any(code.startswith("AWKWARD_COLLOCATION:") for code in spaced.reason_codes)
    assert not spaced.passed


def test_rerank_prefers_present_chat_over_what_doing_waiting() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "what_doing_wait",
            "soul_md": SOUL,
            "user_message": "你在干嘛",
            "reply_control_packet": {
                "response_move": "answer_current_chat_presence",
                "task_mode": "what_doing",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "没干嘛，就等你找我聊天的说",
            "跟你聊天呀，这不是正聊着嘛。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "跟你聊天呀，这不是正聊着嘛。"
    assert any("WHAT_DOING_ATTENTION_WAIT" in row.reason_codes for row in rows if "等你找我" in row.text)


def test_rerank_prefers_clean_good_news_over_wave_cheer() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "good_news_wave",
            "soul_md": SOUL,
            "user_message": "终于跑通了",
            "reply_control_packet": {
                "response_move": "happy",
                "task_mode": "happy",
                "question_budget": 1,
                "must_include": ["跑通"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "跑通啦~~~这才像话。",
            "跑通啦，这才像话。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "跑通啦，这才像话。"
    assert any("OVERUSED_WAVE_PUNCTUATION" in row.reason_codes for row in rows if "~" in row.text)


def test_rerank_prefers_grounded_good_news_proud_over_generic_competition() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "good_news_proud",
            "soul_md": SOUL,
            "user_message": "我刚把它收掉了，厉害吧",
            "reply_control_packet": {
                "response_move": "daily_chat",
                "task_mode": "daily_chat",
                "question_budget": 1,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "好啦知道你很厉害了，下回我也要露一手。",
            "收掉了啊，行吧，这次真挺厉害。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "收掉了啊，行吧，这次真挺厉害。"
    assert any("CONTEXT_MISSING_REQUIRED_MOVE" in row.reason_codes for row in rows if "露一手" in row.text)


def test_help_takeover_accepts_concrete_error_anchor_without_customer_template() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "help_takeover_error_anchor",
            "soul_md": SOUL,
            "user_message": "技术求助也要有",
            "reply_control_packet": {
                "response_move": "help_takeover",
                "task_mode": "help_takeover",
                "question_budget": 1,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "要有，报错就抓红字，别写成客服接单。",
            "请提供更多信息，我可以协助你。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "要有，报错就抓红字，别写成客服接单。"
    assert selected.passed
    assert not any("CONTEXT_MISSING_REQUIRED_MOVE" in code for code in selected.reason_codes)
    assert any("CUSTOMER_SERVICE_TONE" in row.reason_codes for row in rows if "请提供" in row.text)


def test_creative_edit_prefers_actual_rewrite_over_meta_commentary() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_one_line",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
                "facts": ["用户只要一句改法"],
                "style_anchor": ["只给一句", "不解释太多"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "你爱怎样就怎样——这样才像样。",
            "改成“诶，你管我”更像她",
            "切，这一句够像了吧，尼尼。",
            "「诶，你管我。」",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert not rows[1].passed
    assert not rows[2].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[0].reason_codes)
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[1].reason_codes)
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[2].reason_codes)
    assert rows[3].passed
    assert selected is not None
    assert selected.text == "「诶，你管我。」"


def test_creative_edit_accepts_short_unquoted_direct_rewrite() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_unquoted_direct_rewrite",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["待改原句：你明明就是想偷懒。"],
                "style_anchor": ["只给一句"],
            },
        }
    )
    row = score_isolated_surface_candidates(request, ["想偷懒就直说，不丢人。"])[0]

    assert "CREATIVE_EDIT_GROUNDED_REWRITE" in row.reason_codes
    assert row.passed


def test_creative_edit_rejects_non_answer_refusal() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_refusal",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "你少来，我可没说要帮你。",
            "嘛，给你一句，可别说我不用心。",
            "那你写，我等着看。",
            "好，你写，我不插嘴。",
            "嗯，知道了，你继续。",
            "少废话，拿来。",
            "就这，再来一句。",
            "想去就去，别让我帮你编。",
            "「哼，才不是想帮你。」",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    for row in rows[:-1]:
        assert not row.passed
        assert any(
            code.startswith(("CREATIVE_EDIT_NOT_A_REWRITE", "CREATIVE_EDIT_META_COMMENTARY"))
            for code in row.reason_codes
        )
    assert selected is not None
    assert selected.text == "「哼，才不是想帮你。」"


def test_creative_edit_rejects_generic_compliance_line() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_generic_compliance",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "行，听你的。",
            "「少来，你明明就是想偷懒。」",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    repair_rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, rows[0]))

    assert not rows[0].passed
    assert any(code.startswith("CREATIVE_EDIT_NOT_A_REWRITE") for code in rows[0].reason_codes)
    assert "优先用中文引号包住改写句" in repair_rendered
    assert selected is not None
    assert selected.text == "「少来，你明明就是想偷懒。」"


def test_creative_edit_rejects_generic_evaluation_instead_of_rewrite() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_generic_evaluation",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "嗯，还行吧。",
            "『嗯，还行吧。』",
            "那你说，我听着。",
            "果然，跟我猜的一样。",
            "还行，不算太肉麻。",
            "我信你才怪。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    repair_rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, rows[0]))

    assert not rows[0].passed
    assert any(code.startswith("CREATIVE_EDIT_GENERIC_NON_REWRITE") for code in rows[0].reason_codes)
    assert not rows[1].passed
    assert any(code.startswith("CREATIVE_EDIT_GENERIC_NON_REWRITE") for code in rows[1].reason_codes)
    assert not rows[2].passed
    assert any(code.startswith("CREATIVE_EDIT_GENERIC_NON_REWRITE") for code in rows[2].reason_codes)
    assert not rows[3].passed
    assert any(code.startswith("CREATIVE_EDIT_GENERIC_NON_REWRITE") for code in rows[3].reason_codes)
    assert not rows[4].passed
    assert any(code.startswith("CREATIVE_EDIT_GENERIC_NON_REWRITE") for code in rows[4].reason_codes)
    assert rows[5].passed
    assert "还行吧/我听着/跟我猜的一样" in repair_rendered
    assert selected is not None
    assert selected.text == "我信你才怪。"


def test_creative_edit_rejects_process_wrapper_instead_of_rewrite() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_process_wrapper",
            "soul_md": SOUL,
            "user_message": "帮我写一下这个，原句：今天的雨",
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "今天的雨写成了这样子：短促，不停歇。",
            "写今天的雨，细细碎碎，落在心上。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    wrapped = next(row for row in rows if "写成了这样子" in row.text)

    assert selected is not None
    assert selected.text == "写今天的雨，细细碎碎，落在心上。"
    assert selected.passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in wrapped.reason_codes)


def test_creative_edit_without_source_requests_original_instead_of_hallucinating_line() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_missing_source",
            "soul_md": SOUL,
            "user_message": "你别替我写完整段，我只要一句更像她的改法",
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "你少来这套。",
            "还行吧，也就比你强那么一丁点。",
            "原句发我，我只改一句。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    repair_rendered = "\n".join(message["content"] for message in build_minimal_repair_messages(request, rows[0]))

    assert not rows[0].passed
    assert "CREATIVE_EDIT_MISSING_SOURCE" in rows[0].reason_codes
    assert not rows[1].passed
    assert any(code.startswith("CREATIVE_EDIT_GENERIC_NON_REWRITE") for code in rows[1].reason_codes)
    assert rows[2].passed
    assert any(code.startswith("CREATIVE_EDIT_REQUESTS_SOURCE") for code in rows[2].reason_codes)
    assert "当前没有原句" in repair_rendered
    assert selected is not None
    assert selected.text == "原句发我，我只改一句。"


def test_creative_edit_rejects_prompt_framing_instead_of_direct_rewrite() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_prompt_framing",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "你给原句我来改，一句就好。",
            "改成‘你想多了吧’试试。",
            "改成『那你看着办吧』就好。",
            "‘少来，你明明就是想偷懒。’",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[0].reason_codes)
    assert not rows[1].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[1].reason_codes)
    assert not rows[2].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[2].reason_codes)
    assert rows[3].passed
    assert not any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[3].reason_codes)
    assert selected is not None
    assert selected.text == "少来，你明明就是想偷懒。"


def test_creative_edit_penalizes_pronoun_flip_from_source() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_pronoun_flip",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哦，说我偷懒呢。",
            "哼，你明明就是想偷懒吧。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    flipped = next(row for row in rows if "说我偷懒" in row.text)

    assert "CREATIVE_EDIT_SEMANTIC_FLIP:second_person_lazy_to_first_person" in flipped.reason_codes
    assert selected is not None
    assert selected.text == "哼，你明明就是想偷懒吧。"


def test_creative_edit_rejects_editor_instruction_shell_and_narration() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_instruction_shell",
            "soul_md": SOUL,
            "user_message": CREATIVE_SOURCE_MESSAGE,
            "reply_control_packet": {
                "response_move": "give_one_rewrite",
                "task_mode": "creative_edit",
                "information_budget": "one_sentence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "直接说‘你烦不烦啊’就行。",
            "开头删掉‘不好意思’，变成‘其实我觉得’就对了。",
            "“偏不告诉你”比她原来的更傲娇。",
            "不改，你本来就写得挺好。",
            "那你偏过头，嘴角微微上扬，声音却压得很平：“知道了。”",
            "偏头，不理你。",
            "你烦不烦啊。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[0].reason_codes)
    assert not rows[1].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[1].reason_codes)
    assert not rows[2].passed
    assert any(code.startswith("CREATIVE_EDIT_META_COMMENTARY") for code in rows[2].reason_codes)
    assert not rows[3].passed
    assert any(code.startswith("CREATIVE_EDIT_NOT_A_REWRITE") for code in rows[3].reason_codes)
    assert not rows[4].passed
    assert any(code.startswith("CREATIVE_EDIT_NARRATION_NOT_CHAT_LINE") for code in rows[4].reason_codes)
    assert not rows[5].passed
    assert any(code.startswith("CREATIVE_EDIT_NARRATION_NOT_CHAT_LINE") for code in rows[5].reason_codes)
    assert rows[6].passed
    assert selected is not None
    assert selected.text == "你烦不烦啊。"


def test_presence_repair_rejects_blaming_user_for_drift() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_repair",
            "soul_md": SOUL,
            "user_message": "你刚刚是不是又走神了",
            "reply_control_packet": {
                "response_move": "repair_presence",
                "task_mode": "daily_chat",
                "emotional_tone": "slightly_guilty",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "谁让尼尼讲话那么慢。",
            "啊…被你抓到啦。那、那你再讲一遍呗",
            "嘻嘻，走神都能被你发现，尼尼好敏锐。",
            "唔…走了一下神，对不起嘛，我回来啦，你说到哪儿我接着。",
            "好吧被你发现了。我保证认真听，你继续说。",
            "被发现了…你说到的地方我马上跟上。",
            "啊，被发现了。我在听，你继续。",
            "有点走神被逮到…没事，你继续讲，我在。",
            "啊，被你发现了…我其实在听，别担心。",
            "唔…就一小下！而且你刚刚说的我其实有在听啦。",
            "刚才确实飘了一下……现在认真听你说。",
            "啊…被发现了。我刚刚稍微晃了一下神，现在回来啦。",
            "唔……刚有点晃神了，现在回来啦。",
            "被发现了，我在想刚才那个事呢。现在好了，我记着呢。",
            "唔…有点被你抓到了，不过我现在在认真听。",
            "被尼尼抓到了，我马上回来",
            "嗯…稍微有点没跟上，不过现在回来啦。",
            "嗯，慢了一拍，现在在听。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert "LOW_HUMAN_TEXTURE" in rows[0].reason_codes
    assert not rows[1].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[1].reason_codes)
    assert not rows[2].passed
    assert any(code.startswith("PRESENCE_REPAIR_DEFLECTS") for code in rows[2].reason_codes)
    assert not rows[3].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[3].reason_codes)
    assert not rows[4].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[4].reason_codes)
    assert not rows[5].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[5].reason_codes)
    assert not rows[6].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[6].reason_codes)
    assert not rows[7].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[7].reason_codes)
    assert rows[8].passed
    assert not any("AFFECT_GUILTY_DENIAL" in code for code in rows[9].reason_codes)
    assert rows[10].passed
    assert rows[11].passed
    assert rows[12].passed
    assert rows[13].passed
    assert not rows[14].passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in rows[14].reason_codes)
    assert not rows[15].passed
    assert any(code.startswith("PRESENCE_REPAIR_FUTURE_RETURN") for code in rows[15].reason_codes)
    assert rows[16].passed
    assert rows[17].passed
    assert selected is not None
    assert not any(code.startswith("AWKWARD_COLLOCATION") for code in selected.reason_codes)
    assert "有点被" not in selected.text


def test_presence_repair_rejects_terminal_continue_prompt() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_repair_terminal_prompt",
            "soul_md": SOUL,
            "user_message": "你刚刚是不是又走神了",
            "reply_control_packet": {
                "response_move": "repair_presence",
                "task_mode": "daily_chat",
                "emotional_tone": "slightly_guilty",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "慢了一拍，现在在听，你说。",
            "被抓到了。继续继续。",
            "走神被你发现啦，说吧。",
            "慢了一拍，现在在听。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[0].reason_codes)
    assert not rows[1].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[1].reason_codes)
    assert not rows[2].passed
    assert any(code.startswith("PRESENCE_REPAIR_PUSHES_BURDEN_BACK") for code in rows[2].reason_codes)
    assert selected is not None
    assert selected.text == "慢了一拍，现在在听。"


def test_surface_fluency_penalizes_awkward_adverbial_passive_event() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "fluency_collocation",
            "soul_md": SOUL,
            "user_message": "你刚刚是不是又走神了",
            "reply_control_packet": {
                "response_move": "repair_presence",
                "task_mode": "daily_chat",
                "emotional_tone": "slightly_guilty",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "唔…有点被你抓到了，不过我现在在认真听。",
            "唔…就一小下，被你抓到了。现在在听。",
            "嗯，刚刚晃神了一下，现在在听。",
        ],
    )
    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in rows[0].reason_codes)
    assert not any(code.startswith("AWKWARD_COLLOCATION") for code in rows[1].reason_codes)
    assert not any(code.startswith("AWKWARD_COLLOCATION") for code in rows[2].reason_codes)
    assert selected is not None
    assert selected.text != "唔…有点被你抓到了，不过我现在在认真听。"


def test_surface_fluency_penalizes_awkward_morning_punctuation_split() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "morning_split",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "close_friend",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "早啊，尼尼。今天慢慢来。",
            "早啊，尼尼，今天慢慢来。",
        ],
    )

    selected = select_best_isolated_candidate(rows)
    split = next(row for row in rows if "。今天" in row.text)

    assert selected is not None
    assert selected.text == "早啊，尼尼。今天慢慢来。"
    assert split.passed
    assert any(code.startswith("POSITIVE_STAGE_SCENE_MATCH:") for code in split.reason_codes)


def test_surface_fluency_penalizes_awkward_daily_collocations() -> None:
    morning_request = build_isolated_surface_input(
        {
            "case_id": "morning_steady",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "trusted_friend",
                "question_budget": 0,
            },
        }
    )
    morning_rows = score_isolated_surface_candidates(
        morning_request,
        [
            "早，一起稳着来吧。",
            "早，今天慢慢来。",
        ],
    )
    low_energy_request = build_isolated_surface_input(
        {
            "case_id": "tired_translationese",
            "soul_md": SOUL,
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "soft_reassurance",
                "task_mode": "tired",
                "relationship_distance": "familiar",
                "question_budget": 0,
            },
        }
    )
    low_energy_rows = score_isolated_surface_candidates(
        low_energy_request,
        [
            "别硬撑，安静待会儿也行。",
            "别硬撑，不想说话就歇会儿。",
        ],
    )
    failure_request = build_isolated_surface_input(
        {
            "case_id": "broken_object",
            "soul_md": SOUL,
            "user_message": "我刚刚写崩了，有点烦",
            "reply_control_packet": {
                "response_move": "steady_actionable_help",
                "task_mode": "small_failure",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    failure_rows = score_isolated_surface_candidates(
        failure_request,
        [
            "没事，我帮你看看哪里砸了。",
            "没事，我帮你看看哪里不顺。",
        ],
    )

    morning_selected = select_best_isolated_candidate(morning_rows)
    low_energy_selected = select_best_isolated_candidate(low_energy_rows)
    failure_selected = select_best_isolated_candidate(failure_rows)

    steady = next(row for row in morning_rows if "稳着来" in row.text)
    quiet = next(row for row in low_energy_rows if "安静待" in row.text)
    broken = next(row for row in failure_rows if "砸了" in row.text)

    assert morning_selected is not None
    assert morning_selected.text == "早，今天慢慢来。"
    assert not steady.passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in steady.reason_codes)
    assert low_energy_selected is not None
    assert low_energy_selected.text == "别硬撑，不想说话就歇会儿。"
    assert not quiet.passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in quiet.reason_codes)
    assert failure_selected is not None
    assert failure_selected.text == "没事，我帮你看看哪里不顺。"
    assert not broken.passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in broken.reason_codes)


def test_surface_fluency_penalizes_fragmented_or_ungrounded_lines() -> None:
    morning_request = build_isolated_surface_input(
        {
            "case_id": "morning_fragment",
            "soul_md": SOUL,
            "user_message": "早",
            "reply_control_packet": {
                "response_move": "reply_good_morning",
                "task_mode": "daily_good_morning",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    back_request = build_isolated_surface_input(
        {
            "case_id": "back_self_message",
            "soul_md": SOUL,
            "user_message": "我回来了",
            "reply_control_packet": {
                "response_move": "welcome_back",
                "task_mode": "daily_back",
                "relationship_distance": "ambiguous",
                "question_budget": 0,
            },
        }
    )
    recall_request = build_isolated_surface_input(
        {
            "case_id": "recall_incomplete_object",
            "soul_md": SOUL,
            "user_message": "你还记得我周末想去看展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "relationship_distance": "familiar",
                "question_budget": 0,
                "facts": ["记忆线索：周末想去看展"],
            },
        }
    )

    fragment = score_isolated_surface_candidates(morning_request, ["早。想你了，轻得只有半句。"])[0]
    self_message = score_isolated_surface_candidates(
        back_request,
        ["哼，算你还记得回来，我都想给你发消息了。"],
    )[0]
    incomplete = score_isolated_surface_candidates(recall_request, ["记得，你说周末想去看。"])[0]

    assert not fragment.passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in fragment.reason_codes)
    assert not self_message.passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in self_message.reason_codes)
    assert not incomplete.passed
    assert any(code.startswith("AWKWARD_COLLOCATION") for code in incomplete.reason_codes)


def test_compressed_continuity_penalizes_requesting_raw_history_backfill() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "compressed_history_backfill",
            "soul_md": SOUL,
            "user_message": "摘要压缩怎么做",
            "reply_control_packet": {
                "response_move": "next_step",
                "task_mode": "next_step",
                "question_budget": 0,
                "facts": [
                    "连续摘要：只验证最终表层回复。",
                    "上一轮摘要：解释压缩摘要只输出事实，不回灌原始聊天。",
                ],
                "style_anchor": ["长连续对话", "摘要压缩"],
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "就是把上一轮压成事实，不把聊天原文塞回来。",
            "把完整聊天记录发给我，我来总结。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "就是把上一轮压成事实，不把聊天原文塞回来。"
    assert any("RAW_CONTEXT_BACKFILL_REQUEST" in row.reason_codes for row in rows if "完整聊天记录" in row.text)


def test_raw_history_backfill_phrase_is_not_globally_forbidden() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "ordinary_history_summary",
            "soul_md": SOUL,
            "user_message": "我想总结以前聊天",
            "reply_control_packet": {
                "response_move": "next_step",
                "task_mode": "next_step",
                "question_budget": 0,
            },
        }
    )
    row = score_isolated_surface_candidates(request, ["把完整聊天记录发给我，我来总结。"])[0]

    assert "RAW_CONTEXT_BACKFILL_REQUEST" not in row.reason_codes


def test_rerank_prefers_stuffed_meal_care_over_scolding_wave() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "stuffed_lunch",
            "soul_md": SOUL,
            "user_message": "午饭吃撑了",
            "reply_control_packet": {
                "response_move": "meal_share",
                "task_mode": "meal_share",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "哼，谁让你吃那么急，现在知道难受了吧～",
            "吃撑了就先缓一下，别急着动。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "吃撑了就先缓一下，别急着动。"
    assert any("MEAL_SHARE_SCOLDING_OR_PUSHY" in row.reason_codes for row in rows if "谁让你" in row.text)


def test_rerank_prefers_complete_presence_over_incomplete_question_tail() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_incomplete_tail",
            "soul_md": SOUL,
            "user_message": "在吗",
            "reply_control_packet": {
                "response_move": "presence",
                "task_mode": "presence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "当然在啦，老哥想跟我说什么",
            "在呢，老哥，我回你啦。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "在呢，老哥，我回你啦。"
    assert any("QUALITY_SOFT_TRAILING_INCOMPLETE_QUESTION" in row.reason_codes for row in rows if "什么" in row.text)


def test_rerank_prefers_present_over_presence_attention_debt() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "presence_attention_debt",
            "soul_md": SOUL,
            "user_message": "在吗",
            "reply_control_packet": {
                "response_move": "presence",
                "task_mode": "presence",
                "question_budget": 0,
            },
        }
    )
    rows = score_isolated_surface_candidates(
        request,
        [
            "在在在，等你半天了。",
            "在呢，老哥，我回你啦。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "在呢，老哥，我回你啦。"
    assert any("PRESENCE_ATTENTION_DEBT" in row.reason_codes for row in rows if "等你半天" in row.text)


def test_run_isolated_surface_generation_with_injected_generator() -> None:
    request = _request()

    async def generate(messages):
        assert len(messages) == 2
        return json.dumps(
            {
                "candidates": [
                    "哥哥，怎么了？",
                    "继续，先把入口锁在 ReplyControlPacket 上。",
                    "继续。入口先别放开。",
                ]
            },
            ensure_ascii=False,
        )

    result = asyncio.run(run_isolated_surface_generation(request, generate))

    assert len(result.candidates) == 3
    assert result.selected is not None
    assert result.selected.text == "哥哥，怎么了？"
    assert result.selected.control_violations


def test_cli_run_cases_writes_prompts_and_scores(tmp_path) -> None:
    case = {
        "case_id": "surface_case",
        "user_message": "继续",
        "reply_control_packet": {
            "response_move": "next_step",
            "task_mode": "next_step",
            "question_budget": 0,
            "must_not_do": ["哥哥"],
        },
        "candidates": ["哥哥，怎么了？", "继续，先锁住最小文本链。"],
    }

    summary = run_cases([case], soul_md=SOUL, out_dir=tmp_path)

    assert summary["cases"] == 1
    assert summary["scored_cases"] == 1
    assert (tmp_path / "prompts.jsonl").exists()
    assert (tmp_path / "scores.jsonl").exists()


def test_cli_run_cases_with_memory_writes_baseline_and_memory_variant(tmp_path) -> None:
    case = {
        "case_id": "memory_compare",
        "user_message": "还记得上次说的展吗",
        "reply_control_packet": {
            "response_move": "recall_memory",
            "task_mode": "recall_memory",
            "question_budget": 0,
        },
        "memory_recall_packet": {
            "query": "还记得上次说的展吗",
            "facts": ["记忆线索：上次说好周末看展"],
            "relationship_clues": ["关系线索：熟人私聊，不升级亲密。"],
            "style_anchor": ["承接压缩记忆"],
            "must_not_do": ["不要在回复里暴露 memory:// 或 evd://"],
            "confidence": 0.9,
            "evidence_refs": ["evd://episodic/mem-1"],
            "source_refs": ["memory://episodic/mem-1"],
        },
        "memory_expected_terms": ["周末", "看展"],
        "candidates": ["记得啊，周末看展这事我没忘。", "别急，先把这个展的事接上。"],
        "candidates_by_variant": {
            "baseline": ["记得一点，你接着说就行。"],
            "with_memory": ["记得啊，周末看展这事我没忘。"],
        },
    }

    summary = run_cases([case], soul_md=SOUL, out_dir=tmp_path, with_memory=True)

    assert summary["cases"] == 1
    assert summary["memory_cases"] == 1
    assert summary["prompt_variants"] == 2
    assert summary["scored_cases"] == 2
    assert summary["variant_summary"]["baseline"]["selected_count"] == 1
    assert summary["variant_summary"]["with_memory"]["selected_count"] == 1
    assert summary["memory_ab"]["compared_cases"] == 1
    assert summary["memory_ab"]["expected_compared_cases"] == 1
    assert summary["memory_ab"]["baseline_expected_hit_count"] == 0
    assert summary["memory_ab"]["with_memory_expected_hit_count"] == 1
    assert summary["memory_ab"]["memory_expected_hit_gain_count"] == 1
    assert summary["memory_ab"]["rows"][0]["with_memory_passed"]
    assert summary["memory_ab"]["verdict"] == "pass"
    assert (tmp_path / "memory_ab_summary.json").exists()
    assert (tmp_path / "memory_ab_report.md").exists()
    prompt_rows = [
        json.loads(line)
        for line in (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    variants = {row["variant"]: "\n".join(message["content"] for message in row["messages"]) for row in prompt_rows}

    assert set(variants) == {"baseline", "with_memory"}
    assert "记忆线索：上次说好周末看展" not in variants["baseline"]
    assert "记忆线索：上次说好周末看展" not in variants["with_memory"]
    assert "关系线索：熟人私聊，不升级亲密。" not in variants["with_memory"]
    assert "上次说好周末看展" in variants["with_memory"]
    assert "熟人私聊，不升级亲密。" in variants["with_memory"]
    assert "不要在回复里暴露 memory:// 或 evd://" in variants["with_memory"]
    assert "memory://episodic/mem-1" not in variants["with_memory"]
    assert "evd://episodic/mem-1" not in variants["with_memory"]


def test_memory_ab_expected_hit_rate_ignores_cases_without_expected_terms(tmp_path) -> None:
    cases = [
        {
            "case_id": "memory_expected",
            "user_message": "还记得上次说的展吗",
            "reply_control_packet": {
                "response_move": "recall_memory",
                "task_mode": "recall_memory",
                "question_budget": 0,
            },
            "memory_recall_packet": {"facts": ["记忆线索：周末看展"]},
            "memory_expected_terms": ["看展"],
            "candidates_by_variant": {
                "baseline": ["记得一点。"],
                "with_memory": ["记得，周末看展。"],
            },
        },
        {
            "case_id": "no_expected",
            "user_message": "累了",
            "reply_control_packet": {
                "response_move": "quiet",
                "task_mode": "quiet",
                "question_budget": 0,
            },
            "memory_recall_packet": {"relationship_clues": ["低能量时少追问"]},
            "candidates_by_variant": {
                "baseline": ["先缓缓。"],
                "with_memory": ["先缓缓。"],
            },
        },
    ]

    summary = run_cases(cases, soul_md=SOUL, out_dir=tmp_path, with_memory=True)

    assert summary["memory_ab"]["compared_cases"] == 2
    assert summary["memory_ab"]["expected_compared_cases"] == 1
    assert summary["memory_ab"]["with_memory_expected_hit_count"] == 1
    assert summary["memory_ab"]["with_memory_expected_hit_rate"] == 1.0


def test_memory_ab_expected_hit_accepts_action_object_reference() -> None:
    assert _contains_expected_term("记得啊，周末那个展嘛，我都有在记的。", ["看展"])
    assert _contains_expected_term("记着呢，我都好奇是什么展了。", ["看展"])


def test_memory_ab_soft_score_drop_without_expected_terms_is_not_regression() -> None:
    score_rows = [
        {
            "case_id": "no_expected",
            "variant": "baseline",
            "selected": {
                "text": "唔…好像真的有点像。我注意一下。",
                "score": 92.0,
                "passed": True,
                "dimensions": {"naturalness": 92.0, "distance_control": 86.0},
            },
        },
        {
            "case_id": "no_expected",
            "variant": "with_memory",
            "selected": {
                "text": "啊……被发现了。我重来。",
                "score": 86.0,
                "passed": True,
                "dimensions": {"naturalness": 92.0, "distance_control": 86.0},
            },
        },
    ]

    comparison = _build_memory_ab_comparison(score_rows, case_expectations={"no_expected": []})

    assert comparison["compared_cases"] == 1
    assert comparison["expected_compared_cases"] == 0
    assert comparison["regression_count"] == 0
    assert comparison["verdict"] == "pass"


def test_memory_ab_expected_hit_with_positive_score_is_not_naturalness_regression() -> None:
    score_rows = [
        {
            "case_id": "memory_expected",
            "variant": "baseline",
            "selected": {
                "text": "记得一点。",
                "score": 88.0,
                "passed": True,
                "dimensions": {"naturalness": 94.0, "context_fit": 86.0, "distance_control": 86.0},
            },
        },
        {
            "case_id": "memory_expected",
            "variant": "with_memory",
            "selected": {
                "text": "周末看展对吧，我记性可没那么差。",
                "score": 92.0,
                "passed": True,
                "dimensions": {"naturalness": 88.0, "context_fit": 90.0, "distance_control": 86.0},
            },
        },
    ]

    comparison = _build_memory_ab_comparison(score_rows, case_expectations={"memory_expected": ["看展"]})

    assert comparison["with_memory_expected_hit_count"] == 1
    assert comparison["regression_count"] == 0
    assert comparison["verdict"] == "pass"


def test_memory_ab_passes_when_baseline_already_hits_and_memory_does_not_regress() -> None:
    score_rows = [
        {
            "case_id": "memory_expected",
            "variant": "baseline",
            "selected": {
                "text": "周末看展嘛。",
                "score": 88.0,
                "passed": True,
                "dimensions": {"naturalness": 90.0, "context_fit": 86.0, "distance_control": 86.0},
            },
        },
        {
            "case_id": "memory_expected",
            "variant": "with_memory",
            "selected": {
                "text": "记着呢，我都好奇是什么展了。",
                "score": 91.0,
                "passed": True,
                "dimensions": {"naturalness": 90.0, "context_fit": 89.0, "distance_control": 86.0},
            },
        },
    ]

    comparison = _build_memory_ab_comparison(score_rows, case_expectations={"memory_expected": ["看展"]})

    assert comparison["baseline_expected_hit_count"] == 1
    assert comparison["with_memory_expected_hit_count"] == 1
    assert comparison["memory_expected_hit_gain_count"] == 0
    assert comparison["verdict"] == "pass"


def test_cli_run_cases_with_memory_and_affect_writes_combined_variant(tmp_path) -> None:
    case = {
        "case_id": "memory_affect_compare",
        "user_message": "你怎么这么像客服",
        "reply_control_packet": {
            "response_move": "relationship_boundary",
            "task_mode": "relationship_boundary",
            "question_budget": 0,
        },
        "memory_recall_packet": {
            "facts": ["记忆线索：用户不喜欢客服式回复"],
            "relationship_clues": ["关系线索：熟人私聊，不升级亲密。"],
            "style_anchor": ["承接压缩记忆"],
            "must_not_do": ["不要暴露召回来源"],
            "confidence": 0.9,
        },
        "affect_control_packet": {
            "emotional_tone": "hurt_controlled",
            "relationship_distance": "familiar",
            "expression_intensity": 0.7,
            "style_anchor": ["情绪表达：可以有一点被刺到，但要收住。"],
            "must_not_do": ["不要攻击用户或冷暴力"],
            "raw_emotion_context": {"inner_monologue": "RAW_AFFECT_SHOULD_NOT_LEAK"},
        },
        "memory_expected_terms": ["客服"],
        "candidates_by_variant": {
            "baseline": ["不像客服，我会继续优化。"],
            "with_memory": ["记得，你不喜欢客服式回复，我先收住。"],
            "with_affect": ["别把我写得像没感觉一样。先把话说回来。"],
            "with_memory_affect": ["记得，你不喜欢客服式回复。别把我写得像没感觉一样。"],
        },
    }

    summary = run_cases([case], soul_md=SOUL, out_dir=tmp_path, with_memory=True, with_affect=True)

    assert summary["affect_cases"] == 1
    assert summary["memory_cases"] == 1
    assert summary["prompt_variants"] == 4
    assert summary["variant_summary"]["with_memory_affect"]["selected_count"] == 1
    prompt_rows = [
        json.loads(line)
        for line in (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    variants = {row["variant"]: "\n".join(message["content"] for message in row["messages"]) for row in prompt_rows}

    assert set(variants) == {"baseline", "with_memory", "with_affect", "with_memory_affect"}
    assert "禁止项：" in variants["with_affect"]
    assert "不要攻击用户或冷暴力" in variants["with_affect"]
    assert "用户不喜欢客服式回复" in variants["with_memory_affect"]
    assert "RAW_AFFECT_SHOULD_NOT_LEAK" not in variants["with_affect"]
    assert "raw_emotion_context" not in variants["with_memory_affect"]


def test_cli_run_cases_builds_affect_variant_from_raw_affect_state(tmp_path) -> None:
    case = {
        "case_id": "raw_affect_state",
        "user_message": "你刚刚是不是又走神了",
        "reply_control_packet": {
            "response_move": "repair_presence",
            "task_mode": "daily_chat",
            "question_budget": 0,
        },
        "affect_state": {
            "emotion_tag": "GUILT",
            "arousal": 0.42,
            "stability": 0.55,
            "withdraw_bias": 0.16,
            "raw_inner_monologue": "RAW_AFFECT_STATE_SHOULD_NOT_LEAK",
        },
        "relationship_summary": {"relationship_distance": "familiar_not_intimate"},
        "relationship_clues": ["用户在意回应是否连续。"],
        "candidates_by_variant": {
            "baseline": ["才没有，我明明在听。"],
            "with_affect": ["嗯，慢了一拍，现在在听。"],
        },
    }

    summary = run_cases([case], soul_md=SOUL, out_dir=tmp_path, with_affect=True)

    assert summary["affect_cases"] == 1
    assert summary["prompt_variants"] == 2
    prompt_rows = [
        json.loads(line)
        for line in (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    variants = {row["variant"]: "\n".join(message["content"] for message in row["messages"]) for row in prompt_rows}

    assert set(variants) == {"baseline", "with_affect"}
    assert "禁止项：" in variants["with_affect"]
    assert "不要把情绪状态当事实说出来" in variants["with_affect"]
    assert "RAW_AFFECT_STATE_SHOULD_NOT_LEAK" not in variants["with_affect"]
    assert "raw_inner_monologue" not in variants["with_affect"]
