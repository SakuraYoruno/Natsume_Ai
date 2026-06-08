from __future__ import annotations

from pathlib import Path

from host_brain.persona.isolated_surface_continuity import build_continuity_surface_inputs
from tools.build_isolated_surface_continuity_cases import (
    build_continuity_cases,
    build_continuity_eval_cases,
    build_continuity_long_cases,
    build_summary,
)


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


def test_build_continuity_cases_are_valid_compressed_turns() -> None:
    cases = build_continuity_cases()

    assert len(cases) == 6
    assert all(len(case["turns"]) == 3 for case in cases)
    assert len({case["case_id"] for case in cases}) == len(cases)

    for case in cases:
        turns = build_continuity_surface_inputs(case, soul_md=SOUL)
        assert len(turns) == 3
        for turn in turns:
            assert turn.request.user_message
            assert turn.request.control.style_anchor
            assert "连续对话" in turn.request.control.style_anchor
            assert "夏目蓝私聊" in turn.request.control.style_anchor


def test_build_continuity_summary_counts_scenes() -> None:
    cases = build_continuity_cases()
    summary = build_summary(cases, out_dir=Path("out"))

    assert summary["dialogues"] == 6
    assert summary["suite"] == "smoke"
    assert summary["turns"] == 18
    assert summary["scene_counts"]["short_ack"] == 4
    assert summary["scene_counts"]["followup"] == 3


def test_build_continuity_eval_cases_are_valid_and_larger() -> None:
    cases = build_continuity_eval_cases()

    assert len(cases) == 24
    assert all(len(case["turns"]) == 3 for case in cases)
    assert len({case["case_id"] for case in cases}) == len(cases)

    for case in cases:
        turns = build_continuity_surface_inputs(case, soul_md=SOUL)
        assert len(turns) == 3
        assert all(turn.request.user_message for turn in turns)

    summary = build_summary(cases, out_dir=Path("out"), suite="eval")
    assert summary["suite"] == "eval"
    assert summary["turns"] == 72
    assert summary["scene_counts"]["short_ack"] >= 8


def test_build_continuity_long_case_uses_compressed_summary_budget() -> None:
    cases = build_continuity_long_cases()

    assert len(cases) == 1
    assert len(cases[0]["turns"]) >= 31

    turns = build_continuity_surface_inputs(cases[0], soul_md=SOUL)
    assert len(turns) == len(cases[0]["turns"])

    for turn in turns:
        facts = turn.request.control.facts
        rendered_fact_text = "\n".join(facts)
        assert len(facts) <= 6
        assert "连续摘要：" in rendered_fact_text
        assert "conversation_history" not in rendered_fact_text
        assert "messages" not in rendered_fact_text

    assert "上一轮摘要：" in "\n".join(turns[1].request.control.facts)
    assert turns[0].request.control.response_move == "continue_existing_topic"
    assert turns[2].request.control.response_move == "answer_in_place"
    assert any(turn.request.control.response_move == "technical_next_step" for turn in turns)
    assert turns[-1].request.control.response_move == "finalize_chain"

    summary = build_summary(cases, out_dir=Path("out"), suite="long")
    assert summary["suite"] == "long"
    assert summary["dialogues"] == 1
    assert summary["max_turns_per_dialogue"] >= 31
    assert summary["long_dialogues_30_plus"] == 1
    assert summary["turns"] == len(turns)
    rendered = "\n".join(
        "\n".join(turn.request.control.facts) + "\n" + "\n".join(turn.request.control.must_include)
        for turn in turns
    )
    assert "夏目蓝" in rendered
    assert "新海天" not in rendered
