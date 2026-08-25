from __future__ import annotations

from pathlib import Path

import host_brain.app.chat_handler as chat_handler


def test_chat_handler_cannot_reopen_text_derived_mateengine_effects() -> None:
    """Only accepted ExpressionPlan effects may reach Avatar delivery.

    A final reply, route label, or missing emotion tag must never enqueue a
    Chat-side MateEngine command or synthesize ``NEUTRAL``.
    """

    source = Path(chat_handler.__file__).read_text(encoding="utf-8")
    assert "queue_mateengine_command" not in source
    assert "mateengine:chat-bubble" not in source
    assert "mateengine:state:" not in source
    assert "_send_mateengine_state_cue" not in source
    assert "_send_mateengine_chat_bubble" not in source
