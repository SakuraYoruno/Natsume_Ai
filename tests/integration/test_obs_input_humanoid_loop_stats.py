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
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_001",
    status: str | None = None,
    ts_ms: int,
) -> None:
    normalized_status = str(status or ("OK" if "COMPLETED" in event_type else event_type.replace("INPUT_", ""))).upper()
    ledger.log_voice_event(
        {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": event_type,
            "direction": "EDGE_TO_HOST",
            "status": normalized_status,
            "stage": f"input_dispatch.{event_type.lower()}",
            "payload": {
                "source": source,
            },
            "detail": event_type,
            "ts_ms": ts_ms,
        }
    )


def test_input_humanoid_loop_stats_aggregates_gateway_closed_loop(client, ledger):
    now = int(time.time() * 1000)

    # req-1: ACK + progress push + completed
    _log_input_event(ledger, request_id="req-loop-1", event_type="INPUT_ACCEPTED", ts_ms=now - 10_000)
    _log_input_event(ledger, request_id="req-loop-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 9_500)
    _log_input_event(ledger, request_id="req-loop-1", event_type="INPUT_ACKED", ts_ms=now - 9_000)
    _log_input_event(ledger, request_id="req-loop-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 8_500)
    _log_input_event(ledger, request_id="req-loop-1", event_type="INPUT_COMPLETED", ts_ms=now - 8_000)

    # req-2: plain completed (no humanoid touch)
    _log_input_event(ledger, request_id="req-loop-2", event_type="INPUT_ACCEPTED", ts_ms=now - 7_000)
    _log_input_event(ledger, request_id="req-loop-2", event_type="INPUT_COMPLETED", ts_ms=now - 6_500)

    # req-3: deviation detected + recovered + completed
    _log_input_event(ledger, request_id="req-loop-3", event_type="INPUT_ACCEPTED", ts_ms=now - 5_000)
    _log_input_event(ledger, request_id="req-loop-3", event_type="INPUT_ACK_PENDING", ts_ms=now - 4_800)
    _log_input_event(ledger, request_id="req-loop-3", event_type="INPUT_DEVIATION_DETECTED", ts_ms=now - 4_600)
    _log_input_event(ledger, request_id="req-loop-3", event_type="INPUT_DEVIATION_RECOVERED", ts_ms=now - 4_400)
    _log_input_event(ledger, request_id="req-loop-3", event_type="INPUT_COMPLETED", ts_ms=now - 4_200)

    # non-gateway should be ignored
    _log_input_event(
        ledger,
        request_id="req-loop-asr-1",
        event_type="INPUT_ACCEPTED",
        source="ASR",
        session_id="asr_sess_001",
        ts_ms=now - 3_000,
    )

    resp = client.get("/obs/input/humanoid-loop-stats?window_minutes=60")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_gateway_requests"] == 3
    assert data["completed_requests"] == 3
    assert data["failed_requests"] == 0
    assert data["ack_touched_requests"] == 2
    assert data["ack_confirmed_requests"] == 1
    assert data["progress_touched_requests"] == 1
    assert data["deviation_detected_requests"] == 1
    assert data["deviation_recovered_requests"] == 1
    assert data["closed_loop_requests"] == 2
    assert data["completion_rate"] == 100.0
    assert data["ack_touch_rate"] == 66.7
    assert data["progress_touch_rate"] == 33.3
    assert data["deviation_recovery_rate"] == 100.0
    assert data["closed_loop_rate"] == 66.7

    stage_counts = {item["stage"]: item["count"] for item in data["stage_counts"]}
    assert stage_counts["INPUT_ACCEPTED"] == 3
    assert stage_counts["INPUT_COMPLETED"] == 3
    assert stage_counts["INPUT_ACK_PENDING"] == 2
