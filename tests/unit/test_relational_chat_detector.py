from __future__ import annotations

from host_brain.app.relational_chat_detector import (
    build_relational_chat_reason_code,
    detect_relational_chat_topic,
    is_relational_chat_message,
)


def test_detect_relational_chat_topic_identity() -> None:
    assert detect_relational_chat_topic("你是") == "identity"
    assert detect_relational_chat_topic("你谁？") == "identity"


def test_detect_relational_chat_topic_help_commitment() -> None:
    assert detect_relational_chat_topic("那你还会帮我吗") == "help_commitment"
    assert is_relational_chat_message("那你还会帮我吗") is True


def test_relational_chat_detector_ignores_concrete_action_requests() -> None:
    assert detect_relational_chat_topic("你还会帮我写代码吗") == ""
    assert is_relational_chat_message("你还会帮我写代码吗") is False


def test_build_relational_chat_reason_code() -> None:
    assert build_relational_chat_reason_code("你为什么要帮我，我们很熟吗") == "RELATIONAL_CHAT:relationship_boundary"
