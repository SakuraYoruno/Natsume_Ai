from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from tools.diag_stability_weekly import run


def _seed_alert_heavy_rows(db_path: Path) -> None:
    ledger = SQLiteLedger(db_path=str(db_path))
    try:
        base = int(time.time() * 1000) - 60_000
        request_id = "req::stability::001"
        intent_id = "intent::stability::001"

        for idx in range(10):
            if idx < 4:
                decision = "DEGRADED"
            elif idx < 7:
                decision = "REJECT"
            else:
                decision = "APPROVE"
            ledger.log_governor(
                {
                    "request_id": request_id,
                    "thread_id": "thread::stability",
                    "turn_id": f"turn::{idx}",
                    "intent_id": intent_id,
                    "decision": decision,
                    "reason_code": ("R_STABILITY_TEST" if decision != "APPROVE" else None),
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
                detail = "retrieval timeout"
            elif idx < 6:
                status = "DEGRADED"
                reason_code = "RETRIEVAL_PARTIAL_DEGRADED"
                detail = "retrieval degraded"
            ledger.log_retrieval_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "op_kind": "RECALL",
                    "status": status,
                    "reason_code": reason_code,
                    "stage": "S_RETRIEVAL",
                    "payload": {"latency_ms": 20 + idx},
                    "detail": detail,
                    "ts_ms": base + 100 + idx,
                }
            )
    finally:
        ledger.close()


def test_weekly_stability_report_generates_alerts_and_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    _seed_alert_heavy_rows(db_path)

    output_json = tmp_path / "weekly_stability.json"
    output_md = tmp_path / "weekly_stability.md"
    report = run(
        [
            "--ledger-db",
            str(db_path),
            "--days",
            "7",
            "--threshold-timeout-rate-pct",
            "20",
            "--threshold-degraded-rate-pct",
            "20",
            "--threshold-failure-rate-pct",
            "20",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert report["summary"]["status"] == "WARN"
    assert int(report["summary"]["alert_count"]) >= 5
    assert output_json.exists()
    assert output_md.exists()

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["version"] == "stability_weekly.v1"
    assert payload["governor"]["total"] == 10
    assert payload["retrieval"]["event_count"] == 10


def test_weekly_stability_report_empty_ledger_is_ok(tmp_path: Path) -> None:
    db_path = tmp_path / "audit_empty.sqlite3"
    ledger = SQLiteLedger(db_path=str(db_path))
    ledger.close()

    report = run(
        [
            "--ledger-db",
            str(db_path),
            "--days",
            "7",
            "--output-json",
            str(tmp_path / "empty.json"),
            "--output-md",
            str(tmp_path / "empty.md"),
        ]
    )

    assert report["summary"]["status"] == "OK"
    assert report["summary"]["alert_count"] == 0
    assert report["governor"]["total"] == 0
    assert report["retrieval"]["event_count"] == 0
