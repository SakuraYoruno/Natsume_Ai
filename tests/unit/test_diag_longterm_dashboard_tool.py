from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from tools.diag_longterm_dashboard import run


def _seed_dashboard_rows(db_path: Path) -> None:
    ledger = SQLiteLedger(db_path=str(db_path))
    try:
        base = int(time.time() * 1000) - 120_000
        request_id = "req::dashboard::001"
        intent_id = "intent::dashboard::001"

        for idx in range(10):
            decision = "APPROVE"
            if idx < 3:
                decision = "REJECT"
            elif idx < 5:
                decision = "DEGRADED"
            ledger.log_governor(
                {
                    "request_id": request_id,
                    "thread_id": "thread::dashboard",
                    "turn_id": f"turn::{idx}",
                    "intent_id": intent_id,
                    "decision": decision,
                    "reason_code": ("EMOTION_GATE_BLOCKED" if idx == 0 else ("R_DASHBOARD" if decision != "APPROVE" else None)),
                    "rule_trace": [],
                    "tokens": {},
                    "snapshot_refs": [],
                    "stage": "S_GOVERNOR",
                    "ts_ms": base + idx,
                }
            )

        for idx in range(10):
            status = "SUCCESS"
            reason_code = None
            detail = "ok"
            if idx < 3:
                status = "FAILED"
                reason_code = "RETRIEVAL_TIMEOUT"
                detail = "timeout"
            elif idx < 6:
                status = "DEGRADED"
                reason_code = "RETRIEVAL_PARTIAL"
                detail = "degraded"
            ledger.log_retrieval_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "op_kind": "RECALL",
                    "status": status,
                    "reason_code": reason_code,
                    "stage": "S_RETRIEVAL",
                    "payload": {"latency_ms": 500 + (idx * 100), "degraded": status == "DEGRADED"},
                    "detail": detail,
                    "ts_ms": base + 100 + idx,
                }
            )

        for idx in range(8):
            provider = "gemini"
            model_id = "gemini-1.5-pro"
            if idx == 6:
                provider = "edge.fallback_llm"
                model_id = "edge-3b"
            elif idx == 7:
                provider = "host.local"
                model_id = "qwen3:14b"
            ledger.log_model_attempt(
                {
                    "attempt_id": f"attempt-dashboard-{idx}",
                    "request_id": request_id,
                    "route_decision_id": f"route-dashboard-{idx}",
                    "attempt_index": 1,
                    "provider": provider,
                    "model_id": model_id,
                    "status": "succeeded",
                    "retry_reason": "initial",
                    "adapter_id": "dashboard-test",
                    "prompt_id": "dashboard-test",
                    "prompt_version": "1",
                    "prompt_digest": "0" * 64,
                    "started_at_ms": base + 190 + idx,
                    "ended_at_ms": base + 200 + idx,
                    "latency_ms": 1000 + (idx * 200),
                    "output_digest": None,
                    "metadata": {"model_node_id": f"node::{idx}"},
                }
            )

        for idx in range(5):
            ledger.log_graph_run_trace(
                {
                    "request_id": f"{request_id}::graph::{idx}",
                    "session_id": "session::dashboard",
                    "total_latency_ms": (5000 if idx < 3 else 1500),
                    "status": "OK",
                    "reason_code": None,
                    "node_count": 3,
                    "nodes_json": [],
                    "edges_json": [],
                    "ts_ms": base + 300 + idx,
                }
            )

        for idx in range(6):
            ledger.log_llm_route(
                {
                    "request_id": f"{request_id}::route::{idx}",
                    "model_id": "gemini-1.5-pro",
                    "reason": "cloud_primary",
                    "latency_ms": 1200,
                    "fallback_used": (idx == 0),
                    "ts_ms": base + 400 + idx,
                }
            )

        for idx in range(10):
            ledger.log_service_health(
                {
                    "request_id": f"{request_id}::svc::{idx}",
                    "service_id": f"svc::{idx}",
                    "owner": "host",
                    "required": 1,
                    "status": ("UP" if idx < 8 else "DOWN"),
                    "reason_code": (None if idx < 8 else "SERVICE_DOWN"),
                    "stage": "S_SERVICE_HEALTH",
                    "detail": "seed",
                    "ts_ms": base + 500 + idx,
                }
            )

        ledger.log_error(
            request_id=request_id,
            stage="S_INTERNAL_ERROR",
            reason_code="E_DASHBOARD_TEST",
            detail="seed error row",
        )
        ledger.log_qa_violation(
            request_id=request_id,
            code="QA_TEST_VIOLATION",
            detail="seed qa violation",
            field="dashboard",
        )
    finally:
        ledger.close()


def test_longterm_dashboard_generates_warn_report(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    _seed_dashboard_rows(db_path)

    output_json = tmp_path / "dashboard.json"
    output_md = tmp_path / "dashboard.md"
    report = run(
        [
            "--ledger-db",
            str(db_path),
            "--days",
            "30",
            "--threshold-governor-approve-rate-min-pct",
            "90",
            "--threshold-retrieval-failure-rate-max-pct",
            "10",
            "--threshold-graph-p95-latency-max-ms",
            "3000",
            "--threshold-cloud-call-ratio-max-pct",
            "50",
            "--threshold-required-up-rate-min-pct",
            "95",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert report["summary"]["status"] == "WARN"
    assert int(report["summary"]["alert_count"]) >= 4
    assert output_json.exists()
    assert output_md.exists()
    payload = json.loads(output_json.read_text(encoding="utf-8-sig"))
    assert payload["version"] == "longterm_metric_dashboard.v1"
    assert "quality" in payload
    assert "latency" in payload
    assert "cost" in payload
    assert "availability" in payload


def test_longterm_dashboard_empty_ledger_is_ok(tmp_path: Path) -> None:
    db_path = tmp_path / "audit_empty.sqlite3"
    SQLiteLedger(db_path=str(db_path)).close()

    report = run(
        [
            "--ledger-db",
            str(db_path),
            "--days",
            "7",
            "--output-json",
            str(tmp_path / "empty_dashboard.json"),
            "--output-md",
            str(tmp_path / "empty_dashboard.md"),
        ]
    )

    assert report["summary"]["status"] == "OK"
    assert report["summary"]["alert_count"] == 0
