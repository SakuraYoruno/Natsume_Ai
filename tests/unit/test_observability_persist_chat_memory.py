from __future__ import annotations

from host_brain.app.observability_persist import build_chat_episodic_text


def test_build_chat_episodic_text_keeps_user_side_only() -> None:
    text = build_chat_episodic_text(
        user_message="你为什么要帮我，我们很熟吗",
        assistant_reply="我们并不熟。我只是觉得，既然你遇到了麻烦，能帮一把就帮一把。",
        session_id="sess_demo_001",
    )

    assert text == "[session=sess_demo_001]\nUser: 你为什么要帮我，我们很熟吗"
    assert "Natsume:" not in text
    assert "能帮一把就帮一把" not in text
