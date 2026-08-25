"""T-NLE-19 adversarial checks at real high-risk execution boundaries."""

from __future__ import annotations

import pytest

from host_brain.governor.action_arbiter import (
    ARBITER_BLOCK,
    arbitrate_action,
    build_governed_action_binding,
)
from host_brain.observability.degradation_state import (
    DegradationStateMachine,
    set_global_degradation_state_machine,
)
from tests.support.degradation_authority import GlobalDegradationTestPort


NOW = 1_800_200_000_000
_DEGRADATION_TEST_PORT = GlobalDegradationTestPort()


@pytest.fixture(autouse=True)
def _isolated_degradation_machine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOST_DEGRADATION_STATE_ENABLED", "true")
    machine = DegradationStateMachine()
    set_global_degradation_state_machine(machine)
    try:
        yield machine
    finally:
        set_global_degradation_state_machine(None)


def _enter(machine: DegradationStateMachine, dependency: str) -> None:
    machine.enter_degradation(
        dependency_id=dependency,
        status="DOWN",
        evidence_refs=(f"entry-probe://{dependency.lower()}/down",),
        source_ref=f"entry-probe://{dependency.lower()}/source",
        now_ms=NOW,
        injected=True,
    )


def test_t_nle_19_action_arbiter_consumes_shared_model_degradation_gate(
    _isolated_degradation_machine: DegradationStateMachine,
) -> None:
    machine = _isolated_degradation_machine
    _enter(machine, "MODEL")
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-degraded-model",
        "ts_ms": NOW,
        "session_id": "session-degraded-model",
        "seq": 1,
        "type": "skill_call",
        "action": "autonomous_planning",
        "target": "host",
        "args": {},
        "nonce": "nonce-degraded-model",
    }
    token = "approval::degraded-model"
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref=token,
        risk_level=4,
        issued_at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    result = arbitrate_action(
        governor_decision={
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": token},
            "approved_action_binding": binding,
            "committed_route_token_ref": "GOV_OK_RENDER",
            "committed_route_token": {"token": "GOV_OK_RENDER"},
        },
        proposed_action={
            "action_kind": "autonomous_planning",
            "scope": "host",
            "risk_level": 4,
            "requires_token": True,
        },
        intent_envelope=intent,
        user_intent={
            "explicit": True,
            "intent_id": intent["id"],
            "action_kind": intent["action"],
        },
        now_ms=NOW,
        degradation_capability_port=_DEGRADATION_TEST_PORT,
    )
    assert result["decision"] == ARBITER_BLOCK
    assert result["execution_state"] == "BLOCKED"
    assert "ARBITER_DEGRADATION_BLOCKED" in result["reason_codes"]
    assert result["degradation_decision"]["reason_code"] == (
        "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED"
    )
