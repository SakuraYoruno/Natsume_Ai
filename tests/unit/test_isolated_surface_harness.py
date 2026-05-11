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
    assert "继续" in rendered
    assert "目标是先固定最小文本链" in rendered
    assert "当前只允许读取 ReplyControlPacket" in rendered
    assert "history_turns" not in rendered
    assert "旧消息" not in rendered


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
