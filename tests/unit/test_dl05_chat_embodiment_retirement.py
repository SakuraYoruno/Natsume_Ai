from __future__ import annotations

from pathlib import Path

import host_brain.app.chat_handler as chat_handler
from host_brain.app.settings.chat import get_chat_settings


def test_chat_handler_cannot_reopen_text_derived_mateengine_effects(monkeypatch) -> None:
    """Only accepted ExpressionPlan effects may reach Avatar delivery.

    The environment keys remain parseable for one compatibility window, but a
    final reply, route label, or its missing emotion tag must never enqueue a
    Chat-side MateEngine command or synthesize ``NEUTRAL``.
    """

    monkeypatch.setenv("HOST_MATEENGINE_CHAT_BUBBLE_ENABLED", "true")
    monkeypatch.setenv("HOST_MATEENGINE_STATE_CUES_ENABLED", "true")

    assert get_chat_settings().mateengine_chat_bubble_enabled is True
    assert get_chat_settings().mateengine_state_cues_enabled is True
    assert chat_handler._mateengine_chat_bubble_enabled() is False
    assert chat_handler._mateengine_state_cues_enabled() is False

    source = Path(chat_handler.__file__).read_text(encoding="utf-8")
    assert "queue_mateengine_command" not in source
    assert "mateengine:chat-bubble" not in source
    assert "mateengine:state:" not in source
