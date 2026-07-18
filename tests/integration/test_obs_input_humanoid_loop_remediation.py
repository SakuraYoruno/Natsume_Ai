from __future__ import annotations

import time

import pytest
from fastapi import FastAPI

from host_brain.app.obs_routes import router
from host_brain.audit.sqlite_ledger import SQLiteLedger
from tests.fakes.obs_projection_runtime import LedgerStatsRefreshingTestClient, compose_durable_obs_projection_runtime


@pytest.fixture
def projection_runtime(tmp_path, request):
    composition = compose_durable_obs_projection_runtime(tmp_path, request_id="obs-input-remediation")
    runtime = composition.__enter__()
    request.addfinalizer(lambda: composition.__exit__(None, None, None))
    yield runtime


@pytest.fixture
def ledger(projection_runtime):
    yield projection_runtime.ledger


@pytest.fixture
def client(projection_runtime):
    app = FastAPI()
    app.include_router(router)
    return LedgerStatsRefreshingTestClient(
        app,
        projection_runtime=projection_runtime,
        obs_route_ports=projection_runtime.new_route_ports(),
    )


def _log_input_event(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    event_type: str,
    source: str = "GATEWAY",
    session_id: str = "qq_dm_obs_remediation_001",
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


def test_input_humanoid_loop_remediation_returns_structured_steps(client, ledger):
    now = int(time.time() * 1000)

    # Baseline bucket healthy
    _log_input_event(ledger, request_id="req-rem-base-1", event_type="INPUT_ACCEPTED", ts_ms=now - 35 * 60 * 1000)
    _log_input_event(ledger, request_id="req-rem-base-1", event_type="INPUT_ACK_PENDING", ts_ms=now - 35 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-rem-base-1", event_type="INPUT_PROGRESS_PUSHED", ts_ms=now - 35 * 60 * 1000 + 200)
    _log_input_event(ledger, request_id="req-rem-base-1", event_type="INPUT_COMPLETED", ts_ms=now - 35 * 60 * 1000 + 300)
    _log_input_event(ledger, request_id="req-rem-base-2", event_type="INPUT_ACCEPTED", ts_ms=now - 25 * 60 * 1000)
    _log_input_event(ledger, request_id="req-rem-base-2", event_type="INPUT_ACK_PENDING", ts_ms=now - 25 * 60 * 1000 + 100)
    _log_input_event(ledger, request_id="req-rem-base-2", event_type="INPUT_COMPLETED", ts_ms=now - 25 * 60 * 1000 + 300)

    # Latest bucket degraded: no ack touch, one failure
    _log_input_event(ledger, request_id="req-rem-new-1", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000)
    _log_input_event(ledger, request_id="req-rem-new-1", event_type="INPUT_COMPLETED", ts_ms=now - 5 * 60 * 1000 + 300)
    _log_input_event(ledger, request_id="req-rem-new-2", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000 + 600)
    _log_input_event(
        ledger,
        request_id="req-rem-new-2",
        event_type="INPUT_FAILED",
        status="FAILED",
        ts_ms=now - 5 * 60 * 1000 + 900,
    )
    _log_input_event(ledger, request_id="req-rem-new-3", event_type="INPUT_ACCEPTED", ts_ms=now - 5 * 60 * 1000 + 1200)
    _log_input_event(ledger, request_id="req-rem-new-3", event_type="INPUT_COMPLETED", ts_ms=now - 5 * 60 * 1000 + 1500)

    resp = client.get("/obs/input/humanoid-loop-remediation?window_minutes=60&interval_minutes=10")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] in ("critical", "degraded")
    assert data["primary_focus"] in ("抢修闭环", "恢复触达")
    assert data["steps"]
    assert data["representative_requests"]

    step_ids = {item["step_id"] for item in data["steps"]}
    assert "inspect_gateway_recent_health" in step_ids
    assert "open_trace_full" in step_ids
    assert "verify_ack_settings" in step_ids

    ack_step = next(item for item in data["steps"] if item["step_id"] == "verify_ack_settings")
    assert ack_step["target_path"] == "/system"
    assert "ACK_TOUCH_LOW" in ack_step["evidence_codes"]

    rep = data["representative_requests"][0]
    assert rep["target_path"].startswith("/trace/")
