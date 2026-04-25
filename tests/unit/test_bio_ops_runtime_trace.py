from __future__ import annotations

from host_brain.bio_ops.runtime_trace import build_bio_ops_runtime_trace
from host_brain.tribe.thalamus_agent import classify_task_intent_by_rules, classify_task_intent_fastpath


def test_thalamus_short_ack_attaches_bio_ops_reflex_without_codex_worker() -> None:
    decision = classify_task_intent_fastpath("好的")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["bio_ops_reflex"]["matched"] is True
    assert "codex_worker" in decision["bio_ops_reflex"]["forbidden_actions"]


def test_task_like_message_does_not_get_reflex_short_circuited() -> None:
    message = "please fix the failing test and show me the code"
    assert classify_task_intent_fastpath(message) is None

    decision = classify_task_intent_by_rules(message)

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "codex_worker"


def test_bio_ops_runtime_trace_observes_weather_breathing_and_silence() -> None:
    trace = build_bio_ops_runtime_trace(
        message="我好累",
        route_type="chat",
        target_id="",
        emotion_context={"emotion_tag": "EXHAUSTED", "stress_level": "high"},
        runtime_signals={
            "current_hour": 2,
            "codex_queue_size": 4,
            "tts_status": "degraded",
            "explicit_help_request": False,
        },
    )

    assert trace["proposal_only"] is True
    assert trace["reflex"]["matched"] is True
    assert trace["internal_weather"]["internal_weather"] == "static"
    assert trace["active_silence"]["silence_policy"] == "quiet_presence"
    assert trace["response_breathing"]["reply_density"] == "soft_brief"


def test_bio_ops_runtime_trace_marks_execution_routes_as_explicit_help() -> None:
    trace = build_bio_ops_runtime_trace(
        message="帮我修复测试",
        route_type="skill",
        target_id="codex_worker",
        emotion_context={"emotion_tag": "NEUTRAL"},
        runtime_signals={"current_hour": 2, "user_stress": "high"},
    )

    assert trace["reflex"]["matched"] is False
    assert trace["active_silence"]["silence_policy"] == "normal_presence"
    assert trace["response_breathing"]["reply_density"] == "compact_status"
