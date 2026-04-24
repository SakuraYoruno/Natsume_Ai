from __future__ import annotations

import pytest

from host_brain.app.schemas.agent_envelope import (
    AGENT_ENVELOPE_SCHEMA_VERSION,
    AgentEnvelope,
)


def test_agent_envelope_defaults() -> None:
    envelope = AgentEnvelope(
        agent_id="motive_agent",
        node_id="motive_state",
        ts_ms=1775635000000,
    )
    assert envelope.status == "OK"
    assert envelope.schema_version == AGENT_ENVELOPE_SCHEMA_VERSION
    assert envelope.fallback_used is False
    assert envelope.input_refs == []
    trace = envelope.as_trace_dict()
    assert trace["agent_id"] == "motive_agent"
    assert trace["node_id"] == "motive_state"


def test_agent_envelope_confidence_range_validation() -> None:
    with pytest.raises(Exception):
        AgentEnvelope(
            agent_id="planner_agent",
            node_id="query_planner",
            confidence=1.2,
            ts_ms=1775635000000,
        )

