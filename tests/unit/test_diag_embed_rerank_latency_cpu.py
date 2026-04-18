from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_diag_module():
    file_path = Path("D:/Natsume_Ai/tools/diag_embed_rerank_latency_cpu.py")
    spec = importlib.util.spec_from_file_location("diag_embed_rerank_latency_cpu", file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_commit_audit_metrics_prefers_direct_audit_refs() -> None:
    module = _load_diag_module()
    event = {
        "phase": "COMMIT",
        "audit_refs": {
            "latency_ms": 8123,
            "first_token_ms": 2666,
            "path": "chat_agent:direct",
            "model_id": "gemini-2.5-flash-lite",
            "stream_buffered": False,
            "timings_ms": {
                "prompt_build_ms": 11,
                "llm_runtime_resolve_ms": 22,
                "stream_after_first_token_ms": 333,
                "response_intelligence_ms": 0,
                "sanitize_egress_ms": 7,
                "precommit_log_ms": 8,
            },
            "trace_metrics": {
                "first_token_latency_ms": 9999,
                "retrieval_latency_ms": 444,
            },
        },
    }

    metrics = module._extract_commit_audit_metrics(event)
    assert metrics["server_latency_ms"] == 8123
    assert metrics["first_token_latency_ms"] == 2666
    assert metrics["retrieval_latency_ms"] == 444
    assert metrics["path"] == "chat_agent:direct"
    assert metrics["model_id"] == "gemini-2.5-flash-lite"
    assert metrics["stream_buffered"] is False
    assert metrics["prompt_build_ms"] == 11
    assert metrics["llm_runtime_resolve_ms"] == 22
    assert metrics["stream_after_first_token_ms"] == 333
    assert metrics["response_intelligence_ms"] == 0
    assert metrics["sanitize_egress_ms"] == 7
    assert metrics["precommit_log_ms"] == 8


def test_extract_commit_audit_metrics_falls_back_to_trace_metrics() -> None:
    module = _load_diag_module()
    event = {
        "phase": "COMMIT",
        "audit_refs": {
            "trace_metrics": {
                "first_token_latency_ms": 3456,
                "retrieval_latency_ms": 789,
            }
        },
    }

    metrics = module._extract_commit_audit_metrics(event)
    assert metrics["server_latency_ms"] is None
    assert metrics["first_token_latency_ms"] == 3456
    assert metrics["retrieval_latency_ms"] == 789
    assert metrics["path"] is None
    assert metrics["model_id"] is None
