from __future__ import annotations

from host_brain.app.capability_gap_detector import detect_capability_gap


def test_detect_capability_gap_skips_executable_route() -> None:
    decision = detect_capability_gap(
        user_message="帮我搜索一下今天的天气",
        route_type="skill",
        target_id="web_search",
        reason_code="keyword:搜索",
        llm_route_used=False,
    )

    assert decision.detected is False
    assert decision.reason_code == "ROUTE_ALREADY_EXECUTABLE"


def test_detect_capability_gap_detects_action_fallback_chat() -> None:
    decision = detect_capability_gap(
        user_message="请帮我写一个 Python 脚本，清理日志并输出统计",
        route_type="chat",
        target_id="",
        reason_code="default_chat",
        llm_route_used=True,
    )

    assert decision.detected is True
    assert decision.reason_code == "ACTION_INTENT_FALLBACK_CHAT"
    assert "coding" in decision.missing_capability_tags
    assert decision.suggested_family == "code"
    assert decision.confidence >= 0.7


def test_detect_capability_gap_ignores_question_only_turn() -> None:
    decision = detect_capability_gap(
        user_message="这个词是什么意思？",
        route_type="chat",
        target_id="",
        reason_code="default_chat",
        llm_route_used=False,
    )

    assert decision.detected is False
    assert decision.reason_code == "NO_ACTION_INTENT"

