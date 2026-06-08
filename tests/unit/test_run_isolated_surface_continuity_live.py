from __future__ import annotations

import time

from tools.run_isolated_surface_continuity_live import _dialogue_turn_counts, _flatten_turns, _summarize


SOUL = "你是数字生命夏目蓝。说话短一点，有轻微二次元 galgame 私聊感。"


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
