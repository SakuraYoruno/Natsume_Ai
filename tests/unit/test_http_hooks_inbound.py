from __future__ import annotations

from host_brain.app.http_hooks_inbound import should_emit_request_info


def test_should_emit_request_info_silences_internal_tribe_event() -> None:
    assert should_emit_request_info("/internal/tribe_event") is False


def test_should_emit_request_info_keeps_chat_requests() -> None:
    assert should_emit_request_info("/v1/chat") is True
