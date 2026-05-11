from __future__ import annotations

from pathlib import Path

from host_brain.persona.isolated_surface_continuity import build_continuity_surface_inputs
from tools.build_isolated_surface_continuity_cases import build_continuity_cases, build_continuity_eval_cases, build_summary


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


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
