from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.obs_routes import router, set_ledger_provider
from host_brain.audit.sqlite_ledger import SQLiteLedger


@pytest.fixture
def ledger():
    db = SQLiteLedger(":memory:", check_same_thread=False)
    yield db
    db.close()


@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(router)
    set_ledger_provider(lambda: ledger)
    return TestClient(app)


def _log_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    event_type: str,
    ts_ms: int,
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_diag_001",
    status: str | None = None,
    input_preview: str | None = None,
    reply_preview: str | None = None,
    reason_code: str | None = None,
) -> None:
    payload = {"source": source}
    if input_preview is not None:
        payload["input_preview"] = input_preview
    if reply_preview is not None:
        payload["reply_preview"] = reply_preview

    normalized_status = str(status or ("OK" if "COMPLETED" in event_type else event_type.replace("INPUT_", ""))).upper()
    ledger.log_voice_event(
        {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": event_type,
            "direction": "EDGE_TO_HOST",
            "status": normalized_status,
            "stage": f"input_dispatch.{event_type.lower()}",
            "payload": payload,
            "detail": event_type,
            "reason_code": reason_code,
            "ts_ms": ts_ms,
        }
    )


def test_input_humanoid_loop_diagnostics_returns_root_cause_summary(client, ledger):
    now = int(time.time() * 1000)

    # Baseline healthy bucket
    _log_input_event(ledger, request_id="req-diag-base-1", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-diag-base-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 35 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-diag-base-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 35 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-diag-base-1", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 300)
    _log_input_event(ledger, request_id="req-diag-base-2", event_type="INPUT_ACCEPTED", ts_ms=now - 25 * 60 * 1000)
    _log_input_event(ledger, request_id="req-diag-base-2", event_type="INPUT_ACK_PENDING", ts_ms=now - 25 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-diag-base-2", event_type="INPUT_COMPLETED", ts_ms=now - 25 * 60 * 1000 + 300)

    # Latest bucket: failed request
    _log_input_event(
        ledger,
        request_id="req-diag-fail",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000,
        input_preview="帮我生成今天的日报",
    )
    _log_input_event(
        ledger,
        request_id="req-diag-fail",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 200,
        reason_code="SKILL_TIMEOUT",
    )
    ledger.log_error(
        request_id="req-diag-fail",
        stage="S_EXEC",
        reason_code="SKILL_TIMEOUT",
        detail="tool execution timed out",
    )

    # Latest bucket: completed but no ACK / progress touch
    _log_input_event(
        ledger,
        request_id="req-diag-no-touch",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000 + 800,
        input_preview="发封邮件告诉我今天的安排",
    )
    _log_input_event(
        ledger,
        request_id="req-diag-no-touch",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 5 * 60 * 1000 + 1200,
        reply_preview="今天的安排已经整理好了",
    )

    # Latest bucket: deviation detected but not recovered
    _log_input_event(
        ledger,
        request_id="req-diag-deviation",
        event_type="INPUT_ACCEPTED",
        ts_ms=now - 5 * 60 * 1000 + 1600,
        input_preview="帮我整理一下待办",
    )
    _log_input_event(
        ledger,
        request_id="req-diag-deviation",
        event_type="INPUT_ACK_PENDING",
        ts_ms=now - 5 * 60 * 1000 + 1700,
    )
    _log_input_event(
        ledger,
        request_id="req-diag-deviation",
        event_type="INPUT_DEVIATION_DETECTED",
        ts_ms=now - 5 * 60 * 1000 + 1800,
    )
    _log_input_event(
        ledger,
        request_id="req-diag-deviation",
        event_type="INPUT_COMPLETED",
        ts_ms=now - 5 * 60 * 1000 + 2200,
        reply_preview="我先整理出一个候选待办清单",
    )

    resp = client.get("/obs/input/humanoid-loop-diagnostics?window_minutes=60&interval_minutes=10")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] in ("critical", "degraded")
    assert data["diagnosed_count"] == 3
    assert len(data["diagnostics"]) == 3

    by_request = {item["request_id"]: item for item in data["diagnostics"]}

    failed_item = by_request["req-diag-fail"]
    assert failed_item["root_cause_code"] == "EXECUTION_FAILED"
    assert failed_item["suspected_breakpoint"] == "execution_stage"
    assert failed_item["recommended_view_path"] == "/trace/req-diag-fail"
    assert "SKILL_TIMEOUT" in failed_item["evidence_codes"]

    no_touch_item = by_request["req-diag-no-touch"]
    assert no_touch_item["root_cause_code"] == "COMPLETED_WITHOUT_HUMANOID_TOUCH"
    assert no_touch_item["input_text"] == "发封邮件告诉我今天的安排"
    assert no_touch_item["output_text"] == "今天的安排已经整理好了"
    assert "ACK_TOUCH_LOW" in no_touch_item["evidence_codes"]

    deviation_item = by_request["req-diag-deviation"]
    assert deviation_item["root_cause_code"] == "DEVIATION_NOT_RECOVERED"
    assert deviation_item["suspected_breakpoint"] == "deviation_recovery"
    assert "DEVIATION_RECOVERY_LOW" in deviation_item["evidence_codes"]
