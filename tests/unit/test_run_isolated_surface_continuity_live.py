from __future__ import annotations

import time

from tools.run_isolated_surface_continuity_live import _dialogue_turn_counts, _flatten_turns, _summarize


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def _turn(turn_id: str, user_message: str, task_mode: str) -> dict:
    return {
        "turn_id": turn_id,
        "user_message": user_message,
        "reply_control_packet": {
            "response_move": task_mode,
            "task_mode": task_mode,
            "question_budget": 0,
            "style_anchor": ["短句", "连续对话"],
        },
    }


def test_flatten_turns_preserves_dialogue_and_turn_trace() -> None:
    turns = _flatten_turns(
        [
            {
                "case_id": "dialogue_one",
                "turns": [
                    _turn("t01", "在吗", "presence"),
                    _turn("t02", "嗯", "short_ack"),
                ],
            }
        ],
        soul_md=SOUL,
    )

    assert [turn.case_id for turn in turns] == ["dialogue_one", "dialogue_one"]
    assert [turn.turn_id for turn in turns] == ["t01", "t02"]
    assert turns[1].request.case_id == "dialogue_one/t02"
    assert _dialogue_turn_counts(turns) == {"dialogue_one": 2}


def test_summarize_counts_dialogue_pass_only_after_all_turns_complete(tmp_path) -> None:
    started = time.perf_counter()
    selected = {"passed": True, "score": 88.0, "hard_fail": False, "control_violations": []}
    partial = _summarize(
        dialogues_count=1,
        turns_count=2,
        expected_dialogue_turns={"dialogue_one": 2},
        completed=1,
        results=[{"case_id": "dialogue_one", "ok": True, "selected": selected, "latency_ms": 10}],
        started=started,
        provider="fake",
        model="fake-model",
        concurrency=1,
        gen_params={},
        out_dir=tmp_path,
    )
    complete = _summarize(
        dialogues_count=1,
        turns_count=2,
        expected_dialogue_turns={"dialogue_one": 2},
        completed=2,
        results=[
            {"case_id": "dialogue_one", "ok": True, "selected": selected, "latency_ms": 10},
            {"case_id": "dialogue_one", "ok": True, "selected": selected, "latency_ms": 12},
        ],
        started=started,
        provider="fake",
        model="fake-model",
        concurrency=1,
        gen_params={},
        out_dir=tmp_path,
    )

    assert partial["dialogue_pass_count"] == 0
    assert complete["dialogue_pass_count"] == 1
    assert complete["pass_count"] == 2
