from __future__ import annotations

import json

import pytest

from host_brain.persona.isolated_surface_continuity import (
    build_continuity_surface_inputs,
    find_forbidden_continuity_input_paths,
)
from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_messages,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)
from tools.isolated_surface_continuity_harness import run_cases


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def _turn(turn_id: str, user_message: str, task_mode: str, *, facts: list[str] | None = None) -> dict:
    return {
        "turn_id": turn_id,
        "user_message": user_message,
        "reply_control_packet": {
            "response_move": task_mode,
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


def test_continuity_short_ack_rerank_uses_compressed_previous_turn_fact() -> None:
    payload = {
        "case_id": "short_ack_continuity",
        "turns": [
            _turn("t01", "今天心情很差", "bad_mood"),
            _turn(
                "t02",
                "嗯",
                "short_ack",
                facts=["上一轮摘要：尼尼说今天心情很差，回复已先接住情绪。"],
            ),
        ],
    }
    request = build_continuity_surface_inputs(payload, soul_md=SOUL)[1].request
    scores = score_isolated_surface_candidates(
        request,
        [
            "嗯嗯，你说了算。",
            "嗯，我在听，慢慢说。",
        ],
    )

    selected = select_best_isolated_candidate(scores)

    assert selected is not None
    assert selected.text == "嗯，我在听，慢慢说。"
    assert selected.passed
    assert any("BLAND_PASS_LINE" in row.reason_codes for row in scores if row.text == "嗯嗯，你说了算。")


def test_continuity_cli_writes_prompts_and_scores(tmp_path) -> None:
    case = {
        "case_id": "cli_continuity",
        "turns": [
            {
                **_turn("t01", "在吗", "presence"),
                "candidates": ["在呢，尼尼，这句我看见了。", "在在在，等你半天了。"],
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
    assert first_prompt["case_id"] == "cli_continuity"
    assert len(scores) == 2
