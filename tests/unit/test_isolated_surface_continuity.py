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


def test_continuity_summary_compressor_emits_bounded_rolling_facts() -> None:
    compressor = ContinuitySummaryCompressor(
        thread_summary="尼尼和新海天在调最小文本链。",
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
    assert "连续摘要：尼尼和新海天在调最小文本链。" in second_facts
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
        mid_term_summary="尼尼和新海天在验证三层上下文只能先被压缩。",
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
    assert "连续摘要：尼尼和新海天在验证三层上下文只能先被压缩。" in facts
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
