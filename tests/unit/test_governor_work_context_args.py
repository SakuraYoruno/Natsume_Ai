from __future__ import annotations

import pytest

from host_brain.governor.schemas import strict_validate_intent


@pytest.mark.parametrize("action", ("work_code", "work_translate"))
def test_work_intent_accepts_server_owned_conversation_context(action: str) -> None:
    payload = {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-{action}",
        "ts_ms": 1_785_872_000_000,
        "session_id": "session-work-context",
        "seq": 1,
        "type": "query",
        "action": action,
        "target": "host",
        "args": {
            "text": "请处理这个请求",
            "semantic_intent": {"domain": "work"},
            "pragmatic_frame": {"goal": "deliver"},
            "conversation_understanding": {"continuity": "new"},
        },
        "nonce": f"nonce-{action}",
    }

    envelope, reason_code, lint = strict_validate_intent(payload)

    assert reason_code is None, lint
    assert envelope is not None
    assert envelope.action == action
    assert envelope.args["conversation_understanding"] == {"continuity": "new"}


def test_work_intent_still_rejects_unregistered_context_field() -> None:
    payload = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-work-unknown",
        "ts_ms": 1_785_872_000_000,
        "session_id": "session-work-context",
        "seq": 1,
        "type": "query",
        "action": "work_translate",
        "target": "host",
        "args": {"text": "翻译", "untrusted_extension": {}},
        "nonce": "nonce-work-unknown",
    }

    envelope, reason_code, lint = strict_validate_intent(payload)

    assert envelope is None
    assert reason_code == "INTENT_ARGS_INVALID"
    assert lint["args"] == [
        "unknown arg for work_translate: untrusted_extension"
    ]
