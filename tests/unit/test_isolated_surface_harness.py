from __future__ import annotations

import asyncio
import json

import pytest

from host_brain.persona.isolated_surface_harness import (
    ReplyControlPacket,
    build_isolated_surface_input,
    build_isolated_surface_messages,
    build_style_reference_query,
    derive_style_reference_anchor,
    find_forbidden_input_paths,
    merged_facts,
    merged_must_not_do,
    parse_surface_candidates,
    run_isolated_surface_generation,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)
from host_brain.persona.style_reference_bank import clear_style_reference_bank_cache, style_reference_from_raw_row
from tools.isolated_surface_harness import run_cases


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
        "candidates": ["记得啊，周末看展这事我没忘。", "别急，先把这个展的事接上。"],
    }

    summary = run_cases([case], soul_md=SOUL, out_dir=tmp_path, with_memory=True)

    assert summary["cases"] == 1
    assert summary["memory_cases"] == 1
    assert summary["prompt_variants"] == 2
    assert summary["scored_cases"] == 2
    prompt_rows = [
        json.loads(line)
        for line in (tmp_path / "prompts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    variants = {row["variant"]: "\n".join(message["content"] for message in row["messages"]) for row in prompt_rows}

    assert set(variants) == {"baseline", "with_memory"}
    assert "记忆线索：上次说好周末看展" not in variants["baseline"]
    assert "记忆线索：上次说好周末看展" in variants["with_memory"]
    assert "关系线索：熟人私聊，不升级亲密。" in variants["with_memory"]
    assert "不要在回复里暴露 memory:// 或 evd://" in variants["with_memory"]
    assert "memory://episodic/mem-1" not in variants["with_memory"]
    assert "evd://episodic/mem-1" not in variants["with_memory"]
