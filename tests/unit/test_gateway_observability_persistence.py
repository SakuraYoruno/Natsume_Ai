from __future__ import annotations

import time

from host_brain.app.handlers.gateway_orchestrator_handler import _build_gateway_llm_summary
from host_brain.app.observability_persist import persist_chat_observability_rows
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_gateway_llm_summary_persists_graph_trace_and_llm_node_rows() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    request_id = "req::gateway::obs::001"
    session_id = "sess::gateway::obs"
    llm_summary = _build_gateway_llm_summary(
        node_id="chat_direct_ja",
        provider_name="host_ollama",
        model_id="qwen3:14b",
        llm_runtime={
            "route_target": "host_14b",
            "route_reason_code": "HOST_14B_LOCAL",
        },
        resolved_message="在吗，蓝",
        narrative="在的，我在这。",
        retrieval_summary={"status": "SUCCESS"},
        prompt_context_validation={"context_window_ok": True},
        prompt_context_observability={"total_tokens": 422},
        emotion_snapshot={"emotion_tag": "NEUTRAL"},
        response_intelligence_meta={"reflection": {"status": "SKIPPED"}},
        llm_total_latency_ms=6750,
        first_token_latency_ms=418,
    )

    try:
        persist_chat_observability_rows(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            llm_summary=llm_summary,
            ts_ms=int(time.time() * 1000) - 9000,
            fallback_reason_code=str(llm_summary.get("route_reason_code") or "") or None,
        )

        llm_rows = ledger.fetch_by_request_id("llm_node_call_log", request_id)
        trace_rows = ledger.fetch_by_request_id("graph_run_trace", request_id)

        assert len(llm_rows) == 1
        assert llm_rows[0]["node_id"] == "chat_direct_ja"
        assert llm_rows[0]["provider"] == "host_ollama"
        assert llm_rows[0]["model_id"] == "qwen3:14b"
        assert llm_rows[0]["status"] == "OK"
        assert llm_rows[0]["latency_ms"] == 6750

        assert len(trace_rows) == 1
        assert trace_rows[0]["node_count"] == 1
        assert trace_rows[0]["status"] == "OK"
        assert trace_rows[0]["reason_code"] == "HOST_14B_LOCAL"
        nodes = list(trace_rows[0].get("nodes_json") or [])
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "chat_direct_ja"
        assert nodes[0]["provider"] == "host_ollama"
        assert nodes[0]["model_id"] == "qwen3:14b"
        assert nodes[0]["latency_ms"] == 6750
    finally:
        ledger.close()
