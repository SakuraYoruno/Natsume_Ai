from __future__ import annotations

import pytest

from host_brain.governor.decision import IntentDecisionEngine
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


def test_known_read_only_translation_action_is_not_unknown_risk_five() -> None:
    current_ms = 1_785_872_000_000
    engine = IntentDecisionEngine()

    decision = engine.evaluate_intent(
        {
            "schema_version": "Natsume.intent.v1",
            "id": "intent-work-translate-risk",
            "ts_ms": current_ms,
            "session_id": "session-work-translate-risk",
            "seq": 1,
            "type": "query",
            "action": "work_translate",
            "target": "host",
            "args": {"text": "把今天辛苦了翻译成英文"},
            "nonce": "nonce-work-translate-risk",
        },
        request_id="request-work-translate-risk",
        current_ms=current_ms,
        governance_snapshot={
            "trust_level": 80,
            "energy_level": 80,
            "sys_mode": "normal",
            "rate_limit_hit": False,
            "scope": "host",
            "scope_allowlist": ["host"],
            "safety_flag": False,
            "airlock_blocked": False,
        },
    )

    assert decision["decision"] == "APPROVE"
    assert decision["approved_action_binding"]["risk_level"] == 2
