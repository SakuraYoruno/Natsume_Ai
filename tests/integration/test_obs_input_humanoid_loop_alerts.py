from __future__ import annotations

import time

import pytest
from fastapi import FastAPI

from host_brain.app.obs_routes import router
from host_brain.audit.sqlite_ledger import SQLiteLedger
from tests.fakes.obs_projection_runtime import LedgerStatsRefreshingTestClient, compose_durable_obs_projection_runtime


@pytest.fixture
def ledger(tmp_path, request):
    composition = compose_durable_obs_projection_runtime(tmp_path, request_id="obs-input-alerts")
    runtime = composition.__enter__()
    request.addfinalizer(lambda: composition.__exit__(None, None, None))
    yield runtime.ledger


@pytest.fixture
def client(ledger):
    app = FastAPI()
    app.include_router(router)
    return LedgerStatsRefreshingTestClient(app)


def _log_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    event_type: str,
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_alerts_001",
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


def test_input_humanoid_loop_alerts_detects_critical_degradation(client, ledger):
    now = int(time.time() * 1000)

    # baseline bucket: good closed loop
    _log_input_event(ledger, request_id="req-alert-base-1", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-alert-base-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 35 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-alert-base-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 35 * 60 * 1000 + 400)
    _log_input_event(ledger, request_id="req-alert-base-1", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 600)
    _log_input_event(ledger, request_id="req-alert-base-2", event_type="INPUT_ACCEPTED", ts_ms=now - 25 * 60 * 1000)
    _log_input_event(ledger, request_id="req-alert-base-2", event_type="INPUT_ACK_PENDING", ts_ms=now - 25 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-alert-base-2", event_type="INPUT_COMPLETED", ts_ms=now - 25 * 60 * 1000 + 600)

    # latest bucket: degraded and failed
    _log_input_event(ledger, request_id="req-alert-new-1", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000)
    _log_input_event(ledger, request_id="req-alert-new-1", event_type="INPUT_COMPLETED", ts_ms=now - 5 * 60 * 1000 + 600)
    _log_input_event(ledger, request_id="req-alert-new-2", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000 + 1000)
    _log_input_event(
        ledger,
        request_id="req-alert-new-2",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 1600,
    )
    _log_input_event(ledger, request_id="req-alert-new-3", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000 + 2200)
    _log_input_event(ledger, request_id="req-alert-new-3", event_type="INPUT_COMPLETED", ts_ms=now - 5 * 60 * 1000 + 2800)

    # non-gateway should be ignored
    _log_input_event(
        ledger,
        request_id="req-alert-asr-1",
        event_type="INPUT_ACCEPTED",
        source="ASR",
        session_id="asr_alert_session",
        ts_ms=now - 5 * 60 * 1000,
    )

    resp = client.get("/obs/input/humanoid-loop-alerts?window_minutes=60&interval_minutes=10")
    assert resp.status_code == 200
    data = resp.json()

    assert data["sample_requests"] >= 3
    assert data["status"] in ("critical", "degraded")
    assert data["latest_failed_rate"] is not None
    assert data["latest_failed_rate"] >= 20.0
    assert data["latest_closed_loop_rate"] is not None
    assert data["latest_closed_loop_rate"] < 70.0

    codes = {item["code"] for item in data["alerts"]}
    assert "CLOSED_LOOP_LOW" in codes
    assert "FAILED_RATE_HIGH" in codes
