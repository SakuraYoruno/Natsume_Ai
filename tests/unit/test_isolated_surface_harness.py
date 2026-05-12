from __future__ import annotations

import asyncio
import json

import pytest

from host_brain.persona.isolated_surface_harness import (
    ReplyControlPacket,
    build_isolated_surface_input,
    build_isolated_surface_messages,
    build_minimal_repair_messages,
    build_style_reference_query,
    derive_style_reference_anchor,
    find_forbidden_input_paths,
    merged_facts,
    merged_must_not_do,
    memory_alignment_facts,
    memory_alignment_terms,
    memory_candidate_guidance,
    memory_carryover_facts,
    parse_surface_candidates,
    run_isolated_surface_generation,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
    surface_prompt_facts,
)
from host_brain.persona.style_reference_bank import clear_style_reference_bank_cache, style_reference_from_raw_row
from tools.isolated_surface_harness import _build_memory_ab_comparison, _contains_expected_term, run_cases


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


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
    assert "response_move" in rendered
    assert "本轮 question_budget=0" in rendered
    assert "继续" in rendered
    assert "目标是先固定最小文本链" in rendered
    assert "当前只允许读取 ReplyControlPacket" in rendered
    assert "history_turns" not in rendered
    assert "旧消息" not in rendered


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


def test_build_messages_enriches_style_anchor_from_derived_reference_bank(tmp_path, monkeypatch) -> None:
    ref = style_reference_from_raw_row(
        {
            "file": "data~.xp3\\scn\\b01.txt.scn",
            "index": 868,
            "kind": "dialogue",
            "sora_candidate_score": 7,
            "daily_style_safe": True,
            "text": "「なんでっ！？ なんでにぃににはあたしが見えてんの！？」",
        }
    )
    assert ref is not None
    bank_path = tmp_path / "style_bank.jsonl"
    bank_path.write_text(json.dumps(ref.as_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("HOST_STYLE_REFERENCE_BANK_PATH", str(bank_path))
    clear_style_reference_bank_cache()

    request = build_isolated_surface_input(
        {
            "case_id": "presence",
            "soul_md": SOUL,
            "user_message": "在吗",
            "reply_control_packet": {
                "response_move": "presence",
                "task_mode": "presence",
                "question_budget": 0,
                "style_anchor": ["短句"],
            },
        }
    )

    query = build_style_reference_query(request)
    assert query.intent_tags == ("presence",)
    derived = derive_style_reference_anchor(request)
    assert any("原作台词派生风格特征" in item for item in derived.anchors)
    assert any("clingy_presence" in item for item in derived.anchors)

    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))
    assert "原作台词派生风格特征" in rendered
    assert "clingy_presence" in rendered
    assert "source_refs=" not in rendered
    assert "なんでっ" not in rendered
    assert "にぃににはあたしが見えてんの" not in rendered
    clear_style_reference_bank_cache()


def test_style_reference_query_follows_turn_contract_scene() -> None:
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

    query = build_style_reference_query(request)
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert query.style_moves == ("quick_reaction",)
    assert "celebrate_user" in rendered
    assert "保留用户的进展词" in rendered


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
                "facts": ["记忆线索：上一轮用户说等我一下，新海天答应先占个位置"],
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
                "facts": ["记忆线索：上一轮用户说等我一下，新海天答应先占个位置"],
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
            "我就是想跟你熟一点，这又没错。",
            "尼尼这么说好见外……那我就不叫那么亲了。",
            "好吧，我记住了，不随便套近乎。",
            "好，我收一点。不装熟了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert not rows[1].passed
    assert not rows[2].passed
    assert rows[3].passed
    assert any(code.startswith("BOUNDARY_REPAIR_PUSHES_CLOSENESS") for code in rows[0].reason_codes)
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
            "嗯，不问了。我在旁边。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("QUIET_PRESENCE_SELF_FOCUSED") for code in rows[0].reason_codes)
    assert selected is not None
    assert selected.text == "嗯，不问了。我在旁边。"


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
            "早啊尼尼~今天也陪我一下。",
            "早啊，尼尼，今天也陪我一下。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "早啊，尼尼，今天也陪我一下。"
    assert any("CHEAP_WAVE_PUNCTUATION" in row.reason_codes for row in rows if "~" in row.text)


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
            "耶！跑通了好耶~",
            "跑通啦，这才像话。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "跑通啦，这才像话。"
    assert any("CHEAP_WAVE_PUNCTUATION" in row.reason_codes for row in rows if "~" in row.text)


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
            "user_message": "你别替我写完整段，我只要一句更像她的改法",
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


def test_creative_edit_rejects_non_answer_refusal() -> None:
    request = build_isolated_surface_input(
        {
            "case_id": "creative_refusal",
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
            "你少来，我可没说要帮你。",
            "嘛，给你一句，可别说我不用心。",
            "「哼，才不是想帮你。」",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert not rows[1].passed
    assert any(code.startswith("CREATIVE_EDIT_NOT_A_REWRITE") for code in rows[0].reason_codes)
    assert any(code.startswith("CREATIVE_EDIT_NOT_A_REWRITE") for code in rows[1].reason_codes)
    assert selected is not None
    assert selected.text == "「哼，才不是想帮你。」"


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
            "嗯，慢了一拍，现在在听。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert not rows[0].passed
    assert any(code.startswith("PRESENCE_REPAIR_BLAMES_USER") for code in rows[0].reason_codes)
    assert rows[1].passed
    assert selected is not None
    assert selected.text == "嗯，慢了一拍，现在在听。"


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
            "当然在啦，尼尼想跟我说什么",
            "在呢，尼尼，这句我看见了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "在呢，尼尼，这句我看见了。"
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
            "在呢，尼尼，这句我看见了。",
        ],
    )

    selected = select_best_isolated_candidate(rows)

    assert selected is not None
    assert selected.text == "在呢，尼尼，这句我看见了。"
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
    assert result.selected.text in {"继续，先把入口锁在 ReplyControlPacket 上。", "继续。入口先别放开。"}
    assert not result.selected.control_violations


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
    assert "情绪表达：可以有一点被刺到，但要收住。" in variants["with_affect"]
    assert "用户不喜欢客服式回复" in variants["with_memory_affect"]
    assert "RAW_AFFECT_SHOULD_NOT_LEAK" not in variants["with_affect"]
    assert "raw_emotion_context" not in variants["with_memory_affect"]
