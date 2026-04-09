from __future__ import annotations

import pytest

from host_brain.app.schemas.agent_envelope import (
    AGENT_ENVELOPE_SCHEMA_VERSION,
    AgentEnvelope,
)
from host_brain.app.schemas.context_pack import (
    CONTEXT_PACK_SCHEMA_VERSION,
    ContextPack,
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


def test_context_pack_defaults_and_dump() -> None:
    pack = ContextPack(
        request_meta={"request_id": "req_1"},
        planner={"intent": "chat"},
    )
    assert pack.schema_version == CONTEXT_PACK_SCHEMA_VERSION
    dumped = pack.to_agent_input()
    assert dumped["request_meta"]["request_id"] == "req_1"
    assert dumped["planner"]["intent"] == "chat"
    assert dumped["trace_refs"] == []


def test_context_pack_roundtrip() -> None:
    source = {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "request_meta": {"request_id": "req_2"},
        "retrieval": {"hits": 3},
        "motive": {"dominant_motive": "continuity"},
        "response_policy": {"max_chars": 120},
        "soul_state": {"persona_id": "natsume_blue"},
        "trace_refs": ["trace:req_2"],
    }
    pack = ContextPack.model_validate(source)
    assert pack.request_meta["request_id"] == "req_2"
    assert pack.motive["dominant_motive"] == "continuity"
    assert pack.trace_refs == ["trace:req_2"]

