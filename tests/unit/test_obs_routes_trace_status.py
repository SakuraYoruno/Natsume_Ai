from __future__ import annotations

from host_brain.app.obs_routes import TraceNode
from host_brain.app.obs_routes_core import (
    _build_trace_summary,
    _coalesce_trace_status,
    _extract_recall_summary_from_trace_nodes_raw,
    _normalize_trace_node_status,
)


def test_coalesce_trace_status_ignores_optional_tts_degraded() -> None:
    nodes = [
        TraceNode(node_id="voice_edge_1", node_type="edge", display_name="Input", status="OK", ts_ms=1),
        TraceNode(node_id="voice_tts_2", node_type="tts", display_name="TTS", status="DEGRADED", ts_ms=2),
    ]

    status = _coalesce_trace_status("OK", nodes)

    assert status == "OK"


def test_coalesce_trace_status_ignores_empty_recall_degraded() -> None:
    nodes = [
        TraceNode(node_id="recall_fusion", node_type="recall", display_name="Recall", status="DEGRADED", output_data={"candidate_count": 0}, ts_ms=1),
        TraceNode(node_id="llm_stream", node_type="llm", display_name="LLM", status="OK", ts_ms=2),
    ]

    status = _coalesce_trace_status("OK", nodes)

    assert status == "OK"


def test_trace_summary_uses_input_completed_reply_preview_when_memory_missing() -> None:
    summary = _build_trace_summary(
        request_id="req_obs_001",
        intent_rows=[],
        governor_rows=[],
        retrieval_rows=[],
        llm_rows=[],
        airlock_rows=[],
        bond_rows=[],
        memory_rows=[],
        error_rows=[],
        voice_rows=[
            {
                "event_type": "INPUT_COMPLETED",
                "status": "OK",
                "payload": {
                    "reply_preview": "这是监控应该显示的回复摘要",
                    "reply_text_len": 14,
                },
                "detail": "top_agent decision=APPROVE",
                "ts_ms": 1000,
            }
        ],
    )

    assert summary["output_text"] == "这是监控应该显示的回复摘要"


def test_trace_summary_uses_voice_input_preview_when_intent_missing() -> None:
    summary = _build_trace_summary(
        request_id="req_obs_002",
        intent_rows=[],
        governor_rows=[],
        retrieval_rows=[],
        llm_rows=[],
        airlock_rows=[],
        bond_rows=[],
        memory_rows=[],
        error_rows=[],
        voice_rows=[
            {
                "event_type": "INPUT_ACCEPTED",
                "status": "ACCEPTED",
                "payload": {"input_preview": "这是用户发来的原始消息"},
                "detail": "source=GATEWAY text_len=10",
                "ts_ms": 1000,
            }
        ],
    )

    assert summary["input_text"] == "这是用户发来的原始消息"


def test_trace_summary_falls_back_to_bond_topic_when_voice_input_is_placeholder() -> None:
    summary = _build_trace_summary(
        request_id="req_obs_003",
        intent_rows=[],
        governor_rows=[],
        retrieval_rows=[],
        llm_rows=[],
        airlock_rows=[],
        bond_rows=[
            {
                "request_id": "req_obs_003",
                "topic_id": "贪吃蛇",
                "ts_ms": 1001,
            }
        ],
        memory_rows=[],
        error_rows=[],
        voice_rows=[
            {
                "event_type": "INPUT_ACCEPTED",
                "status": "ACCEPTED",
                "payload": {"input_preview": "??????"},
                "detail": "source=GATEWAY text_len=6",
                "ts_ms": 1000,
            }
        ],
    )

    assert summary["input_text"] == "贪吃蛇"


def test_normalize_trace_node_status_maps_governor_approve_to_ok() -> None:
    assert _normalize_trace_node_status("APPROVE", node_type="governor") == "OK"


def test_normalize_trace_node_status_maps_input_dispatched_to_ok() -> None:
    assert _normalize_trace_node_status("DISPATCHED", node_type="edge") == "OK"


def test_normalize_trace_node_status_ignores_empty_recall_degraded() -> None:
    assert _normalize_trace_node_status(
        "DEGRADED",
        node_type="recall",
        reason_code=None,
        output_data={"candidate_count": 0},
    ) == "OK"


def test_extract_recall_summary_from_trace_nodes_raw_uses_recall_fusion_snapshot() -> None:
    summary = _extract_recall_summary_from_trace_nodes_raw(
        [
            {
                "node_id": "recall_fusion",
                "node_type": "recall",
                "status": "DEGRADED",
                "reason_code": None,
                "output_data": {"candidate_count": 0, "route_count": 2},
                "ts_ms": 1234,
            }
        ]
    )

    assert summary is not None
    assert summary["candidate_count"] == 0
    assert summary["route_count"] == 2
    assert summary["stages"][0]["status"] == "OK"
