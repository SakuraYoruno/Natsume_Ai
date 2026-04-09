from __future__ import annotations

from host_brain.app.top_agent import _build_trace_stage_node


def test_build_trace_stage_node_includes_standard_agent_envelope() -> None:
    node = _build_trace_stage_node(
        node_id="agentic_rag",
        node_type="retrieval",
        display_name="Agentic RAG",
        ts_ms=123,
        status="DEGRADED",
        request_id="req-env-001",
        reason_code="FALLBACK_EMBEDDING_USED",
        output_data={"candidate_count": 0, "fallback_embedding_used": True},
    )

    envelope = dict(node.get("agent_envelope") or {})
    assert envelope["request_id"] == "req-env-001"
    assert envelope["node_id"] == "agentic_rag"
    assert envelope["agent_family"] == "retrieval"
    assert envelope["status"] == "DEGRADED"
    assert envelope["reason_code"] == "FALLBACK_EMBEDDING_USED"
    assert envelope["fallback_used"] is True
    assert envelope["degrade_code"] == "FALLBACK_EMBEDDING_USED"
    assert envelope["schema_version"] == "1.0"


def test_build_trace_stage_node_envelope_keeps_parent_and_digest() -> None:
    node = _build_trace_stage_node(
        node_id="response_policy",
        node_type="system",
        display_name="Response Policy",
        ts_ms=456,
        status="OK",
        request_id="req-env-002",
        parent_node_id="motive_state",
        output_data={"allow_followup": False, "max_sentences": 2},
    )

    envelope = dict(node.get("agent_envelope") or {})
    assert envelope["request_id"] == "req-env-002"
    assert envelope["node_id"] == "response_policy"
    assert envelope["parent_node_id"] == "motive_state"
    assert envelope["status"] == "OK"
    assert envelope["confidence"] == 1.0
    assert envelope["fallback_used"] is False
    assert envelope["output_digest"]["allow_followup"] is False
    assert envelope["output_digest"]["max_sentences"] == 2
