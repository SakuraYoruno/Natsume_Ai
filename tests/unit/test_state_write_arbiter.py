from __future__ import annotations

from host_brain.app.memory.state_write_arbiter import (
    STATE_WRITE_DENY_REASON,
    StateScope,
    StateWriteArbiter,
)


def test_soul_state_only_persona_core_allowed_by_default() -> None:
    arbiter = StateWriteArbiter()
    allowed = arbiter.can_write(scope=StateScope.SOUL_STATE, writer_agent_id="persona_core")
    denied = arbiter.can_write(scope=StateScope.SOUL_STATE, writer_agent_id="motive_agent")
    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason_code == STATE_WRITE_DENY_REASON


def test_denied_write_does_not_mutate_scope_state() -> None:
    arbiter = StateWriteArbiter()
    result = arbiter.write(
        scope=StateScope.SOUL_STATE,
        writer_agent_id="motive_agent",
        payload={"persona_stage": "close_friend"},
        request_id="req_denied",
    )
    assert result.status == "DENIED"
    assert result.written is False
    assert result.reason_code == STATE_WRITE_DENY_REASON
    assert arbiter.get_state(StateScope.SOUL_STATE) == {}
    assert arbiter.get_state_version(StateScope.SOUL_STATE) == 0


def test_allowed_write_increments_version() -> None:
    arbiter = StateWriteArbiter()
    first = arbiter.write(
        scope=StateScope.SOUL_STATE,
        writer_agent_id="persona_core",
        payload={"persona_stage": "familiar"},
        request_id="req_1",
    )
    second = arbiter.write(
        scope=StateScope.SOUL_STATE,
        writer_agent_id="persona_core",
        payload={"bond_score": 0.42},
        request_id="req_2",
    )
    assert first.status == "OK"
    assert first.state_version == 1
    assert second.state_version == 2
    state = arbiter.get_state(StateScope.SOUL_STATE)
    assert state["persona_stage"] == "familiar"
    assert state["bond_score"] == 0.42


def test_replace_mode_overwrites_scope_state() -> None:
    arbiter = StateWriteArbiter()
    arbiter.write(
        scope=StateScope.RUNTIME_STATE,
        writer_agent_id="motive_agent",
        payload={"dominant_motive": "continuity", "reach_out_drive": 0.7},
    )
    overwritten = arbiter.write(
        scope=StateScope.RUNTIME_STATE,
        writer_agent_id="motive_agent",
        payload={"dominant_motive": "withdraw"},
        merge=False,
    )
    assert overwritten.status == "OK"
    assert arbiter.get_state(StateScope.RUNTIME_STATE) == {"dominant_motive": "withdraw"}


def test_set_scope_writers_can_expand_authorization() -> None:
    arbiter = StateWriteArbiter()
    arbiter.set_scope_writers(StateScope.SOUL_STATE, ["persona_core", "growth_agent"], replace=False)
    decision = arbiter.can_write(scope=StateScope.SOUL_STATE, writer_agent_id="growth_agent")
    assert decision.allowed is True
