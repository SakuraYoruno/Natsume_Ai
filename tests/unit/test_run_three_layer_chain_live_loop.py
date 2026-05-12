from __future__ import annotations

import time

from host_brain.persona.isolated_surface_harness import build_isolated_surface_messages
from tools.run_three_layer_chain_live_loop import (
    _summarize,
    build_loop_request,
    raw_recent_leak_hashes,
    recent_window_trace,
)


SOUL = "你是新海天。说话短一点，有轻微二次元 galgame 私聊感。"


def _turn(user_message: str, task_mode: str = "next_step") -> dict:
    return {
        "turn_id": "t02",
        "user_message": user_message,
        "reply_control_packet": {
            "response_move": task_mode,
            "task_mode": task_mode,
            "emotional_tone": "light",
            "question_budget": 0,
            "must_include": ["压缩"],
            "must_not_do": [],
            "style_anchor": ["三层闭环"],
        },
    }


def test_build_loop_request_hashes_recent_and_keeps_raw_out_of_prompt() -> None:
    raw_recent = [
        {"role": "user", "content": "RAW_LOOP_USER_SECRET 最近几轮原始用户句不能泄露"},
        {"role": "assistant", "content": "RAW_LOOP_ASSISTANT_SECRET 上一轮真实回复不能原样塞回"},
    ]
    request = build_loop_request(
        case_id="loop/t02",
        turn=_turn("那最近 N 轮怎么处理"),
        raw_recent=raw_recent,
        soul_md=SOUL,
    )
    messages = build_isolated_surface_messages(request)
    rendered = "\n".join(message["content"] for message in messages)
    trace = recent_window_trace(raw_recent)

    assert len(request.control.facts) <= 6
    assert "连续摘要：" in "\n".join(request.control.facts)
    assert raw_recent[0]["content"] not in rendered
    assert raw_recent[1]["content"] not in rendered
    assert raw_recent_leak_hashes(messages, raw_recent) == ()
    assert trace[0]["sha256_16"]
    assert "RAW_LOOP_USER_SECRET" not in str(trace)


def test_build_loop_request_compresses_turn_affect_state_before_surface_prompt() -> None:
    raw_recent = [{"role": "assistant", "content": "RAW_RECENT_EXACT_REPLY"}]
    turn = _turn("控制台一片红，我现在只想知道先看哪一条", task_mode="technical_help")
    turn["affect_state"] = {
        "emotion_tag": "ANXIOUS",
        "arousal": 0.92,
        "stability": 0.28,
        "raw_inner_monologue": "RAW_AFFECT_LOOP_SECRET",
    }
    turn["relationship_summary"] = {"relationship_distance": "familiar_not_intimate"}
    request = build_loop_request(
        case_id="loop/affect",
        turn=turn,
        raw_recent=raw_recent,
        soul_md=SOUL,
    )
    rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

    assert request.control.emotional_tone == "steady_helpful"
    assert request.control.relationship_distance == "familiar_not_intimate"
    assert "技术或推进场景里，情绪只做稳定器" in rendered
    assert "RAW_AFFECT_LOOP_SECRET" not in rendered
    assert "raw_inner_monologue" not in rendered
    assert raw_recent[0]["content"] not in rendered


def test_summarize_counts_closed_loop_dialogue_pass(tmp_path) -> None:
    selected = {"passed": True, "score": 88.5, "hard_fail": False, "control_violations": []}
    cases = [{"case_id": "loop_dialogue", "turns": [_turn("第一轮"), _turn("第二轮")]}]
    started = time.perf_counter()

    partial = _summarize(
        cases=cases,
        completed=1,
        results=[{"case_id": "loop_dialogue", "ok": True, "selected": selected, "latency_ms": 10}],
        started=started,
        provider="fake",
        model="fake-model",
        gen_params={},
        out_dir=tmp_path,
        raw_recent_leak_count=0,
    )
    complete = _summarize(
        cases=cases,
        completed=2,
        results=[
            {"case_id": "loop_dialogue", "ok": True, "selected": selected, "latency_ms": 10},
            {"case_id": "loop_dialogue", "ok": True, "selected": selected, "latency_ms": 12},
        ],
        started=started,
        provider="fake",
        model="fake-model",
        gen_params={},
        out_dir=tmp_path,
        raw_recent_leak_count=0,
    )

    assert partial["closed_loop"] is True
    assert partial["dialogue_pass_count"] == 0
    assert complete["dialogue_pass_count"] == 1
    assert complete["actual_feedback_turns"] == 2
    assert complete["raw_recent_leak_count"] == 0
