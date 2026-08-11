from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.orchestration.governor_grant import (
    GOVERNOR_GRANT_BINDING_MISSING,
    GOVERNOR_GRANT_SCOPE_MISMATCH,
    DurableGovernorGrantResolver,
    GovernorGrantError,
    canonical_proactive_intent_args,
    governor_grant_ref,
)


REQUEST_ID = "request-bound-proactive"
INTENT_ID = "intent-bound-proactive"
TOKEN_REF = "approval::intent-bound-proactive"


def _args(*, person_id: str = "person-1", content: str = "hello") -> dict[str, str]:
    return canonical_proactive_intent_args(
        person_id=person_id,
        platform="qq",
        conversation_id="conversation-1",
        content=content,
        source_kind="manual_proactive_attempt",
    )


def _bound_decision() -> dict:
    binding = build_governed_action_binding(
        intent_envelope={
            "schema_version": "Natsume.intent.v1",
            "id": INTENT_ID,
            "session_id": "session-1",
            "seq": 1,
            "type": "COMMAND",
            "action": "proactive_attempt",
            "target": "proactive.gateway",
            "args": _args(),
            "nonce": "nonce-1",
            "person_id": "person-1",
        },
        approval_token_ref=TOKEN_REF,
        risk_level=3,
        issued_at_ms=1_800_000_000_000,
        expires_at_ms=4_000_000_000_000,
    )
    return {
        "request_id": REQUEST_ID,
        "intent_id": INTENT_ID,
        "decision": "APPROVE",
        "tokens": {"approval_token_ref": TOKEN_REF},
        "approved_action_binding": binding,
        "stage": "S_INTENT_OK",
        "ts_ms": 1_800_000_000_000,
    }


def _require(resolver: DurableGovernorGrantResolver, decision: dict, **overrides):
    values = {
        "request_id": REQUEST_ID,
        "expected_ref": governor_grant_ref(REQUEST_ID, decision),
        "expected_person_id": "person-1",
        "expected_action_kind": "proactive_attempt",
        "expected_target": "proactive.gateway",
        "expected_intent_args": _args(),
        "now_ms": 1_900_000_000_000,
    }
    values.update(overrides)
    return resolver.require_bound(**values)


def test_bound_grant_survives_sqlite_log_and_validates_exact_scope() -> None:
    ledger = SQLiteLedger(":memory:")
    decision = _bound_decision()
    grant_ref = governor_grant_ref(REQUEST_ID, decision)
    ledger.log_governor(decision)

    durable = ledger.governor_log[-1]
    assert durable["tokens"]["approved_action_binding"]["person_id"] == "person-1"
    resolved = DurableGovernorGrantResolver(ledger).require_bound(
        request_id=REQUEST_ID,
        expected_ref=grant_ref,
        expected_person_id="person-1",
        expected_action_kind="proactive_attempt",
        expected_target="proactive.gateway",
        expected_intent_args=_args(),
        now_ms=1_900_000_000_000,
    )
    assert resolved["approved_action_binding"]["principal_id"] == "person-1"


def test_real_governor_sqlite_trace_preserves_and_resolves_bound_identity() -> None:
    ledger = SQLiteLedger(":memory:")
    engine = IntentDecisionEngine(ledger=ledger)
    current_ms = 1_800_000_000_000
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-real-bound",
        "ts_ms": current_ms - 100,
        "session_id": "session-real-bound",
        "seq": 1,
        "type": "skill_call",
        "action": "read_only_status",
        "target": "host",
        "args": {"scope": "system"},
        "nonce": "nonce-real-bound",
        "person_id": "person-real-bound",
    }
    decision = engine.evaluate_intent(
        intent,
        request_id="request-real-bound",
        current_ms=current_ms,
        governance_snapshot={
            "trust_level": 80,
            "energy_level": 80,
            "sys_mode": "normal",
            "rate_limit_hit": False,
            "scope": "host",
            "scope_allowlist": ["host", "system"],
            "safety_flag": False,
            "airlock_blocked": False,
        },
    )
    assert decision["decision"] == "APPROVE"
    resolved = DurableGovernorGrantResolver(ledger).require_bound(
        request_id="request-real-bound",
        expected_ref=governor_grant_ref("request-real-bound", decision),
        expected_person_id="person-real-bound",
        expected_action_kind="read_only_status",
        expected_target="host",
        expected_intent_args={"scope": "system"},
        now_ms=current_ms + 100,
    )
    binding = resolved["approved_action_binding"]
    assert binding["person_id"] == "person-real-bound"
    assert binding["principal_id"] == "person-real-bound"


def test_client_args_cannot_nominate_grant_principal() -> None:
    binding = build_governed_action_binding(
        intent_envelope={
            "schema_version": "Natsume.intent.v1",
            "id": "intent-untrusted-principal",
            "session_id": "session-untrusted-principal",
            "seq": 1,
            "type": "COMMAND",
            "action": "proactive_attempt",
            "target": "proactive.gateway",
            "args": {**_args(), "principal_id": "client-admin"},
            "nonce": "nonce-untrusted-principal",
        },
        approval_token_ref="approval::intent-untrusted-principal",
        risk_level=3,
        issued_at_ms=1_800_000_000_000,
        expires_at_ms=4_000_000_000_000,
    )

    assert binding["person_id"] is None
    assert binding["principal_id"] is None


def test_ordinary_approve_cannot_be_reused_as_proactive_grant() -> None:
    decision = {"decision": "APPROVE", "intent_id": INTENT_ID, "ts_ms": 123}

    class Ledger:
        def trace_request(self, _request_id):
            return {"governor_log": [decision]}

    with pytest.raises(GovernorGrantError) as exc_info:
        _require(DurableGovernorGrantResolver(Ledger()), decision)
    assert exc_info.value.reason_code == GOVERNOR_GRANT_BINDING_MISSING


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_person_id": "person-2"},
        {"expected_action_kind": "embodiment"},
        {"expected_target": "mateengine.state"},
        {"expected_intent_args": _args(content="tampered")},
    ],
)
def test_bound_grant_rejects_person_action_target_and_payload_mismatch(overrides) -> None:
    decision = _bound_decision()

    class Ledger:
        def trace_request(self, _request_id):
            return {"governor_log": [decision]}

    with pytest.raises(GovernorGrantError) as exc_info:
        _require(DurableGovernorGrantResolver(Ledger()), decision, **overrides)
    assert exc_info.value.reason_code == GOVERNOR_GRANT_SCOPE_MISMATCH
